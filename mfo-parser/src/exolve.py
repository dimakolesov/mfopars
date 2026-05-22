"""
МТС Exolve — приём входящих звонков через webhook.

Архитектура:
  Exolve (cloud-телефония) ──── POST ────►  cloudflared/ngrok tunnel
                                                       │
                                                       ▼
                                            FastAPI listener (:8000)
                                                       │
                                                       ▼
                                                 Google Sheets
                                                 event_type=call

REST API exolve в нашем setup'е недоступен (у service-account нет прав на
Statistics/Numbering/SMS/Voice, кроме Finance/GetBalance). Поэтому
используем встроенную фичу Exolve «Переадресация вхд. вызовов на URL» —
Exolve POST'ит JSON с метаданными звонка на наш публичный endpoint.

Точная схема payload Exolve не была документирована мной заранее, поэтому
парсер defensive: пробует разные имена полей и логирует raw в Sheets.context
для дебага. После первого реального звонка можно будет ужать список полей
под фактический payload Exolve.

Эндпоинт для webhook:
  POST /exolve/incoming-call
  Content-Type: application/json
  Body: payload от Exolve (структура зависит от их формата)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger


# Имена полей, в которых разные CPaaS-провайдеры передают идентификатор
# звонящего, длительность и т.п. Берём первое непустое из списка.
CALLER_FIELDS = (
    "caller", "from", "src", "source", "source_number", "a_number",
    "caller_id", "callerId", "from_number", "originator", "msisdn",
)
CALLED_FIELDS = (
    "called", "to", "dst", "destination", "destination_number",
    "b_number", "to_number", "phone",
)
STATUS_FIELDS = (
    "status", "state", "call_status", "result", "call_result",
    "hangup_cause", "ISDNDescr",
)
DURATION_FIELDS = (
    "duration", "duration_sec", "duration_ms", "billsec", "BillSec",
    "call_duration", "talk_time",
)
TIMESTAMP_FIELDS = (
    "timestamp", "time", "start_time", "started_at", "created_at",
    "datetime", "date", "GMT",
)
CALL_ID_FIELDS = (
    "call_id", "callId", "id", "uuid", "session_id", "leg_id",
)


def _pick(payload: dict, fields: tuple[str, ...]) -> str:
    """Берёт первое непустое значение из заданных имён полей. Ищет также в
    верхнеуровневом payload, в payload.get('event'), и payload.get('call')."""
    sources = [payload]
    for sub in ("event", "call", "data", "payload", "params"):
        v = payload.get(sub)
        if isinstance(v, dict):
            sources.append(v)
    for src in sources:
        for f in fields:
            v = src.get(f)
            if v not in (None, "", False, [], {}):
                return str(v)
    return ""


def parse_exolve_webhook(payload: dict) -> dict:
    """Достаёт стандартизированную метадату звонка из payload вебхука."""
    return {
        "call_id": _pick(payload, CALL_ID_FIELDS),
        "caller": _pick(payload, CALLER_FIELDS),
        "called": _pick(payload, CALLED_FIELDS),
        "status": _pick(payload, STATUS_FIELDS),
        "duration_sec": _pick(payload, DURATION_FIELDS),
        "timestamp": _pick(payload, TIMESTAMP_FIELDS) or datetime.utcnow().isoformat() + "Z",
        "raw_keys": sorted(list(payload.keys())),
    }


def log_call_event(sheets, payload: dict, context_extra: str = "") -> dict:
    """
    Извлекает данные звонка из вебхук-payload и пишет в Google Sheets
    через переданный SheetsLogger. Возвращает извлечённые поля для
    отладки.
    """
    parsed = parse_exolve_webhook(payload)
    logger.info(
        f"[EXOLVE webhook] call_id={parsed['call_id']!r} "
        f"caller={parsed['caller']!r} called={parsed['called']!r} "
        f"status={parsed['status']!r} duration={parsed['duration_sec']!r}"
    )
    context_parts = [f"call_id={parsed['call_id']}"]
    if parsed["called"]:
        context_parts.append(f"called={parsed['called']}")
    if parsed["status"]:
        context_parts.append(f"status={parsed['status']}")
    if context_extra:
        context_parts.append(context_extra)
    context_parts.append(f"raw_keys={parsed['raw_keys']}")
    sheets.log_call(
        caller_msisdn=parsed["caller"],
        result_desc=parsed["status"] or "incoming",
        provider_date=parsed["timestamp"],
        duration_sec=parsed["duration_sec"] or "",
        context=" | ".join(context_parts),
    )
    return parsed

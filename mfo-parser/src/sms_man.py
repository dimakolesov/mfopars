"""
Клиент sms-man.com.

API base: https://api.sms-man.com/control/
Авторизация: ?token=... в query string
Формат ответа: JSON.
  Успех:  {"success": true, ...payload}
  Ошибка: {"success": false, "error_code": "...", "error_msg": "..."}

Реально проверенные эндпоинты (на api.sms-man.com):
  /control/get-balance         — баланс
  /control/countries           — список стран (id, title, ru/en/...)
  /control/applications        — список приложений-сервисов (id, title)
  /control/limits              — лимиты по выбранному country+application
  /control/get-number          — взять одноразовый номер (request_id, number)
  /control/get-sms             — получить SMS по request_id
  /control/set-status          — статус активации (ready/used/cancel)

⚠ Rent API (/rent/) на api.sms-man.com отсутствует (404). У sms-man только
одноразовые активации. Для одной заявки на МФО этого хватает: подаём,
ловим SMS-код подтверждения (МФО шлёт в первые 30-60 сек), при необходимости
вызываем set-status=ready чтобы запросить следующее SMS.

⚠ application_id для "Other" нужно подобрать на свой страх и риск — у sms-man
каждое приложение имеет свой id. Список — через /control/applications.
Дефолт в коде — 95 (часто это "Other / Любой сайт"), можно переопределить
через .env (SMS_MAN_APPLICATION_ID).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import httpx
from loguru import logger


class SmsManError(RuntimeError):
    pass


@dataclass
class ActivatedNumber:
    request_id: str
    number: str  # формат "79XXXXXXXXX" (без плюса)

    @property
    def number_plus(self) -> str:
        return "+" + self.number if not self.number.startswith("+") else self.number

    @property
    def number_local(self) -> str:
        """10-значный российский формат '9XXXXXXXXX'."""
        digits = "".join(c for c in self.number if c.isdigit())
        return digits[-10:]


class SmsManClient:
    BASE = "https://api.sms-man.com/control"

    # Статусы set-status у sms-man (строковые):
    STATUS_READY = "ready"    # подтвердить готовность принимать ещё SMS
    STATUS_USED = "used"      # завершить активацию успехом
    STATUS_CANCEL = "cancel"  # отменить активацию (возврат средств)

    def __init__(self, token: str, timeout: float = 30.0):
        self.token = token
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SmsManClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, path: str, **params) -> dict:
        params["token"] = self.token
        url = f"{self.BASE}/{path}"
        r = self._client.get(url, params=params)
        r.raise_for_status()
        try:
            data = r.json()
        except Exception:
            raise SmsManError(f"Bad JSON from sms-man: {r.text[:200]}")
        if isinstance(data, dict) and data.get("success") is False:
            raise SmsManError(f"{data.get('error_code')}: {data.get('error_msg')}")
        return data

    # ---------- профиль ----------

    def get_balance(self) -> float:
        data = self._get("get-balance")
        try:
            return float(data.get("balance", 0))
        except (TypeError, ValueError):
            return 0.0

    def get_countries(self) -> list[dict]:
        data = self._get("countries")
        # обычно отдаётся либо list, либо {"countries": [...]}
        if isinstance(data, list):
            return data
        return data.get("countries") or []

    def get_applications(self) -> list[dict]:
        data = self._get("applications")
        if isinstance(data, list):
            return data
        return data.get("applications") or []

    def get_limits(self, country_id: int, application_id: int) -> dict:
        return self._get("limits", country_id=country_id, application_id=application_id)

    # ---------- активация ----------

    def get_number(self, country_id: int = 7, application_id: int = 95) -> ActivatedNumber:
        """
        Берёт одноразовый номер. По умолчанию РФ (country_id=7),
        приложение «Other / Любой сайт» (application_id=95).
        ВНИМАНИЕ: реальные id уточняй через /control/applications.
        """
        data = self._get("get-number", country_id=country_id, application_id=application_id)
        # Ответ: {"request_id": "12345", "number": "79991234567"}
        if "number" not in data or "request_id" not in data:
            raise SmsManError(f"Не удалось взять номер: {data}")
        return ActivatedNumber(request_id=str(data["request_id"]), number=str(data["number"]))

    # Имена полей, в которых разные SMS-провайдеры/sms-man отдают
    # SMPP source_addr (идентификатор отправителя на принимающей стороне).
    # Для МО-сообщений (Mobile Originated) это обычно реальный MSISDN
    # отправителя; для трафика от ESME/CMS-шлюзов — короткий код или
    # alphanumeric sender (если оператор пропускает alpha-name к ESME).
    _SOURCE_ADDR_FIELDS = (
        "source_addr", "sourceAddr", "sender", "from", "phone_from",
        "from_phone", "msisdn", "src", "originator",
    )
    _TEXT_FIELDS = ("sms_code", "text", "sms", "body", "message")

    def get_sms(self, request_id: str) -> dict | None:
        """
        Опрашивает /control/get-sms. Возвращает None, если SMS ещё не пришло.

        При получении SMS возвращает dict:
          {
            "text": <тело сообщения>,
            "source_addr": <идентификатор отправителя из API: MSISDN, короткий
                           код или alphanumeric — что отдал шлюз>,
            "raw": <полный JSON-ответ от sms-man, на случай, если у них
                    появились новые поля>,
          }
        """
        try:
            data = self._get("get-sms", request_id=request_id)
        except SmsManError as e:
            # 'wait_sms' / 'not_received' = SMS ещё не пришло, это норма
            if "wait_sms" in str(e) or "not_received" in str(e).lower():
                return None
            raise
        text = None
        for k in self._TEXT_FIELDS:
            v = data.get(k)
            if v not in (None, "", False):
                text = str(v)
                break
        if not text:
            return None
        source_addr = ""
        for k in self._SOURCE_ADDR_FIELDS:
            v = data.get(k)
            if v not in (None, "", False):
                source_addr = str(v)
                break
        return {"text": text, "source_addr": source_addr, "raw": data}

    def set_status(self, request_id: str, status: str) -> dict:
        """status: ready / used / cancel"""
        return self._get("set-status", request_id=request_id, status=status)


def iter_new_sms(
    client: SmsManClient,
    activated: ActivatedNumber,
    poll_every: float = 5.0,
    timeout: int = 600,
) -> Iterable[dict]:
    """
    Итеративно дёргает get-sms. Когда SMS приходит:
      - yield-ит {text, source_addr, raw};
      - вызывает set-status=ready, чтобы запросить следующее SMS (МФО шлёт 1-3);
    Прекращает по таймауту, в конце выполняет set-status=used.
    """
    seen_texts: set[str] = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sms = client.get_sms(activated.request_id)
        except SmsManError as e:
            logger.error(f"sms-man error: {e}")
            time.sleep(poll_every)
            continue
        if sms and sms["text"] not in seen_texts:
            seen_texts.add(sms["text"])
            logger.info(f"⇣ SMS source_addr={sms.get('source_addr','')!r} "
                        f"text={sms['text']!r}")
            yield sms
            try:
                client.set_status(activated.request_id, SmsManClient.STATUS_READY)
            except SmsManError as e:
                logger.debug(f"set-status=ready ошибка (можно игнорировать): {e}")
        time.sleep(poll_every)
    try:
        client.set_status(activated.request_id, SmsManClient.STATUS_USED)
    except SmsManError:
        pass

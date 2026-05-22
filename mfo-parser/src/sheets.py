"""
Логгер в Google Sheets через сервис-аккаунт.

Один общий лист с колонками:
| timestamp_received | event_type | source_addr | text_or_desc | provider_date | duration_sec | links_in_sms | final_redirect_urls | context |

event_type: 'sms' | 'call' | 'leads_tech'

source_addr — идентификатор отправителя/звонящего на стороне приёма:
  * для sms: SMPP source_addr (обычно MSISDN отправителя; короткий код или
    alpha-name если оператор пропускает их к ESME);
  * для call: CLI / Caller MSISDN из payload Exolve webhook.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "timestamp_received",
    "event_type",
    "source_addr",
    "text_or_desc",
    "provider_date",
    "duration_sec",
    "links_in_sms",
    "final_redirect_urls",
    "context",
]


class SheetsLogger:
    def __init__(self, service_account_json: str, sheet_id: str, worksheet_name: str = "log"):
        creds = Credentials.from_service_account_file(service_account_json, scopes=SCOPES)
        gc = gspread.authorize(creds)
        self.sh = gc.open_by_key(sheet_id)
        try:
            self.ws = self.sh.worksheet(worksheet_name)
            logger.info(f"Используем существующий лист '{worksheet_name}'")
        except gspread.WorksheetNotFound:
            self.ws = self.sh.add_worksheet(title=worksheet_name, rows=500, cols=len(HEADERS))
            logger.info(f"Создали лист '{worksheet_name}'")
        existing = self.ws.row_values(1)
        if existing != HEADERS:
            self.ws.update("A1", [HEADERS])

    def log_sms(
        self,
        source_addr: str,
        text: str,
        provider_date: str,
        links_in_sms: list[str],
        final_redirect_urls: list[str],
        context: Optional[str] = None,
    ) -> None:
        """source_addr — SMPP source_addr из API SMS-провайдера
        (MSISDN отправителя / короткий код / alpha-name, в зависимости от
        того, что отдаёт шлюз)."""
        self._append(
            event_type="sms",
            source_addr=source_addr,
            text_or_desc=text,
            provider_date=provider_date,
            duration_sec="",
            links_in_sms=links_in_sms,
            final_redirect_urls=final_redirect_urls,
            context=context,
        )

    def log_call(
        self,
        caller_msisdn: str,
        result_desc: str,
        provider_date: str,
        duration_sec: int | str,
        context: Optional[str] = None,
    ) -> None:
        """caller_msisdn — CLI/АОН звонящего из payload Exolve webhook."""
        self._append(
            event_type="call",
            source_addr=caller_msisdn,
            text_or_desc=result_desc,
            provider_date=provider_date,
            duration_sec=duration_sec,
            links_in_sms=[],
            final_redirect_urls=[],
            context=context,
        )

    def log_leads_tech(
        self,
        source_url: str,
        final_url: str,
        title: str,
        redirect_chain: list[str],
    ) -> None:
        self._append(
            event_type="leads_tech",
            source_addr="(leads.tech submit)",
            text_or_desc=title,
            provider_date="",
            duration_sec="",
            links_in_sms=[source_url],
            final_redirect_urls=[final_url],
            context="redirect_chain=" + " ; ".join(redirect_chain[:15]),
        )

    def _append(
        self,
        event_type: str,
        source_addr: str,
        text_or_desc: str,
        provider_date: str,
        duration_sec: int | str,
        links_in_sms: list[str],
        final_redirect_urls: list[str],
        context: Optional[str],
    ) -> None:
        row = [
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
            event_type,
            source_addr or "",
            text_or_desc or "",
            provider_date or "",
            str(duration_sec) if duration_sec != "" else "",
            " | ".join(links_in_sms),
            " | ".join(final_redirect_urls),
            context or "",
        ]
        self.ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Sheets: записали {event_type} source_addr={source_addr!r}")

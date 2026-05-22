"""
End-to-end orchestrator.

Entry point обоих сценариев — CPA-ссылка t.leads.tech/click/... (после неё
парсер попадает на конкретный МФО-лендинг и заполняет форму).

Сценарий A — SMS через sms-man.com:
  1. берём активацию sms-man (RU, application)
  2. в Chromium идём t.leads.tech → редиректы → лендинг МФО → заполняем
     форму с номером sms-man
  3. параллельно polling SMS через /control/get-sms
  4. каждая SMS пишется в Sheets (event_type=sms) с source_addr (SMPP)

Сценарий B — звонок через MTS Exolve:
  Передусловие: webhook_server.py уже запущен в отдельном процессе и Exolve
  настроен слать вебхуки на https://<твой-tunnel>/exolve/incoming-call
  1. в Chromium идём t.leads.tech → редиректы → лендинг МФО → заполняем
     форму с номером Exolve (EXOLVE_INBOUND_NUMBER, +79587341964)
  2. ждём пока Exolve POST'нет вебхук на FastAPI listener (event_type=call
     пишет сам listener, не main.py)
  3. main.py просто ждёт указанный CALL_WAIT_TIMEOUT и завершается

Запуск:
  python main.py                    — оба сценария подряд + leads_tech-визит
  python main.py --only sms
  python main.py --only call
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from src.data_generator import generate_applicant
from src.mfo_form import open_via_leads_tech_and_fill
from src.redirect import extract_links, follow_all
from src.sheets import SheetsLogger
from src.sms_man import SmsManClient, SmsManError

load_dotenv()

Path("out").mkdir(exist_ok=True)
Path("out/debug").mkdir(exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO")
logger.add("out/run.log", rotation="5 MB", level="DEBUG")


def env(key: str, default: str | None = None, required: bool = True) -> str:
    v = os.getenv(key, default)
    if required and v is None:
        logger.error(f"Не задана переменная окружения {key}")
        sys.exit(2)
    return v or ""


# ---------------- Сценарий A: SMS через sms-man.com ----------------

async def scenario_sms(sheets: SheetsLogger, leads_url: str,
                       amount: int, days: int, headless: bool,
                       sms_timeout: int) -> None:
    token = env("SMS_MAN_TOKEN")
    country_id = int(env("SMS_MAN_COUNTRY_ID", "7", required=False) or "7")
    application_id = int(env("SMS_MAN_APPLICATION_ID", "95", required=False) or "95")

    applicant = generate_applicant()
    logger.info(f"[SMS] Заявитель: {applicant.last_name} {applicant.first_name} "
                f"{applicant.middle_name}, паспорт {applicant.passport_series} "
                f"{applicant.passport_number}")

    with SmsManClient(token) as client:
        bal = client.get_balance()
        logger.info(f"[SMS] Баланс sms-man: {bal} ₽")
        if bal < 5:
            logger.error("[SMS] Баланс sms-man слишком низкий")
            return
        activated = client.get_number(country_id=country_id, application_id=application_id)
        logger.info(f"[SMS] Получен номер +{activated.number} "
                    f"(request_id={activated.request_id})")

        async def fill_task():
            try:
                info = await open_via_leads_tech_and_fill(
                    leads_tech_url=leads_url,
                    applicant=applicant,
                    phone_local=activated.number_local,
                    loan_amount=amount,
                    loan_days=days,
                    headless=headless,
                )
                # лог leads_tech ивента (финальный URL + redirect chain)
                sheets.log_leads_tech(
                    source_url=leads_url,
                    final_url=info.get("final_url", ""),
                    title=info.get("title", ""),
                    redirect_chain=info.get("redirect_chain", []),
                )
                logger.info(f"[SMS] Заявка отправлена на {info.get('final_url','')!r}")
            except Exception as e:
                logger.error(f"[SMS] open_via_leads_tech_and_fill упал: {e}")

        async def poll_task():
            loop = asyncio.get_running_loop()

            def _iterate():
                import time
                seen: set[str] = set()
                deadline = time.time() + sms_timeout
                while time.time() < deadline:
                    try:
                        sms = client.get_sms(activated.request_id)
                    except SmsManError as e:
                        logger.error(f"[SMS] get-sms ошибка: {e}")
                        time.sleep(5)
                        continue
                    if sms and sms["text"] not in seen:
                        seen.add(sms["text"])
                        text = sms["text"]
                        source_addr = sms.get("source_addr") or ""
                        raw = sms.get("raw") or {}
                        logger.info(f"[SMS] ⇣ source_addr={source_addr!r} text={text!r}")
                        links = extract_links(text)
                        finals = follow_all(links) if links else []
                        sheets.log_sms(
                            source_addr=source_addr,
                            text=text,
                            provider_date="",
                            links_in_sms=links,
                            final_redirect_urls=finals,
                            context=(f"phone=+{activated.number} amount={amount} "
                                     f"days={days} request_id={activated.request_id} "
                                     f"raw_fields={list(raw.keys()) if isinstance(raw, dict) else 'n/a'}"),
                        )
                        try:
                            client.set_status(activated.request_id, SmsManClient.STATUS_READY)
                        except SmsManError:
                            pass
                    time.sleep(5)

            await loop.run_in_executor(None, _iterate)

        await asyncio.gather(fill_task(), poll_task())

        try:
            client.set_status(activated.request_id, SmsManClient.STATUS_USED)
            logger.info(f"[SMS] Активация {activated.request_id} закрыта")
        except SmsManError as e:
            logger.warning(f"[SMS] не смог закрыть активацию: {e}")


# ---------------- Сценарий B: звонок через Exolve webhook ----------------

async def scenario_call(sheets: SheetsLogger, leads_url: str,
                        amount: int, days: int, headless: bool,
                        call_timeout: int) -> None:
    inbound_number = env("EXOLVE_INBOUND_NUMBER")
    digits = "".join(ch for ch in inbound_number if ch.isdigit())
    phone_local = digits[-10:]

    applicant = generate_applicant()
    logger.info(f"[CALL] Заявитель: {applicant.last_name} {applicant.first_name} "
                f"{applicant.middle_name}")
    logger.info(f"[CALL] Exolve номер: {inbound_number} (local={phone_local})")
    logger.info(f"[CALL] Звонки логирует webhook_server.py (event_type=call).")
    logger.info(f"[CALL] Убедись что:")
    logger.info(f"[CALL]   1) webhook_server.py запущен (python webhook_server.py)")
    logger.info(f"[CALL]   2) cloudflared/ngrok туннель пробрасывает на localhost:8000")
    logger.info(f"[CALL]   3) в Exolve LK URL вебхука указан правильно")

    try:
        info = await open_via_leads_tech_and_fill(
            leads_tech_url=leads_url,
            applicant=applicant,
            phone_local=phone_local,
            loan_amount=amount,
            loan_days=days,
            headless=headless,
        )
        sheets.log_leads_tech(
            source_url=leads_url,
            final_url=info.get("final_url", ""),
            title=info.get("title", ""),
            redirect_chain=info.get("redirect_chain", []),
        )
        logger.info(f"[CALL] Заявка отправлена на {info.get('final_url','')!r}")
    except Exception as e:
        logger.error(f"[CALL] open_via_leads_tech_and_fill упал: {e}")
        return

    logger.info(f"[CALL] Жду {call_timeout} сек прозвона от МФО на {inbound_number}...")
    logger.info(f"[CALL] (звонки прилетят в Sheets через webhook_server.py)")
    await asyncio.sleep(call_timeout)
    logger.info(f"[CALL] Тайм-аут истёк, сценарий B завершён.")


# ---------------- main ----------------

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["sms", "call"], default=None,
                        help="Запустить только один сценарий")
    args = parser.parse_args()

    gsa_json = env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = env("GOOGLE_SHEET_ID")
    sheet_name = env("GOOGLE_SHEET_NAME", "log", required=False)
    leads_url = env("LEADS_TECH_URL")
    amount = int(env("LOAN_AMOUNT", "15000", required=False))
    days = int(env("LOAN_DAYS", "30", required=False))
    headless = (env("HEADLESS", "true", required=False) or "true").lower() in {"1", "true", "yes"}
    sms_timeout = int(env("SMS_WAIT_TIMEOUT", "600", required=False))
    call_timeout = int(env("CALL_WAIT_TIMEOUT", "900", required=False))

    sheets = SheetsLogger(gsa_json, sheet_id, sheet_name)

    if args.only is None or args.only == "sms":
        try:
            await scenario_sms(sheets, leads_url, amount, days, headless, sms_timeout)
        except Exception as e:
            logger.exception(f"Сценарий SMS упал: {e}")

    if args.only is None or args.only == "call":
        try:
            await scenario_call(sheets, leads_url, amount, days, headless, call_timeout)
        except Exception as e:
            logger.exception(f"Сценарий CALL упал: {e}")


if __name__ == "__main__":
    asyncio.run(main())

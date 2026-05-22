"""
FastAPI listener для МТС Exolve incoming-call webhook.

Запуск:
  python webhook_server.py
   → стартует uvicorn на 0.0.0.0:8000

Cloudflared/ngrok туннель должен указывать на этот порт. Endpoint:
  POST /exolve/incoming-call
  GET  /healthz   (для проверки что туннель живой)

В Exolve LK → Настройки приложения → Уведомления о событиях →
Переадресация вхд. вызовов на URL — вставь:
  https://<твой-туннель>/exolve/incoming-call
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from loguru import logger
import uvicorn

# подключаем наш sheets logger + парсер exolve-payload
from src.sheets import SheetsLogger
from src.exolve import log_call_event

load_dotenv()
Path("out").mkdir(exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO")
logger.add("out/webhook.log", rotation="5 MB", level="DEBUG")

GSA_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "./credentials/service_account.json")
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "log")
PORT = int(os.getenv("WEBHOOK_PORT", "8000"))

sheets = SheetsLogger(GSA_JSON, SHEET_ID, SHEET_NAME)

app = FastAPI(title="MFO Parser — Exolve webhook listener")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/exolve/incoming-call")
async def exolve_incoming_call(request: Request):
    # принимаем любой content-type, парсим как json если получится
    body_bytes = await request.body()
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        # Exolve может слать form-encoded или text/plain
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:
            payload = {"_raw": body_bytes.decode("utf-8", errors="replace")[:2000]}

    headers = {k.lower(): v for k, v in request.headers.items()}
    logger.info(f"⇣ webhook POST /exolve/incoming-call headers={dict(headers)!r}")
    logger.info(f"⇣ payload={payload!r}")

    try:
        parsed = log_call_event(
            sheets,
            payload,
            context_extra=f"x-real-ip={headers.get('x-real-ip','')}"
                          f" user-agent={headers.get('user-agent','')[:80]}",
        )
        return {"ok": True, "parsed": parsed}
    except Exception as e:
        logger.exception(f"Ошибка при логировании в Sheets: {e}")
        # всё равно отдаём 200, чтобы Exolve не ретраил без конца
        return {"ok": False, "error": str(e)}


@app.get("/")
async def root():
    return {
        "service": "MFO Parser webhook",
        "endpoints": {
            "GET /healthz": "ping",
            "POST /exolve/incoming-call": "Exolve incoming call webhook",
        },
        "expected_sheet": SHEET_ID,
    }


if __name__ == "__main__":
    logger.info(f"FastAPI listener стартует на 0.0.0.0:{PORT}")
    logger.info(f"Sheet ID: {SHEET_ID}, лист: {SHEET_NAME}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

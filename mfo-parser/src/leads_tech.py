"""
Финальный шаг ТЗ: «оставить заявку в МФО https://t.leads.tech/click/8/330/?sub1=bizdev&sub2=Name_vacancy»

Логика: это CPA-tracker (leads.tech), который при первом обращении ставит cookie,
редиректит на лендинг конкретной МФО под партнёрский поток.

Сценарий:
1. Открыть ссылку в реальном браузере (Playwright).
2. Пройти по редиректам до посадочной МФО.
3. Залогировать всю цепочку редиректов + финальный URL.
4. (Опционально) попытаться заполнить форму на лендинге теми же фейк-данными.
"""
from __future__ import annotations

import asyncio
from typing import List

from loguru import logger
from playwright.async_api import async_playwright


async def visit_and_record(url: str, headless: bool = True) -> dict:
    redirects: List[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            locale="ru-RU",
            viewport={"width": 414, "height": 896},  # mobile — у CPA часто отдельный mobile-лендинг
        )
        page = await ctx.new_page()

        def _on_response(resp):
            if 300 <= resp.status < 400:
                loc = resp.headers.get("location")
                if loc:
                    redirects.append(f"{resp.url} → {loc}")

        page.on("response", _on_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            logger.warning(f"goto завершилось с ошибкой (могло быть ок): {e}")
        final_url = page.url
        title = await page.title()
        await page.screenshot(path="out/debug/leads_tech_final.png", full_page=True)
        await browser.close()
    return {"final_url": final_url, "title": title, "redirect_chain": redirects}

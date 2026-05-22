"""
Дебаг-утилита. Открывает web-zaim.ru, ждёт прохождения Qrator,
делает скриншот и сохраняет HTML — чтобы по нему уточнить селекторы
формы, если main.py не нашёл какие-то поля.

Запуск:  python inspect_form.py
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
Path("out/inspect").mkdir(parents=True, exist_ok=True)


async def main():
    headless = os.getenv("HEADLESS", "false").lower() in {"1", "true", "yes"}
    target = os.getenv("TARGET_MFO_URL", "https://web-zaim.ru/")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            viewport={"width": 1366, "height": 900},
        )
        page = await ctx.new_page()
        print(f"→ Открываю {target} (Qrator-челлендж займёт 5-7 сек)")
        await page.goto(target, wait_until="domcontentloaded")
        await page.wait_for_timeout(7000)

        await page.screenshot(path="out/inspect/landing.png", full_page=True)
        Path("out/inspect/landing.html").write_text(await page.content(), encoding="utf-8")
        print("✓ landing.png + landing.html сохранены")

        # дамп всех form-полей с атрибутами
        fields = await page.evaluate("""
            () => Array.from(document.querySelectorAll('input, select, textarea, button'))
                .map(el => ({
                    tag: el.tagName,
                    type: el.type || null,
                    name: el.name || null,
                    id: el.id || null,
                    class: el.className || null,
                    placeholder: el.placeholder || null,
                    text: (el.innerText || '').trim().slice(0, 100) || null,
                    visible: !!(el.offsetParent),
                }))
        """)
        import json
        Path("out/inspect/fields.json").write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ Найдено {len(fields)} полей → out/inspect/fields.json")
        print("Открой fields.json и landing.png — увидишь, какие name/id есть на странице.")
        await browser.close()


asyncio.run(main())

"""
Универсальный заполнитель форм МФО.

Точка входа — t.leads.tech/click/... — CPA-tracker партнёра. После всех
редиректов попадаем на лендинг какой-то конкретной МФО (Lime/Webbankir/
Webzaim/...). Заранее неизвестно, какая именно — поэтому селекторы
defensive: пробуем несколько вариантов под каждое поле.

Если форма не нашлась с первого экрана — пытаемся ткнуть в обычные CTA
("Получить", "Оформить") и работаем дальше на втором экране.

На каждом шаге дампим скриншот+HTML в out/debug/, чтобы при поломке за
минуту понять, какой селектор изменился.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import (
    Page, BrowserContext, async_playwright,
    TimeoutError as PWTimeout,
)

from src.data_generator import FakeApplicant

DEBUG_DIR = Path("out/debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


async def _dump(page: Page, label: str) -> None:
    if os.getenv("DEBUG_DUMP", "true").lower() not in {"1", "true", "yes"}:
        return
    ts = datetime.utcnow().strftime("%H%M%S")
    try:
        await page.screenshot(path=str(DEBUG_DIR / f"{ts}_{label}.png"), full_page=True)
    except Exception as e:
        logger.warning(f"screenshot {label} failed: {e}")
    try:
        html = await page.content()
        (DEBUG_DIR / f"{ts}_{label}.html").write_text(html, encoding="utf-8")
    except Exception:
        pass


async def _try_fill(page: Page, selectors: list[str], value: str, label: str) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() == 0:
                continue
            await el.scroll_into_view_if_needed(timeout=2000)
            await el.click(timeout=2000)
            try:
                await el.fill("")
            except Exception:
                pass
            await el.type(value, delay=30)
            logger.debug(f"[{label}] заполнено через {sel}")
            return True
        except Exception as e:
            logger.debug(f"[{label}] {sel}: {e}")
            continue
    logger.warning(f"[{label}] ни один селектор не сработал")
    return False


async def _try_click(page: Page, selectors: list[str], label: str) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() == 0:
                continue
            await el.scroll_into_view_if_needed(timeout=2000)
            await el.click(timeout=3000)
            logger.debug(f"[{label}] клик через {sel}")
            return True
        except Exception as e:
            logger.debug(f"[{label}] click {sel}: {e}")
            continue
    return False


async def fill_form_on_current_page(
    page: Page,
    applicant: FakeApplicant,
    phone_local: str,
    loan_amount: int,
    loan_days: int,
) -> None:
    """Пытается заполнить максимум полей на текущем экране."""
    # сумма / срок (если есть)
    await _try_fill(page, [
        "input[name='amount']", "input[name='sum']", "input[name='summ']",
        "#amount", "input[id*='amount' i]",
        "input[type='number'][name*='sum' i]", "input[data-test*='amount' i]",
    ], str(loan_amount), "amount")

    await _try_fill(page, [
        "input[name='days']", "input[name='period']", "input[name='term']",
        "#period", "#days",
        "input[type='number'][name*='day' i]", "input[type='number'][name*='term' i]",
    ], str(loan_days), "days")

    # телефон
    await _try_fill(page, [
        "input[name='phone']", "input[type='tel']",
        "input[name*='phone' i]", "input[name*='tel' i]", "input[name*='mobile' i]",
        "input[placeholder*='телефон' i]", "input[placeholder*='+7' i]",
        "input[data-test*='phone' i]", "input[autocomplete='tel']",
    ], phone_local, "phone")

    # email
    await _try_fill(page, [
        "input[type='email']", "input[name='email']", "input[name*='email' i]",
    ], applicant.email, "email")

    # ФИО (одно поле или раздельно)
    await _try_fill(page, [
        "input[name='fio']", "input[name='fullName']", "input[name='full_name']",
        "input[name='name']",  # часто это first_name, но и fullName бывает
    ], f"{applicant.last_name} {applicant.first_name} {applicant.middle_name}", "fio")

    await _try_fill(page, [
        "input[name='lastName']", "input[name='last_name']",
        "input[name='surname']", "input[name='secondName']",
    ], applicant.last_name, "lastName")
    await _try_fill(page, [
        "input[name='firstName']", "input[name='first_name']",
    ], applicant.first_name, "firstName")
    await _try_fill(page, [
        "input[name='middleName']", "input[name='middle_name']",
        "input[name='patronymic']",
    ], applicant.middle_name, "middleName")

    # дата рождения
    await _try_fill(page, [
        "input[name='birthDate']", "input[name='birth_date']", "input[name='dob']",
        "input[name='birthday']", "input[placeholder*='рожд' i]",
    ], applicant.birth_date.strftime("%d.%m.%Y"), "birthDate")

    # паспорт
    await _try_fill(page, [
        "input[name='passportSeries']", "input[name='passport_series']",
        "input[name='series']", "input[name*='passport' i][name*='series' i]",
    ], applicant.passport_series, "passportSeries")
    await _try_fill(page, [
        "input[name='passportNumber']", "input[name='passport_number']",
        "input[name='number']", "input[name*='passport' i][name*='number' i]",
    ], applicant.passport_number, "passportNumber")
    await _try_fill(page, [
        "input[name='passportIssuedDate']", "input[name='passport_issued_date']",
        "input[name='issued_date']", "input[name='dateIssue']",
    ], applicant.passport_issued_date.strftime("%d.%m.%Y"), "passportIssuedDate")
    await _try_fill(page, [
        "input[name='passportIssuedBy']", "input[name='passport_issued_by']",
        "input[name='issued_by']", "textarea[name*='issued' i]",
        "input[name='issuedBy']",
    ], applicant.passport_issued_by, "passportIssuedBy")
    await _try_fill(page, [
        "input[name='depCode']", "input[name='department_code']",
        "input[name='dep_code']", "input[name*='code' i][name*='dep' i]",
    ], applicant.passport_dep_code, "depCode")

    # checkboxes согласий (галочки на условия) — пытаемся отметить
    for sel in [
        "input[type='checkbox'][name*='agree' i]",
        "input[type='checkbox'][name*='consent' i]",
        "input[type='checkbox'][name*='accept' i]",
        "input[type='checkbox'][name*='terms' i]",
        "input[type='checkbox'][name*='confirm' i]",
    ]:
        try:
            checks = page.locator(sel)
            n = await checks.count()
            for i in range(n):
                cb = checks.nth(i)
                if not await cb.is_checked():
                    await cb.check(timeout=1500)
                    logger.debug(f"checked {sel}[{i}]")
        except Exception:
            pass


async def click_primary_cta(page: Page) -> bool:
    return await _try_click(page, [
        "button:has-text('Получить деньги')",
        "button:has-text('Получить заём')",
        "button:has-text('Получить займ')",
        "button:has-text('Оформить заявку')",
        "button:has-text('Подать заявку')",
        "button:has-text('Отправить заявку')",
        "button:has-text('Получить SMS')",
        "button:has-text('Получить код')",
        "button:has-text('Продолжить')",
        "button:has-text('Далее')",
        "a:has-text('Получить деньги')",
        "a:has-text('Получить')",
        "button[type='submit']",
        "input[type='submit']",
    ], "cta")


async def open_via_leads_tech_and_fill(
    leads_tech_url: str,
    applicant: FakeApplicant,
    phone_local: str,
    loan_amount: int,
    loan_days: int,
    headless: bool = True,
) -> dict:
    """
    Открывает CPA-ссылку, проходит редиректы, попадает на лендинг МФО,
    заполняет форму. Возвращает {final_url, title, redirect_chain}.
    """
    redirects: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx: BrowserContext = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            locale="ru-RU",
            viewport={"width": 414, "height": 896},  # mobile (МФО мобайл-лендинги)
        )
        page = await ctx.new_page()

        def _on_response(resp):
            if 300 <= resp.status < 400:
                loc = resp.headers.get("location")
                if loc:
                    redirects.append(f"{resp.url} -> {loc}")

        page.on("response", _on_response)

        try:
            logger.info(f"Открываю CPA-ссылку: {leads_tech_url}")
            try:
                await page.goto(leads_tech_url, wait_until="networkidle", timeout=60000)
            except PWTimeout:
                logger.warning("networkidle не наступил, продолжаю по domcontentloaded")
                await page.wait_for_timeout(3000)

            final_url = page.url
            title = await page.title()
            logger.info(f"Финальный URL после редиректов: {final_url} | title={title}")
            await _dump(page, "01_after_redirects")

            # Заполняем форму на текущем экране
            await fill_form_on_current_page(
                page, applicant, phone_local, loan_amount, loan_days
            )
            await _dump(page, "02_after_fill_screen1")

            # Жмём primary CTA
            if await click_primary_cta(page):
                await page.wait_for_timeout(4000)
                await _dump(page, "03_after_cta1")
                # на втором экране попробуем дозаполнить ещё (часто там анкета)
                await fill_form_on_current_page(
                    page, applicant, phone_local, loan_amount, loan_days
                )
                await _dump(page, "04_after_fill_screen2")
                if await click_primary_cta(page):
                    await page.wait_for_timeout(4000)
                    await _dump(page, "05_after_cta2")

            return {
                "final_url": final_url,
                "title": title,
                "redirect_chain": redirects,
            }
        except Exception as e:
            logger.error(f"Ошибка при работе с формой: {e}")
            await _dump(page, "99_error")
            return {
                "final_url": page.url if page else "",
                "title": "",
                "redirect_chain": redirects,
                "error": str(e),
            }
        finally:
            await page.wait_for_timeout(1500)
            await browser.close()

"""
Извлекает ссылки из текста SMS и проходит все редиректы до финального URL.
МФО и партнёрки часто шлют сокращённые ссылки (cli.co, vk.cc, lnk.bz),
которые редиректят несколько раз перед лендингом — нам нужен финал.
"""
from __future__ import annotations

import re
from typing import List
from urllib.parse import urlparse

import httpx
from loguru import logger

URL_RE = re.compile(r"https?://[^\s\)<>\"']+", re.IGNORECASE)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def extract_links(text: str) -> List[str]:
    if not text:
        return []
    found = URL_RE.findall(text)
    # уберём хвостовую пунктуацию
    cleaned = []
    for u in found:
        while u and u[-1] in ".,;:!?)]":
            u = u[:-1]
        cleaned.append(u)
    # дедуп с сохранением порядка
    seen = set()
    out = []
    for u in cleaned:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def follow_redirects(url: str, timeout: float = 15.0, max_hops: int = 12) -> str:
    """Идёт по 3xx редиректам и через meta-refresh / window.location в HTML.
    Возвращает финальный URL. На любой сбой — возвращает последний известный URL."""
    current = url
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            headers=DEFAULT_HEADERS,
            verify=True,
        ) as client:
            for hop in range(max_hops):
                try:
                    r = client.get(current)
                except Exception as e:
                    logger.warning(f"hop {hop}: {current} → ошибка запроса: {e}")
                    return current
                # 3xx — есть Location
                if 300 <= r.status_code < 400 and "location" in r.headers:
                    new_url = str(httpx.URL(r.headers["location"]))
                    if not urlparse(new_url).netloc:
                        # относительный → склеиваем
                        new_url = str(httpx.URL(current).join(r.headers["location"]))
                    logger.debug(f"hop {hop}: {current} → 3xx → {new_url}")
                    current = new_url
                    continue
                # 200 — смотрим meta-refresh и window.location
                if r.status_code == 200:
                    body = r.text[:30000]
                    # <meta http-equiv="refresh" content="0; url=...">
                    m = re.search(
                        r"""<meta[^>]+http-equiv=["']refresh["'][^>]+content=["'][^;]*;\s*url=([^"'>\s]+)""",
                        body,
                        re.IGNORECASE,
                    )
                    if m:
                        new_url = m.group(1)
                        if not urlparse(new_url).netloc:
                            new_url = str(httpx.URL(current).join(new_url))
                        logger.debug(f"hop {hop}: {current} → meta-refresh → {new_url}")
                        current = new_url
                        continue
                    # window.location.href = "..."
                    m = re.search(
                        r"""window\.location(?:\.href)?\s*=\s*["']([^"']+)""", body
                    )
                    if m:
                        new_url = m.group(1)
                        if not urlparse(new_url).netloc:
                            new_url = str(httpx.URL(current).join(new_url))
                        logger.debug(f"hop {hop}: {current} → JS-redirect → {new_url}")
                        current = new_url
                        continue
                    # достигли стабильной страницы
                    return current
                # любой другой код — возвращаем текущий
                return current
    except Exception as e:
        logger.warning(f"Ошибка обхода редиректов для {url}: {e}")
    return current


def follow_all(urls: List[str]) -> List[str]:
    return [follow_redirects(u) for u in urls]

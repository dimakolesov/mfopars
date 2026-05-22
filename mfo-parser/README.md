# MFO Parser — SMS + звонки + редиректы через CPA leads.tech

Парсер открывает CPA-ссылку партнёра (`t.leads.tech/click/...`), проходит
все редиректы до фактического лендинга МФО, заполняет форму фейк-данными,
ловит:

- **SMS-код подтверждения** — через [sms-man.com](https://sms-man.com)
  (одноразовая активация, polling `/control/get-sms`)
- **Прозвон оператора МФО** — через [МТС Exolve](https://exolve.ru) webhook
  (Exolve POST'ит метаданные звонка на наш FastAPI listener)

Всё пишется в один общий **Google Sheet** с колонкой `event_type`
(`sms` / `call` / `leads_tech`).

## Архитектура

```
┌─────────────────┐
│  sms-man.com    │ ── SMS (polling) ──┐
└─────────────────┘                    │
        │                              ▼
        │ phone=...                ┌───────────────┐
        │                          │ Google Sheets │
        ▼                          └───────────────┘
┌───────────────────┐                  ▲
│ t.leads.tech CPA  │                  │
│  ─ redirects ─    │                  │
│      ↓            │                  │
│  MFO landing      │ ─ fill form ─►   │
└───────────────────┘                  │
        ▲                              │
        │ phone=+79587341964           │
        │                              │
┌─────────────────┐                    │
│  МТС Exolve     │ ── webhook ────────┤
│  (cloudflared)  │   POST /exolve/    │
└─────────────────┘   incoming-call    │
                                       │
        (FastAPI listener) ────────────┘
```

## Структура

```
mfo-parser/
├── main.py                  # оркестратор сценариев A и B
├── webhook_server.py        # FastAPI listener для Exolve callback
├── inspect_form.py          # дебаг: дамп HTML+скриншот+полей лендинга
├── requirements.txt
├── .env.example
└── src/
    ├── data_generator.py    # фейковые ФИО/паспорт серии 99XX/СНИЛС/ИНН
    ├── sms_man.py           # клиент sms-man.com (одноразовые активации)
    ├── exolve.py            # парсер Exolve webhook + лог в Sheets
    ├── sheets.py            # логгер Google Sheets, колонка source_addr/event_type
    ├── redirect.py          # извлекаем ссылки из SMS, проходим редиректы
    ├── mfo_form.py          # универсальный заполнитель форм МФО (Playwright)
    └── leads_tech.py        # (старая утилита визита; больше не используется в main)
```

## Установка

```bash
git clone <repo>
cd mfo-parser
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env
# отредактировать .env и положить service_account.json в ./credentials/
```

## Запуск (пошагово)

### Шаг 0 — туннель к webhook_server (один раз)
```bash
cloudflared tunnel --url http://localhost:8000
# или: ngrok http 8000
```
Запоминаем публичный URL, например:
`https://tracking-efficient-removal-eggs.trycloudflare.com`

### Шаг 1 — настроить webhook в Exolve (один раз)
В личном кабинете МТС Exolve:
- Настройки приложения → Уведомления о событиях →
  **Переадресация вхд. вызовов на URL**
- Вставить: `https://<твой-tunnel>/exolve/incoming-call`

### Шаг 2 — запустить FastAPI listener (отдельный терминал)
```bash
python webhook_server.py
```
Слушает `0.0.0.0:8000`, endpoint `POST /exolve/incoming-call`,
healthcheck `GET /healthz`.

### Шаг 3 — запустить основной пайплайн (другой терминал)
```bash
python main.py                   # оба сценария подряд
python main.py --only sms        # только SMS
python main.py --only call       # только звонок
```

## Что произойдёт

### Сценарий A (`--only sms`, ~5–10 мин)
1. sms-man выдаёт RU-номер активации (~5–15 ₽).
2. Chromium открывает `t.leads.tech/click/...`, проходит все редиректы,
   попадает на МФО-лендинг.
3. Парсер заполняет форму фейк-данными, телефон — sms-man номер.
4. Жмёт CTA, при наличии второго экрана заполняет ещё.
5. Polling `/control/get-sms` каждые 5 сек, до 10 мин.
6. Каждое SMS → строка в Google Sheets (`event_type=sms`):
   - `source_addr` — SMPP source_addr из API (MSISDN отправителя / короткий код)
   - `text_or_desc` — тело SMS
   - `links_in_sms` — извлечённые URL
   - `final_redirect_urls` — финал каждого URL после всех редиректов
7. Также пишется leads_tech-строка с финальным URL и цепочкой редиректов.

### Сценарий B (`--only call`, ~5–15 мин)
1. Тот же путь через t.leads.tech до МФО-лендинга.
2. Парсер заполняет форму, телефон — `EXOLVE_INBOUND_NUMBER` (+79587341964).
3. Жмёт submit и ждёт `CALL_WAIT_TIMEOUT` (по умолчанию 15 мин).
4. Параллельно в другом терминале **webhook_server.py** ловит POST от
   Exolve при каждом входящем на твой номер.
5. Каждый вебхук → строка в Google Sheets (`event_type=call`):
   - `source_addr` — MSISDN звонящего (CLI)
   - `text_or_desc` — статус звонка
   - `duration_sec` — длительность
   - `context` — full raw_keys payload для дебага формата

## Google Sheet — структура

| timestamp_received | event_type | source_addr | text_or_desc | provider_date | duration_sec | links_in_sms | final_redirect_urls | context |
|---|---|---|---|---|---|---|---|---|
| 2026-05-21T14:33:12Z | sms | 79991234567 | Ваш код: 1234 | | | | | phone=+79991234567 request_id=12345 raw_fields=[...] |
| 2026-05-21T14:36:05Z | sms | 79257778899 | Одобрено: https://cli.co/abc | | | https://cli.co/abc | https://partner.mfo.ru/lk?utm=... | phone=... |
| 2026-05-21T14:38:11Z | call | 79587341964 | answered | 2026-05-21T... | 45 | | | call_id=... called=+79587341964 raw_keys=[...] |
| 2026-05-21T14:39:50Z | leads_tech | (leads.tech submit) | Webbankir — оформи онлайн заём | | | https://t.leads.tech/click/8/330/?... | https://webbankir.com/?cpa_id=... | redirect_chain=... |

### Что такое source_addr
- Для `sms` — SMPP source_addr из API sms-man (MSISDN отправителя для
  MO-трафика; короткий код / alphanumeric для ESME-трафика, если оператор
  пропускает alpha-name к ESME). В РФ alpha-name преимущественно для
  outgoing-рассылок, поэтому на приёме у виртуальных номеров обычно MSISDN
  или короткий код.
- Для `call` — CLI/АОН из payload Exolve (MSISDN звонящего или короткий
  код колл-центра МФО).
- Для `leads_tech` — литерал `(leads.tech submit)`.

## Если форма не нашлась

```bash
HEADLESS=false python inspect_form.py
```
Откроет реальный браузер, дампит:
- `out/inspect/landing.png`
- `out/inspect/landing.html`
- `out/inspect/fields.json` (все input/select/button + атрибуты)

Поправляй селекторы в `src/mfo_form.py:fill_form_on_current_page` и
`click_primary_cta`.

## Известные нюансы

- **sms-man + source_addr:** клиент пробует прочитать source_addr из набора
  возможных полей ответа `/control/get-sms` (`source_addr`, `sender`,
  `from`, `phone_from`, `msisdn`, `originator`, ...). Какое именно поле
  sms-man использует — пишется в колонку `context` как `raw_fields=[...]`,
  чтобы при первом прогоне сразу было видно и при необходимости поправить
  `_SOURCE_ADDR_FIELDS` в `src/sms_man.py`.

- **Exolve webhook payload:** точная схема нам неизвестна (REST API на этом
  service-account был недоступен), поэтому `src/exolve.py` парсит payload
  defensive — пробует несколько имён полей (`caller`/`from`/`src`,
  `duration`/`billsec`, ...) и сохраняет полный список ключей входящего
  JSON в `context: raw_keys=[...]`. После первого реального звонка можно
  сузить список.

- **Юридически:** скрипт использует фейковые ФИО+паспорт. Серия паспорта
  всегда `99XX` (таких ОВД не существует), данные не совпадают с реальными
  людьми. Скоринг МФО отклонит заявку на этапе БКИ. Цель — продемонстрировать
  пайплайн, не получить займ.

- **Cloudflared tunnel:** TryCloudflare URL живёт пока процесс запущен.
  Если cloudflared перезапустить — URL поменяется, надо обновить в Exolve LK.

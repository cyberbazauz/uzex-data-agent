# UZEX Data Agent

Data Agent поверх вымышленного набора сделок UZEX: отвечает на вопросы о сделках, суммах, объёмах,
контрактах, участниках, брокерах и динамике **только по данным PostgreSQL**, с обязательным evidence
для каждого ответа `FOUND`. Дополнительно поддерживает General AI Mode: свободный диалог и общие
вопросы, которые явно не являются фактами реестра.

Сервис не генерирует SQL из текста вопроса, не даёт LLM доступ к БД/файлам/инструментам и не
раскрывает API-ключи во frontend.

> **English summary.** A grounded data agent over a synthetic UZEX deal registry. The LLM only
> produces a strict JSON `QueryPlan`; every number comes from a parameterised, allowlisted SQL
> handler executed through a `SELECT`-only PostgreSQL role, and every `FOUND` answer carries
> evidence. Unsupported or ambiguous questions fail closed (`UNSUPPORTED` / `NEEDS_CLARIFICATION`),
> and infrastructure errors return `UNVERIFIED` instead of a fake `NOT_FOUND`. Stack: FastAPI,
> SQLAlchemy, Alembic, PostgreSQL, React/Vite, Docker Compose, Telegram long polling, optional Groq
> planner. Includes 719 passing tests, a 100-question evaluation set, and a verified AWS deployment.

> Данные в `uzex_spot_data.zip` **вымышленные** — это прямо указано в исходном README. Проект не
> утверждает, что набор описывает реальную биржу. Сам архив с данными в репозиторий не входит:
> положите `uzex_spot_data.zip` в корень проекта перед импортом (см. раздел 4.4).

## 0. Статус проекта

Проект реализован и проверен end-to-end, включая запуск Telegram-бота в Docker Compose на AWS EC2.
Демонстрационный инстанс был выведен из эксплуатации, поэтому публичного URL сейчас нет.
Порядок локального запуска и развёртывания — в разделах 4 и 7; отчёт о проверенном AWS-деплое —
в [`docs/v3_deployment_report.md`](docs/v3_deployment_report.md) (идентификаторы инфраструктуры
отредактированы).

> Для запуска нужен собственный `GROQ_API_KEY` (только server-side) либо `PLANNER_MODE=local`,
> который работает полностью офлайн. Секретов в репозитории нет.

---

## 1. Что умеет

Поддержанные типы вопросов:

- свободный диалог и общие вопросы (General AI Mode, требует `PLANNER_MODE=llm`);
- объяснение биржевых терминов, методов анализа и помощь с формулировкой запроса;
- количество сделок (всего, по периодам, статусам, типу контракта, товару, региону, участнику);
- суммы сделок и средние/мин/макс (UZS, USD, валюта контракта, метод конвертации README);
- физические объёмы и поставки с группировкой по единицам измерения;
- топ-N продавцов, покупателей и брокеров;
- сравнение периодов и динамика по годам;
- точный поиск сделки, контракта, участника и брокера;
- список последних/отфильтрованных сделок.

Языки ответа: **русский**, **узбекский латиница**, **узбекский кириллица**.

Публичные статусы:

| Статус | Значение |
|---|---|
| `FOUND` | Данные найдены; ответ содержит `data` и `evidence`. |
| `NOT_FOUND` | Запрос выполнен, совпадений в активном наборе нет. |
| `NEEDS_CLARIFICATION` | Сущность или условие неоднозначны; агент не угадывает. |
| `UNSUPPORTED` | Вопрос нельзя достоверно выполнить на текущей схеме. |
| `UNVERIFIED` | Ошибка/непровалидированный результат; это **не** `NOT_FOUND`. |
| `GENERAL` | Ответ из общих знаний; не проверялся по реестру, без `data`/`evidence`. |

### General AI Mode

LLM planner сам определяет режим вопроса:

- «Что такое экспорт?» → `GENERAL`, обычный ответ из общих знаний;
- «Сколько экспортных сделок было в 2024 году?» → `EXECUTE`, строгий SQL-план;
- «Почему экспортных сделок стало меньше?» → `EXECUTE` + `explanation_requested`,
  сначала подтверждённые данные из PostgreSQL, затем отдельный блок с явно
  помеченными гипотезами.

Гарантии режима:

- `GENERAL` никогда не содержит `data`/`evidence` и не выдаётся за проверенный факт;
- детерминированный `LocalPlannerAdapter` не умеет `GENERAL`;
- если LLM ошибочно классифицирует явно реестровый вопрос как `GENERAL`,
  срабатывает защита и вопрос уходит в детерминированный план данных;
- ответы с утверждением «по данным базы» или секретами отклоняются (`UNVERIFIED`);
- гипотезы о причинах всегда идут после подтверждённых фактов под отдельной
  меткой и проходят ту же проверку чисел и сущностей, что и обычный ответ.

Провайдер и квоты: режим выполняет минимум два вызова (классификация + ответ).
На бесплатном тарифе Groq действует общий лимит организации `200 000` токенов
в сутки и `8 000` в минуту. Один вызов планировщика занимает около `4 100`
токенов, поэтому одна организация выдерживает примерно 40–50 сообщений в сутки
на всех пользователей.

В production настроены две независимые организации Groq. Основная принимает
запросы первой; вторая используется **только** после длительного (суточного)
429 основной организации, а короткие RPM/TPM-лимиты по-прежнему ожидаются, а не
переключаются. Ключи второй организации хранятся отдельно
(`GROQ_FAILOVER_API_KEYS`) и никогда не смешиваются с реестром основной.
Это удваивает доступную суточную квоту, примерно до 80–100 сообщений в сутки,
но не отменяет ограничение: для production-нагрузки нужен Dev Tier или
отдельный квалифицированный провайдер.

Доменные правила планировщика:

- экспорт/внутренний рынок — это `contract_type` (`Экспортный` / `Внутренний`),
  а не `trade_type` (тип аукциона);
- допустимые значения контролируемых полей (`trade_type`, `contract_type`,
  `contract_form`, `status`, `currency`) проверяются до SQL: неизвестное
  значение даёт `UNSUPPORTED`, а не ложный `NOT_FOUND`;
- заглавная буква в начале предложения — грамматика, а не утверждение
  идентичности; сущности внутри предложения по-прежнему сверяются с
  подтверждёнными данными.

---

## 2. Архитектура

```text
React UI
  -> FastAPI /api/v1/chat
     -> language/script detection
     -> planner (local | llm)
        -> strict JSON QueryPlan (decision = EXECUTE | CLARIFY | UNSUPPORTED | GENERAL)
     -> GENERAL: free-text answer, no data/evidence, no DB access
     -> EXECUTE:
        -> Pydantic validation
        -> entity resolver (exact matches, ambiguity -> clarification)
        -> allowlist/policy validation
        -> parameterized SQL handler (13 handlers)
        -> PostgreSQL read-only role
        -> result + evidence validation
        -> deterministic RU/UZ formatter
        -> optional labelled hypothesis block (explanation_requested)
  -> response: status + answer + data + evidence + clarification
```

Ключевые каталоги:

| Путь | Назначение |
|---|---|
| `backend/app/agent/` | planner, QueryPlan, orchestrator, обработка статусов |
| `backend/app/handlers/` | allowlisted SQL handlers и dispatch |
| `backend/app/entities/` | точное разрешение сущностей |
| `backend/app/validation/` | валидация плана, результата и evidence |
| `backend/app/responses/` | детерминированный formatter |
| `backend/app/language/` | RU/UZ Latin/UZ Cyrillic словари и шаблоны |
| `backend/importer/` | staging, integrity checks, атомарное переключение датасета |
| `backend/alembic/` | миграции схемы |
| `frontend/` | React + Vite UI |
| `deploy/` | nginx и init-скрипт read-only роли |
| `docs/` | data dictionary и test report |

---

## 3. Требования

- Python **3.11+** (проверялось на 3.14);
- PostgreSQL **14+** (проверялось на 18);
- Node.js **20+** (проверялось на 22) и npm;
- для Docker-варианта — Docker + Docker Compose.

Python-зависимости: `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `psycopg2-binary`, `alembic`,
`httpx`; dev — `pytest`, `pytest-cov`.

---

## 4. Быстрый старт (локально)

### 4.1. Переменные окружения

```powershell
Copy-Item backend\.env.example backend\.env
```

Заполните как минимум:

```dotenv
DATABASE_URL=postgresql+psycopg2://uzex_owner:<owner_password>@127.0.0.1:55432/uzex
DATABASE_READONLY_URL=postgresql+psycopg2://uzex_reader:<reader_password>@127.0.0.1:55432/uzex
PLANNER_MODE=local
```

`backend/.env` читается только сервером. Не коммитьте реальные значения.

Для `PLANNER_MODE=llm` дополнительно задайте `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`. Ключ
используется только backend-процессом и никогда не передаётся браузеру.

General AI Mode управляется `GENERAL_MODE_ENABLED` (по умолчанию `true`) и
`GENERAL_ANSWER_MAX_CHARS`. Он активируется только при `PLANNER_MODE=llm`:
детерминированный локальный планировщик не может вернуть `GENERAL`.
Блок гипотез после проверенных данных управляется `EXPLANATION_MODE=llm|off`
и `EXPLANATION_MAX_CHARS`.

### 4.2. PostgreSQL

Создайте БД и две роли:

- `uzex_owner` — DDL + запись, используется импортом и миграциями;
- `uzex_reader` — только `SELECT`, используется runtime API.

Пример (под суперпользователем):

```sql
CREATE ROLE uzex_owner LOGIN PASSWORD '...';
CREATE ROLE uzex_reader LOGIN PASSWORD '...';
CREATE DATABASE uzex OWNER uzex_owner;
\c uzex
GRANT USAGE ON SCHEMA public TO uzex_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE uzex_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO uzex_reader;
ALTER ROLE uzex_reader SET default_transaction_read_only = on;
```

В проекте уже использовался изолированный кластер на `127.0.0.1:55432` с каталогом
`data/pgdata`. Запуск:

```powershell
& "D:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" `
  -D "$PWD\data\pgdata" `
  -o "-p 55432 -c listen_addresses=127.0.0.1" `
  -l "$PWD\data\pg.log" start
```

### 4.3. Миграции

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:PYTHONIOENCODING="utf-8"
Set-Location backend
python -m alembic upgrade head
```

### 4.4. Импорт данных

Архив `uzex_spot_data.zip` или распакованные CSV в `data/raw/uzex_spot_data`.

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:PYTHONIOENCODING="utf-8"
Set-Location backend
python -m importer.import_cli
```

Полезные флаги:

| Флаг | Действие |
|---|---|
| без флагов | проверить checksums, загрузить staging, выполнить проверки, атомарно переключить датасет |
| `--dry-run` | только валидация без переключения |
| `--force` | повторный импорт уже активной версии |

Импорт идемпотентен. Отчёт пишется в `data/manifests/last_import_report.json`.

### 4.5. Backend

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:PYTHONIOENCODING="utf-8"
Set-Location backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Проверка:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
Invoke-RestMethod http://127.0.0.1:8000/api/v1/meta
```

### 4.6. Frontend

```powershell
Set-Location frontend
npm install
npm run dev
```

Dev-сервер: `http://127.0.0.1:5173`. Vite проксирует `/api` на `http://127.0.0.1:8000`
(переопределяется через `VITE_PROXY_TARGET`).

Production build:

```powershell
npm run build
```

`VITE_API_BASE_URL` задаёт базу API для собранного frontend. Пустое значение означает same-origin
(используется в Docker + nginx). Секретов в `VITE_*` быть не должно.

---

## 5. API

Префикс: `/api/v1`.

| Метод | Endpoint | Назначение |
|---|---|---|
| `GET` | `/health` | liveness |
| `GET` | `/ready` | DB, dataset и schema готовы |
| `GET` | `/meta` | публичные счётчики и версия датасета |
| `GET` | `/examples` | примеры вопросов (помечены `is_example`) |
| `POST` | `/chat` | ответ на вопрос |

Пример:

```powershell
$body = @{ question = "Сколько сделок было в 2024 году?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/chat `
  -ContentType "application/json; charset=utf-8" -Body $body
```

Ответ `FOUND` содержит `answer`, `data`, `evidence`, `request_id`, `duration_ms`.
`NOT_FOUND` может содержать evidence с `matched_rows = 0`. Остальные статусы не содержат
`data`/`evidence`.

Поддерживается заголовок `X-Request-ID`; он попадает в structured logs.

---

## 6. Тесты

Из корня репозитория:

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:PYTHONIOENCODING="utf-8"
python -m pytest -q
```

Последний подтверждённый прогон V3: **719 passed, 1 skipped** (см. `docs/v3_deployment_report.md`).
Исторические прогоны и их отличия зафиксированы в `docs/test_report.md`.
Пропущен живой 100-вопросный прогон; включить:

```powershell
$env:UZEX_RUN_EVALUATION="1"
python -m pytest -q backend/tests/test_evaluation.py
```

Отдельный отчёт evaluation:

```powershell
python backend\evaluation\run_evaluation.py
```

Результат пишется в `backend/evaluation/reports/evaluation_report.json`.
Подробности и ограничения — в [`docs/test_report.md`](docs/test_report.md).

---

## 7. Docker Compose

```powershell
Copy-Item .env.example .env
# заполните POSTGRES_PASSWORD, UZEX_READER_PASSWORD, APP_SECRET
docker compose up --build
```

Сервисы:

```text
db -> migrate -> import -> api -> web
```

- `db` — PostgreSQL с init-скриптом read-only роли;
- `migrate` — `alembic upgrade head`, запускается один раз;
- `import` — идемпотентный импорт, запускается один раз;
- `api` — FastAPI, healthcheck `/api/v1/ready`;
- `web` — nginx, отдаёт UI и проксирует `/api/` на `api:8000`.

UI: `http://localhost:8080` (порт задаётся `WEB_PORT`).

> Сборка и запуск контейнеров проверены на AWS EC2 (см. `docs/v3_deployment_report.md`).
> На рабочей станции Docker может отсутствовать — тогда используйте локальный запуск из раздела 4.

### 7.1. Демонстрационный сервер без Docker

Если Docker/nginx недоступны, можно отдать production-сборку и проксировать API встроенным
скриптом:

```powershell
Set-Location frontend
npm run build
Set-Location ..
python scripts\serve_frontend.py --host 127.0.0.1 --port 8088
```

Скрипт отдаёт `frontend/dist` и проксирует `/api/` на `http://127.0.0.1:8000`. Для временного
публичного доступа:

```powershell
cloudflared tunnel --url http://127.0.0.1:8088
```

Команда печатает публичный `https://*.trycloudflare.com` URL без регистрации. Туннель — временное
решение, не замена nginx-деплою; ссылка живёт, пока запущены backend, статический сервер и
`cloudflared`.

---

## 8. Модель безопасности

- LLM получает только текст вопроса и декларативные подсказки; **не** получает SQL, файлы, БД,
  shell, Python, интернет или произвольные tools.
- LLM возвращает строгий JSON QueryPlan; любой ответ вне схемы отклоняется.
- SQL строится только из allowlisted handler'ов; значения передаются параметрами.
- SQL, вернувший LLM, никогда не исполняется.
- Пользовательский текст не конкатенируется в SQL.
- Runtime-роль PostgreSQL — только `SELECT`; запись отклоняется.
- Денежные значения — `Decimal`/`NUMERIC`, не `float`.
- `NULL` не заменяется нулём; UZS и USD не смешиваются без метода README; физические единицы
  не суммируются между собой.
- Товары связываются через `contract_no`, не через название.
- `warehouse_region` не трактуется как регион компании.
- Каждый `FOUND` содержит evidence; неоднозначность не разрешается автоматически.
- Ошибки LLM/DB дают `UNVERIFIED`, а не `NOT_FOUND`.
- Секреты читаются только из environment/`.env` backend; в frontend и репозитории их нет.
- Логи структурированные, с `request_id`, без секретов.

---

## 9. Ограничения

- Evaluation 100/100 — совпадение с заранее заданными ожиданиями, а не доказательство корректности
  любых формулировок.
- `PLANNER_MODE=llm` настроен и развёрнут в production. Живые вызовы ограничены квотой
  провайдера: Groq free tier — `200 000` токенов в сутки на организацию, около 40–50
  сообщений. Две независимые организации и cross-org failover на длительный 429 дают
  примерно 80–100 сообщений в сутки, но не снимают лимит полностью. При исчерпании
  обеих квот агент возвращает `UNVERIFIED`, а не выдуманный ответ.
- Docker-образы собраны и развёрнуты на EC2; Docker daemon на рабочей станции не запускался.
- Демонстрационный EC2-инстанс выведен из эксплуатации. Для постоянного публичного URL нужен
  сервер/Docker-хост с доменом и HTTPS.
- Локальный planner намеренно fail-closed: не описанные в словарях формулировки возвращают
  `UNSUPPORTED`, неоднозначные — `NEEDS_CLARIFICATION`.

---

## 10. Документация

- [`UZEX_AI_AGENT_ARCHITECTURE.md`](UZEX_AI_AGENT_ARCHITECTURE.md) — техническая спецификация.
- [`docs/data_dictionary.md`](docs/data_dictionary.md) — проверенная структура данных.
- [`docs/test_report.md`](docs/test_report.md) — результаты тестов, импорта, evaluation и ограничения.
- `data/raw/uzex_spot_data/README.md` — исходное описание данных (появляется после распаковки
  `uzex_spot_data.zip`; архив в репозиторий не входит).

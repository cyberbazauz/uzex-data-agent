# UZEX Data Agent — отчёт о тестировании

Дата прогона: **2026-11-05**
Окружение: Windows, Python 3.14, PostgreSQL 18 (локальный кластер `127.0.0.1:55432`), Node.js/Vite (frontend).
Версия датасета: `sha256:96424654325b6c41074b67aa3919435747f9cbc029a7fe9bf5e2dd02810e8fab`
Срез данных: **2026-10-30**.

> Отчёт фиксирует только фактически выполненные проверки. Он не является заявлением о «100% точности» системы: см. раздел «Ограничения».

---

## 1. Сводка

| Проверка | Результат |
|---|---|
| Полный `pytest` | **309 passed, 1 skipped, 0 failed** (21.35 s) |
| Пропущенный тест | живой прогон 100 вопросов; включается `UZEX_RUN_EVALUATION=1` |
| Импорт датасета | **IMPORTED**, 500 000 сделок, 97 проверок + 8 финальных проверок |
| Контрольные суммы | совпадают с независимым пересчётом |
| 100-вопросный evaluation | **100/100** по ожидаемым статусам/локалям/структуре + 11 числовым эталонам |
| Frontend production build | успешно, `dist/` собран |
| API smoke (`/health`, `/ready`, `/meta`, `/examples`, `/chat`) | успешно |
| Browser-проверки UI | успешно, ошибок консоли нет |
| Docker build/run | **не выполнялся**: Docker в окружении отсутствует |
| Публичный URL | временный Cloudflare Quick Tunnel проверен end-to-end; постоянного хостинга нет |

---

## 2. Тесты backend

Команда:

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:PYTHONIOENCODING="utf-8"
python -m pytest -q
```

Итог:

```text
309 passed, 1 skipped in 21.35s
```

Пропуск — `test_live_evaluation_set_matches_expectations`; он требует явного флага, чтобы обычный unit-прогон не зависел от поднятой БД.

### 2.1. Состав тестов

| Файл | Тестов | Что покрывает |
|---|---:|---|
| `test_local_planner.py` | 95 | язык/script, интенты, фильтры, периоды, валюты, top-N, сравнение, lookup, injection/unsupported |
| `test_plan_validator.py` | 45 | allowlist полей/операторов/интентов, запрет произвольных структур, fail-closed |
| `test_entity_resolution.py` | 40 | точное разрешение INN/кода/имени, неоднозначность, fuzzy только как варианты |
| `test_handlers.py` | 34 | 13 handler'ов, параметризованный SQL, NULL, валюты, единицы, evidence |
| `test_orchestrator.py` | 20 | цепочка статусов, маппинг ошибок, отсутствие маскировки DB/LLM ошибок под `NOT_FOUND` |
| `test_result_validator.py` | 19 | проверки результата, evidence, запрет данных у не-FOUND статусов |
| `test_api.py` | 15 | публичные endpoint'ы, отсутствие секретов, структура ответа |
| `test_llm_adapter.py` | 15 | strict JSON, retry, transport/structure ошибки, отсутствие ключа |
| `test_import_integrity.py` | 13 | staging, целостность, контрольные суммы, атомарное переключение |
| `test_formatter.py` | 10 | детерминированные RU/UZ ответы, Decimal, отсутствие выдуманных значений |
| `test_evaluation.py` | 4 | форма набора из 100 вопросов, покрытие статусов/локалей, опциональный live-прогон |
| **Итого** | **310** | 309 passed + 1 skipped |

### 2.2. Что именно проверяется на уровне бизнес-правил

- Денежные значения считаются через `Decimal`/`NUMERIC`; `float` не используется.
- `NULL` не подменяется нулём; пустая агрегация отличается от «записей нет».
- UZS и USD не складываются и не сравниваются без явного метода конвертации из README.
- Физический объём = `quantity_lots × lot_multiplier`; объёмы разных единиц не суммируются.
- `warehouse_region` не используется как регион компании.
- Товар связывается только через `contract_no`, не через название.
- Неоднозначная сущность не выбирается автоматически: возвращается `NEEDS_CLARIFICATION`.
- Ошибка LLM/DB не превращается в `NOT_FOUND`.
- Каждый `FOUND` содержит `data` и `evidence`; `UNSUPPORTED`/`NEEDS_CLARIFICATION`/`UNVERIFIED` не содержат данных/evidence.
- Runtime-роль БД имеет только `SELECT`; запись от `uzex_reader` отклоняется.

---

## 3. Импорт и целостность

Отчёт импорта: `data/manifests/last_import_report.json`.

| Сущность | Загружено |
|---|---:|
| `deals_2022.csv` | 84 299 |
| `deals_2023.csv` | 93 985 |
| `deals_2024.csv` | 106 647 |
| `deals_2025.csv` | 115 763 |
| `deals_2026.csv` | 99 306 |
| **Всего сделок** | **500 000** |
| `contracts.csv` | 6 616 |
| `participants.csv` | 14 392 |
| `brokers.csv` | 120 |
| `usd_rates.csv` | 1 765 |

Статус импорта: `IMPORTED`. Проверок: **97**, финальных проверок: **8**, все `ok=true`.

Финальные проверки:

| Проверка | Ожидалось | Фактически |
|---|---:|---:|
| `final:deals_total` | 500 000 | 500 000 |
| `final:contracts` | 6 616 | 6 616 |
| `final:participants` | 14 392 | 14 392 |
| `final:brokers` | 120 | 120 |
| `final:usd_rates` | 1 765 | 1 765 |
| `final:deal_version` | 0 | 0 |
| `final:internal_uzs` | 206 835 219 986 242.97 | 206 835 219 986 242.97000000 |
| `final:active_version` | 1 | 1 |

Независимо пересчитанные контрольные значения:

| Показатель | Значение |
|---|---:|
| Внутренние сделки, сумма UZS | `206835219986242.97` |
| Экспортные сделки, сумма USD | `1137509729.39` |
| Экспортные сделки, сумма в UZS по курсу на дату сделки | `13641937568188.8167` |
| Итог по методу README (UZS) | `220477157554431.7867` |

Импорт идемпотентен: повторный запуск возвращает `SKIPPED_ALREADY_ACTIVE`, `--force` выполняет повторную загрузку, `--dry-run` — только валидацию.

Ожидаемые `NULL` (не ошибки): `payment_date` — 40 437, `delivery_date` — 46 291, `delivered_lots` — 46 291.

---

## 4. 100-вопросный evaluation

Набор: `backend/evaluation/questions.json`
Запуск: `backend/evaluation/run_evaluation.py`
Отчёт: `backend/evaluation/reports/evaluation_report.json`

```text
evaluation: 100/100 passed (100.0%)
statuses: {"FOUND": 64, "NEEDS_CLARIFICATION": 13, "NOT_FOUND": 13, "UNSUPPORTED": 10}
```

| Группа | Passed |
|---|---:|
| count | 15/15 |
| amount | 15/15 |
| volume | 10/10 |
| top | 7/7 |
| trend | 3/3 |
| compare | 5/5 |
| lookup | 10/10 |
| list | 3/3 |
| clarification | 8/8 |
| unsupported | 8/8 |
| edge | 2/2 |
| uz_latn | 8/8 |
| uz_cyrl | 6/6 |

Для каждого ответа проверяются:

- ожидаемый публичный статус;
- локаль ответа (`ru`, `uz_latn`, `uz_cyrl`);
- наличие `data` и `evidence` у `FOUND`;
- отсутствие `data`/`evidence` у `UNSUPPORTED`, `NEEDS_CLARIFICATION`, `UNVERIFIED`;
- наличие `clarification` у `NEEDS_CLARIFICATION`;
- `matched_rows = 0` у `NOT_FOUND` с evidence.

Числовые эталоны (11 вопросов) сверяются с `data.value`/строками:

| Вопрос | Эталон |
|---|---:|
| Всего сделок | 500 000 |
| Сделки 2022 | 84 299 |
| Сделки 2023 | 93 985 |
| Сделки 2024 | 106 647 |
| Сделки 2025 | 115 763 |
| Сделки 2026 | 99 306 |
| Экспортные сделки | 6 590 |
| Сумма в USD | 1 137 509 729.39 |
| Сумма в UZS | 206 835 219 986 242.97 |
| UZ Latin, сделки 2024 | 106 647 |
| UZ Cyrillic, сделки 2024 | 106 647 |

Регрессии, найденные evaluation и исправленные:

- «экспортных/внутренних» ошибочно трактовались как название товара;
- узбекский Latin `shartnoma raqami` не приводил к contract lookup.

Обе регрессии закрыты тестами в `test_local_planner.py`.

**Важно:** 100/100 означает совпадение с заранее заданными ожиданиями для этого набора. Это не доказательство корректности любых формулировок и не заявление о «100% точности».

---

## 5. API smoke-проверки

Проверено на запущенном backend (`http://127.0.0.1:8000/api/v1`):

| Endpoint / запрос | Результат |
|---|---|
| `GET /health` | `{"status":"ok","version":"1.0.0"}` |
| `GET /ready` | `ready`, checks: database/dataset/schema = true |
| `GET /meta` | 500 000 сделок, 6 616 контрактов, 14 392 участника, 120 брокеров, 1 765 курсов |
| `GET /examples` | примеры помечены `is_example: true` |
| `POST /chat` «Сколько сделок было в 2024 году?» | `FOUND`, 106 647, evidence `matched_rows=106647` |
| `POST /chat` «Сколько сделок было в 2027 году?» | `NOT_FOUND`, evidence `matched_rows=0` |
| `POST /chat` «Покажи брокера KAHIR» | `NEEDS_CLARIFICATION` (сторона брокера) |
| `POST /chat` «Спрогнозируй количество сделок в 2027 году» | `UNSUPPORTED` |
| `POST /chat` «2024-yilda nechta bitim bo'ldi?» | `FOUND`, `uz_latn`, 106 647 |
| `POST /chat` «Битим рақами 100001» | `FOUND`, `uz_cyrl`, evidence `matched_rows=1` |

Секреты и внутренние строки подключения в ответах не обнаружены.

---

## 6. Frontend

Сборка:

```powershell
npm run build
```

Результат:

```text
vite v5.4.21 building for production...
✓ 32 modules transformed.
dist/index.html                       0.60 kB │ gzip:   0.43 kB
dist/assets/index-RGuTq8uT.css      12.16 kB │ gzip:   3.44 kB
dist/assets/index--k11eRpr.js      158.65 kB │ gzip:  52.10 kB
✓ built in 607ms
```

Функциональные browser-проверки (dev UI `http://127.0.0.1:5173`):

- `FOUND`: число + evidence-блок, RU;
- `NOT_FOUND` для 2027;
- `NEEDS_CLARIFICATION` для брокера без стороны;
- `UNSUPPORTED` для прогноза;
- deal lookup `100001`;
- UZ Latin count;
- неоднозначный участник — варианты уточнения;
- ошибок в консоли браузера нет;
- скалярная строка не дублируется как агрегатная таблица.

Frontend обращается только к `/api/v1`; LLM-ключ и серверные env в клиентский bundle не попадают.

---

## 7. Docker / deployment

Созданы:

- `Dockerfile.backend`, `Dockerfile.frontend`;
- `docker-compose.yml` (db → migrate → import → api → web);
- `deploy/nginx.conf` (same-origin proxy `/api/` → `api:8000`);
- `deploy/postgres/init/01-reader-role.sh`;
- `.env.example`, `.dockerignore`.

Compose-файл успешно парсится, но **фактический `docker build`/`docker compose up` не выполнялся**: в окружении отсутствуют `docker` и `docker-compose`. Поэтому образы и health-цепочка контейнеров не подтверждены практическим прогоном.

Публичный доступ проверен через временный **Cloudflare Quick Tunnel**:

```text
https://advice-receive-rolling-string.trycloudflare.com
```

Туннель поднят к локальному демонстрационному серверу `scripts/serve_frontend.py` (порт `8088`),
который отдаёт production-сборку `frontend/dist` и проксирует `/api/` на FastAPI. Проверено:

- `GET /` — UI загружается, readiness `Сервис готов`;
- `GET /api/v1/ready` — `ready`;
- `POST /api/v1/chat` через UI — `FOUND`, 106 647, evidence отображается;
- ошибок консоли браузера нет.

Quick Tunnel не является постоянным хостингом: нет SLA/uptime-гарантии, ссылка живёт только пока
запущены локальные процессы. Для стабильного публичного URL нужен собственный хост/Docker с
доменом и HTTPS. Docker-образы в этом окружении по-прежнему не собраны.

---

## 8. Ограничения и честные оговорки

1. **Evaluation — это проверка ожиданий, а не доказательство универсальной точности.** Набор из 100 вопросов покрывает поддержанные формы, но не все возможные формулировки.
2. **LLM-режим не проверялся вживую**: нет `LLM_API_KEY`/`LLM_MODEL`; текущий runtime — `PLANNER_MODE=local`. LLM-адаптер покрыт unit-тестами с моками.
3. **Docker-образы не собраны** из-за отсутствия Docker; конфигурация не валидирована practically.
4. **Публичный URL временный** (Cloudflare Quick Tunnel); он не является постоянным хостингом и
   перестанет работать после остановки локальных процессов. Постоянный деплой не выполнен.
5. Локальный планировщик намеренно fail-closed: часть корректных, но не описанных в словарях формулировок вернёт `UNSUPPORTED`; часть неоднозначных — `NEEDS_CLARIFICATION`. Это соответствует спецификации, но ограничивает покрытие.
6. Отдельные вопросы с неоднозначным названием товара/региона (`Средняя цена карбамида`, `Топ-5 ... из Ташкентской области`) намеренно требуют уточнения и не разрешаются автоматически.
7. Проверки выполнялись на локальном PostgreSQL-кластере; поведение на другом железе/версии PostgreSQL не измерялось.

# UZEX Data Agent — итоговый отчёт о валидации

Дата прогона: **2026-11-05**
Окружение: Windows, Python 3.12, PostgreSQL 18 (локальный кластер `127.0.0.1:55432`), Node.js/Vite.
Версия датасета: `sha256:96424654325b6c41074b67aa3919435747f9cbc029a7fe9bf5e2dd02810e8fab`
Срез данных: **2026-10-30**.

> **Статус живого LLM-тестирования: ВЫПОЛНЕНО ЧАСТИЧНО, С ЧЕСТНО ЗАФИКСИРОВАННЫМ
> ОГРАНИЧЕНИЕМ.**
>
> - **Groq (`openai/gpt-oss-120b`)**: реальный live-прогон **21/21**, один и тот же
>   `QueryPlan` и те же бизнес-правила. `live_calls = true`, моков нет.
> - **Gemini (нативный адаптер)**: реальный live-прогон **21/21** на доступной
>   модели `gemini-3.1-flash-lite-preview` (`responseSchema`). Из 21 кейса
>   **19 ответила непосредственно Gemini**, 2 — Groq после исчерпания суточной
>   квоты Gemini; итог 21/21.
> - **`gemini-3.8-flash`**: модель существует в живом списке моделей и ключ
>   валиден, но free-tier дневная квота (20 запросов/день на модель) исчерпана
>   на **обоих** ключах (основном и запасном) → HTTP 429 `RESOURCE_EXHAUSTED`.
>   Успешной генерации именно `gemini-3.8-flash` сегодня **не получено**, поэтому
>   её happy-path нельзя объявлять проверенным. Это ограничение, а не пройденный
>   тест.
> - Отказ Gemini по 429 вживую корректно уходит в fallback Groq и **не**
>   превращается в `NOT_FOUND`/выдуманный ответ.

---

## 1. Сводка

| Проверка | Результат |
|---|---|
| Полный `pytest` | **349 passed, 1 skipped, 0 failed** (37.93 s) |
| Пропущенный тест | 100-вопросный evaluation; включается `UZEX_RUN_EVALUATION=1` |
| 100-вопросный evaluation | **100/100**, все группы 100 % |
| Импорт и целостность датасета | **PASSED**, 500 000 сделок, контрольные суммы точные |
| Adversarial-тесты (код) | **17 passed** |
| Performance/устойчивость (код) | **6 passed** |
| Provider-тесты (код) | **17 passed** |
| Живой Groq | **21/21 (100 %)**, `openai/gpt-oss-120b` |
| Живой Gemini | **21/21 (100 %)**, 19 ответов Gemini + 2 fallback после 429 |
| Живой `gemini-3.8-flash` | **НЕ ЗАВЕРШЁН** — free-tier 429 на обоих ключах |
| Import-проверка модулей | успешно |
| Frontend production build | успешно (32 модуля) |

Итог: анти-галлюцинационный контур подтверждён на уровне кода, тестов,
evaluation и **реальных** вызовов Groq и Gemini. Остаётся одно явно
зафиксированное ограничение: успешный ответ именно `gemini-3.8-flash` сегодня
не получен из-за квоты; адаптер Gemini проверен на других доступных моделях
Gemini с тем же транспортом, схемой и Pydantic-контрактом.

---

## 2. Что изменено

### 2.1. Нативные адаптеры провайдеров

`backend/app/agent/llm_adapter.py`:

- **`GeminiPlannerAdapter`** — нативный `models/{model}:generateContent`:
  - `responseMimeType = application/json`;
  - `responseJsonSchema` (стандартный JSON Schema) или `responseSchema`
    (OpenAPI-подмножество) по `GEMINI_SCHEMA_MODE`;
  - `temperature = 0`;
  - ключ передаётся только в заголовке `x-goog-api-key` и никогда не попадает
    в тело запроса, логи или ответ;
  - tools / function calling / code execution **не передаются**.
- **`GroqPlannerAdapter`** — OpenAI-совместимый `/chat/completions` со строгим
  `response_format={"type":"json_schema","json_schema":{"strict":true,...}}`.
- **`LLMPlannerAdapter`** — прежний generic OpenAI-совместимый адаптер сохранён.
- **`FailoverPlanner`** — переключение только по `ProviderTechnicalError`.

### 2.2. Единая схема для всех моделей

Все адаптеры используют один и тот же Pydantic-класс `QueryPlan`
(`extra="forbid"`). Схемы для провайдеров **генерируются из него**:

- `gemini_response_json_schema()` — инлайнит `$ref`/`$defs`;
- `gemini_response_schema()` — убирает аннотации, не поддерживаемые старым
  `responseSchema`;
- `openai_strict_schema()` — `additionalProperties=false`, все поля в `required`.

Для Groq в wire-схеме четырёхветвевой `FilterValue` (`str | int | list[str] |
list[int]`) оставлены две различимые ветви (`string | array[string]`), потому что
strict-валидатор Groq требует различимых `anyOf`. Серверный Pydantic по-прежнему
принимает полный union и приводит типы. Это меняет только проводной формат
провайдера, а не валидируемый контракт.

Проверено вживую:

- Groq принимает strict `json_schema` и возвращает валидный `QueryPlan`;
- Gemini 3.8 принимает `responseJsonSchema` структурно (запрос доходит до
  проверки квоты), но успешный ответ не получен из-за 429;
- более старые Gemini-модели (`gemini-3-flash-preview`,
  `gemini-3.1-flash-lite*`) отклоняют `responseJsonSchema` с HTTP 400 и
  корректно работают через `GEMINI_SCHEMA_MODE=response_schema`.

### 2.3. Failover только при технической ошибке провайдера

`FailoverPlanner` переключается на Groq **только** при
`ProviderTechnicalError` (транспорт, timeout, HTTP 4xx/5xx, 429, битый
envelope). Он **не** переключается:

- при семантически неверном плане (`UnverifiedQuery` → `UNVERIFIED`);
- при валидном `NOT_FOUND` после SQL;
- при `CLARIFY`/`UNSUPPORTED`.

Планирование выполняется **ровно один раз** за запрос; SQL идёт уже после
плана, поэтому пустой результат SQL физически не может запустить второй модель.

### 2.4. Уточнение prompt по роли участника

Live-прогон Gemini выявил поведенческое расхождение: на вопрос
«Покажи участника Несуществующая Компания» модель возвращала
`CLARIFY/PARTICIPANT_ROLE_AMBIGUOUS` вместо поиска и `NOT_FOUND`. Backend
разрешает `role=any` для `participant_lookup`, поэтому prompt уточнён:

- отсутствие стороны сделки само по себе не делает вопрос неоднозначным;
- для простого показа участника/брокера используется `role=any` и `EXECUTE`;
- сторона требуется только там, где без неё нельзя посчитать сделку/сумму/
  количество по компании (например, «Сколько сделок у компании X?»).

После правки кейс стабильно даёт `NOT_FOUND` через Gemini primary, а
`ru_ambiguous_company` по-прежнему даёт `NEEDS_CLARIFICATION`.

### 2.5. Конфигурация

Добавлены серверные настройки (только backend `.env` / окружение):

| Переменная | Назначение |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_BASE_URL`, `GEMINI_SCHEMA_MODE` | основной провайдер |
| `GEMINI_API_KEY_SECONDARY` | запасной ключ Gemini (резерв) |
| `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_BASE_URL` | технический fallback |
| `GROQ_API_KEY_SECONDARY` | запасной ключ Groq (резерв) |
| `LLM_FALLBACK_ENABLED` | включение/выключение fallback |
| `LLM_PROVIDER=gemini\|groq\|openai_compatible` | выбор основного транспорта |

Запасные ключи **не ротируются автоматически**: 429/401 — это техническая
ошибка провайдера, она следует обычной политике failover и не должна молча
тратить вторую квоту. `Settings.public_safe_dict()` отдаёт только
`llm_configured`, `llm_secondary_keys_configured` и имя провайдера; ключи не
сериализуются. Тест `test_describe_never_exposes_any_api_key` проверяет, что
`describe()` не содержит ни одного ключа.

---

## 3. Полный прогон тестов

```powershell
cd backend
python -m pytest -q
```

```text
349 passed, 1 skipped in 37.93s
```

| Файл | Тестов |
|---|---:|
| `test_local_planner.py` | 95 |
| `test_plan_validator.py` | 45 |
| `test_entity_resolution.py` | 40 |
| `test_handlers.py` | 34 |
| `test_orchestrator.py` | 20 |
| `test_result_validator.py` | 19 |
| `test_adversarial.py` (новый) | 17 |
| `test_llm_providers.py` (новый) | 17 |
| `test_llm_adapter.py` | 15 |
| `test_api.py` | 15 |
| `test_import_integrity.py` | 13 |
| `test_formatter.py` | 10 |
| `test_performance.py` (новый) | 6 |
| `test_evaluation.py` | 4 |
| **Итого собрано** | **350** (349 passed + 1 skipped) |

Существующие тесты не удалялись и не переписывались: новые файлы добавлены
рядом.

---

## 4. Adversarial-тесты

Файл `backend/tests/test_adversarial.py` (17 тестов, все passed):

| Сценарий | Проверяемое поведение |
|---|---|
| Несуществующая компания | `NOT_FOUND`, без `data` |
| Несуществующий ИНН | `NOT_FOUND`, без `data` |
| Несуществующий товар | `NOT_FOUND`, без `data` |
| Несуществующий контракт | `NOT_FOUND`, без `data` |
| Будущая дата / нет данных | `NOT_FOUND`, `matched_rows=0`, без выдуманного нуля |
| Перевёрнутый диапазон дат | отклоняется схемой |
| Противоречивые условия | `NOT_FOUND`, не число |
| Похожие названия компаний | `NEEDS_CLARIFICATION`, 2 варианта |
| SQL-инъекция в тексте | `UNSUPPORTED`; параметризованный SQL; `deals` = 500 000 |
| Prompt injection с полями `sql`/`answer`/`result` | `UNVERIFIED`, executor не вызван |
| Prompt injection в user-тексте | текст остаётся в user-message, не в system |
| Принудительно выдуманный ответ | `UNVERIFIED`, число не попадает в ответ |
| NULL delivered volume | `NOT_FOUND`, не `0` |
| UZS + USD | две независимые суммы, без общего числа |
| Явный UZS / USD | возвращается только запрошенная валюта |
| LLM HTTP 500 | `UNVERIFIED`, не `NOT_FOUND` |
| LLM 429 rate limit | `UNVERIFIED`, не `NOT_FOUND` |

Те же классы сценариев дополнительно прогнаны **вживую** (см. раздел 6).

---

## 5. Performance и устойчивость

Файл `backend/tests/test_performance.py` (6 тестов, все passed):

| Проверка | Результат |
|---|---|
| Время ответа на репрезентативных вопросах | max < 5 s, медиана < 2 s |
| 8 конкурентных запросов | все завершаются, без cross-talk, уникальные `request_id` |
| Один медленный запрос не блокирует другого | быстрый запрос < 1 s, не ждёт медленный |
| 5 повторных одинаковых вопросов | стабильно 106 647 и одинаковый evidence |
| `statement_timeout` PostgreSQL | `UNVERIFIED`, без `data`/`evidence` |
| Повторный старт backend | тот же `FOUND`, то же значение и версия датасета |

Живые latency-замеры (включают сеть провайдера, PostgreSQL и форматирование):

| Провайдер | median | max | Комментарий |
|---|---:|---:|---|
| Groq `openai/gpt-oss-120b` | 2076 ms | 3952 ms | 21 live-кейс |
| Gemini primary | 1792 ms | 27 160 ms | max — 2 кейса после 429 Gemini + fallback Groq |

Это измерения на текущем датасете и free-tier провайдерах, а не SLA-бенчмарк.

---

## 6. Живой LLM: фактические результаты

### 6.1. Groq — основной live-прогон fallback-провайдера

Команда:

```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
python evaluation/run_live_llm_validation.py --provider groq --delay 20
```

Результат:

```text
live LLM validation: 21/21 passed (100.0%) provider=groq
planner: {"name": "groq", "provider": "groq",
          "model": "openai/gpt-oss-120b", "structured_output": "json_schema"}
```

Отчёт: `backend/evaluation/reports/live_llm_report.json`
(`status=COMPLETED`, `live_calls=true`, `models_used=["groq"]`,
`injection_did_not_modify_dataset=true`, `post_injection_count=500000`).

Покрытые кейсы: RU / UZ Latin / UZ Cyrillic, 2024, всего сделок, ИНН продавца,
ТН ВЭД, контракт, компания в кавычках, товар, январь 2024, USD, UZS,
несуществующие компания/ИНН/товар/контракт, будущий 2027 год, неоднозначная
компания, прибыль (unsupported), prompt injection, SQL injection.

### 6.2. Gemini — live-прогон нативного адаптера

Из-за исчерпания квоты `gemini-3.8-flash` адаптер проверен на доступной
Gemini-модели с тем же нативным транспортом и схемой:

```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
$env:GEMINI_MODEL="gemini-3.1-flash-lite-preview"
$env:GEMINI_SCHEMA_MODE="response_schema"
python evaluation/run_live_llm_validation.py --provider gemini --delay 5 `
  --output evaluation/reports/live_llm_report_gemini_primary.json
```

Результат:

```text
live LLM validation: 21/21 passed (100.0%) provider=gemini
planner: {"name": "failover",
          "primary": {"name": "gemini", "model": "gemini-3.1-flash-lite-preview",
                      "structured_output": "response_schema"},
          "fallback": {"name": "groq", "model": "openai/gpt-oss-120b"},
          "fallback_policy": "technical_provider_error_only"}
```

Отчёт: `backend/evaluation/reports/live_llm_report_gemini_primary.json`
(`status=COMPLETED`):

- `passed=21`, `failed=0`, `pass_rate=1.0`;
- `planner_used`: **primary=19**, **fallback=2**;
- `injection_did_not_modify_dataset=true`, `post_injection_count=500000`;
- 2 fallback-кейса (`ru_prompt_injection`, `ru_sql_injection`) — Gemini вернул
  техническую ошибку/429 после дневной квоты, запрос корректно обработан Groq;
  ни один кейс не превратился в выдуманный ответ.

Дополнительно адаптер был проверен на `gemini-3-flash-preview`
(`response_schema`): до правки prompt — 20/21 (единственное расхождение —
`NEEDS_CLARIFICATION` вместо `NOT_FOUND` для несуществующей компании), после
правки — фокусная проверка того же кейса дала `NOT_FOUND`.

### 6.3. `gemini-3.8-flash`: точная причина блокировки

- Модель присутствует в живом списке `GET /v1beta/models`:
  `models/gemini-3.8-flash` — **существует**.
- Оба ключа (основной и запасной) получают:

```text
HTTP 429 RESOURCE_EXHAUSTED
Quota exceeded ... limit: 20, model: gemini-3.8-flash
Generate content requests per day limit exceeded on Free Tier.
Please retry in ~21h.
```

- `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`,
  `gemini-flash-latest` в момент прогона также вернули 429.
- Запрос с полной схемой `QueryPlan` для `gemini-3.8-flash`
  (`responseJsonSchema`) проходит структурную проверку и упирается именно в
  квоту (429), а не в 400 — то есть схема проводного формата принята.
- Следовательно: **нельзя утверждать, что happy-path `gemini-3.8-flash`
  проверен вживую.** Нужен повторный прогон после сброса квоты или ключ с
  billing/большим лимитом.

Команда для повторной проверки после сброса:

```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
$env:GEMINI_MODEL="gemini-3.8-flash"
$env:GEMINI_SCHEMA_MODE="json_schema"
python evaluation/run_live_llm_validation.py --provider gemini --delay 20
```

---

## 7. Evaluation

```text
evaluation: 100/100 passed (100.0%)
statuses: {"FOUND": 64, "NEEDS_CLARIFICATION": 13, "NOT_FOUND": 13, "UNSUPPORTED": 10}
```

| Группа | Passed |
|---|---:|
| amount | 15/15 |
| clarification | 8/8 |
| compare | 5/5 |
| count | 15/15 |
| edge | 2/2 |
| list | 3/3 |
| lookup | 10/10 |
| top | 7/7 |
| trend | 3/3 |
| unsupported | 8/8 |
| uz_cyrl | 6/6 |
| uz_latn | 8/8 |
| volume | 10/10 |

`100/100` означает совпадение с заранее заданными ожиданиями этого набора, а не
универсальную «100 % точность».

---

## 8. Импорт и целостность

Подтверждено тестами `test_import_integrity.py` и активным манифестом:

- 500 000 сделок (2022: 84 299; 2023: 93 985; 2024: 106 647; 2025: 115 763;
  2026: 99 306);
- contracts 6 616, participants 14 392, brokers 120, usd_rates 1 765;
- `validation_status = PASSED`, `max_deal_date = 2026-10-30`;
- контрольные суммы `internal_uzs`, `export_usd`, `export_converted_uzs`
  совпадают с независимым пересчётом;
- runtime-роль БД — только `SELECT`.

---

## 9. Frontend

```text
vite v5.4.21 building for production...
✓ 32 modules transformed.
dist/index.html                       0.60 kB │ gzip:  0.43 kB
dist/assets/index-RGuTq8uT.css      12.16 kB │ gzip:  3.44 kB
dist/assets/index--k11eRpr.js      158.65 kB │ gzip: 52.10 kB
✓ built in 1.05s
```

Frontend использует только `VITE_API_BASE_URL`; LLM-ключи и серверные env в
клиентский bundle не попадают.

---

## 10. Критерий завершённости

> «Ни одна неподтверждённая компания, сумма, дата или иная сущность не должна
> попасть в ответ пользователю.»

На уровне кода это обеспечивается цепочкой:

`LLM → QueryPlan (extra=forbid) → Pydantic → entity resolution → allowlist/policy
→ параметризованный SQL → result/evidence validation → детерминированный
formatter`.

Live-прогоны подтверждают:

- Groq 21/21 и Gemini-primary 21/21 не выдали ни одного неподтверждённого
  значения;
- prompt injection и SQL injection не изменили датасет (`500000` после
  инъекции);
- несуществующие сущности дали `NOT_FOUND` без `data`;
- технические ошибки провайдера дали fallback/`UNVERIFIED`, но не
  `NOT_FOUND` и не выдуманное число.

Остаётся ограничение: успешный ответ именно `gemini-3.8-flash` сегодня не
получен из-за free-tier 429. До повторного прогона нельзя утверждать, что
именно эта модель проверена end-to-end.

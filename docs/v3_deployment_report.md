# V3 deployment report — Telegram bot on AWS

Recorded: 2026-11-05 (post-reboot verification at 14:43 UTC)

> The demonstration host has since been decommissioned. Infrastructure identifiers
> (instance ID, IP addresses, bot handle) are redacted in this public copy; the
> engineering results below are unchanged.

## 1. What is deployed

| Item | Verified state |
|---|---|
| Bot | `@example_bot` — `https://t.me/example_bot` |
| Host | EC2 `<EC2_INSTANCE_ID>`, `eu-north-1`, `<EC2_PUBLIC_IP>` |
| App directory | `/opt/uzex/app` |
| Containers | `uzex-data-agent-telegram-bot-1` healthy, `uzex-data-agent-db-1` healthy |
| Transport | Telegram long polling only; no public website or domain |
| Planner | `PLANNER_MODE=local` (Groq remains UNQUALIFIED) |
| Answer generation | `ANSWER_GENERATION_MODE=off` (deterministic templates only) |
| Dataset | 500,000 deals, cutoff `2026-10-30` |
| DB role | Runtime reads through `uzex_reader` (SELECT-only) |

The deployment was updated in place on the existing EC2 host. No AWS resource
was created, changed or deleted as part of this V3 rollout.

## 2. Packaging incident and fix

The first V3 image started and passed its healthcheck, but an in-container
application smoke failed before answering any question:

```text
FileNotFoundError: [Errno 2] No such file or directory:
  '/usr/local/lib/python3.12/site-packages/app/language/synonyms_uz_cyrl.json'
```

Cause: `backend/pyproject.toml` declared only Python packages. `pip install .`
therefore installed `.py` modules but omitted the runtime resources read via
`pathlib.Path(__file__)`:

- `app/language/*.json` (locale messages and synonym dictionaries);
- `app/rag/knowledge/*.json` (documentation index metadata);
- `app/agent/prompts/*.txt` (the LLM planner prompt, unused while
  `PLANNER_MODE=local` but part of the package contract).

Fix: explicit package data in `backend/pyproject.toml`:

```toml
[tool.setuptools.package-data]
app = [
  "language/*.json",
  "rag/knowledge/*.json",
  "agent/prompts/*.txt",
]
importer = ["*.json"]
```

The fix was validated locally by building a wheel and confirming all resources
were present and importable from the extracted wheel before rebuilding the
server image. The production image was rebuilt and the bot container recreated.

## 3. Production smoke (inside the deployed container)

Run through `Orchestrator` using the container's installed package, after the
packaging fix and again after an EC2 reboot:

```text
SMOKE | FOUND     | 106647 | ru       | Сколько сделок было в 2024 году?
SMOKE | FOUND     | 93985  | ru       | Сколько сделок было в 2023 году?
SMOKE | NOT_FOUND | None   | ru       | Сколько сделок было в 2003 году?
SMOKE | FOUND     | 106647 | uz_latn  | 2024-yilda nechta bitim bo'ldi?
SMOKE | FOUND     | 106647 | uz_cyrl  | 2024-йилда нечта битим бўлди?
SMOKE | FOUND     | 500000 | ru       | Сколько всего сделок?
FOLLOWUP | FOUND | context=False | FOUND | 93985 | context=True
AFTER_RESET | UNSUPPORTED | None
```

- Required control values match: 2024 = `106647`, 2023 = `93985`,
  2003 = `NOT_FOUND`.
- Uzbek Latin and Uzbek Cyrillic return the same verified values.
- Total deal count from PostgreSQL: `500000`.
- `А за 2023?` resolves from same-session context only; after `/reset` the
  context is gone and the ambiguous question returns `UNSUPPORTED`.

## 4. Restart and reboot recovery

- Container restart: the worker stops on `SIGTERM` (`telegram_polling_stopped`),
  restarts, re-reads its identity and resumes polling. One
  `telegram_lock_release_failed` warning was logged during shutdown; the
  PostgreSQL advisory lock is session-scoped and is released when the
  connection closes, so this did not block the restart.
- EC2 reboot: both containers returned healthy, `telegram_polling_started`
  appeared, the database still reported `500000` deals, and the full smoke
  above passed again.

## 5. Log hygiene

- A scan of the bot logs for `api.telegram.org/bot<digits>:` returned **0**
  matches. The token is not present in the log stream.
- Log rotation is configured (`json-file`, `max-size: 10m`, `max-file: 3`).
- `/opt/uzex/app/.env` remains `root:root`, mode `600`; it is never printed.

## 6. Live inbound message and regression fix

A real inbound Telegram message was recorded at `2026-11-05T14:35:58Z`
(persisted in the per-user SQLite session store):

```text
Какое полное наименование товара чаще всего встречалось
в сделках 2024 года и сколько раз?
```

The worker answered `NOT_FOUND` with `товар «чаще всего встречалось»`. This
was a genuine local-planner defect: the product extractor captured the
analytical phrase after the word «товара» as a product name. It was fixed by
treating analytical tails (`чаще/реже`, `наименован`, `полное`, `встречал`,
plus Uzbek and Cyrillic equivalents) as generic question phrasing. A regression
test was added and the fixed plan returns a grounded `FOUND` top-10 product
list (first row `Хлопок-сырец (спот)`, 11 253 deals in 2024).

The fix was verified locally (719 passed, 1 skipped; 180/180 conversational;
100/100 standard), the image was rebuilt, and the bot container was recreated
at `2026-11-05 14:51 UTC`. The in-container smoke now returns `FOUND` for the
same question, with the same first rows.

## 7. Still open

- **Live inbound round trip:** at least one real inbound message is confirmed
  by the persisted session state. A fresh `telegram_update_processed` /
  `telegram_reply_sent` log pair should be captured after the redeploy and a
  new operator message; container recreation resets the in-memory log buffer.
- **Token rotation:** the bot token was exposed in an earlier chat. It should
  be rotated; rotation requires updating `/opt/uzex/app/.env` (mode 600) and
  restarting the worker. Never print or commit the token.
- **Disk:** the root volume is tight (~613 MB free, 91% used after the latest
  rebuild). Avoid unnecessary image or build-cache growth; prune unused build
  cache before the next image build.
- **LLM path:** enabled for planning, general answers and causal hypotheses
  (`PLANNER_MODE=llm`, `GENERAL_MODE_ENABLED=true`, `EXPLANATION_MODE=llm`).
  The Groq free-tier daily token budget (200 000 TPD, shared by all keys in the
  same organization) is the current operational limit. See section 8.

## 8. General AI Mode rollout

Recorded: 2026-11-05, 17:08–17:15 UTC.

### 8.1 What changed on the host

| Item | State |
|---|---|
| Source | `/opt/uzex/app/backend` replaced with the General AI Mode build |
| New module | `backend/app/agent/general.py` present |
| New prompts | `general_assistant_v1.txt`, `explanation_v1.txt` readable in the image |
| Backup source | `/opt/uzex/backup-src-20261105T170809Z.tar.gz` |
| Backup env | `/opt/uzex/backup-env-20261105T170809Z` (`root:root`, `600`) |
| Image | `uzex-data-agent-backend:local` rebuilt (`ff71703b5f56`) |
| Containers | `telegram-bot` and `db` healthy; polling started |

The production `.env` remains `root:root`, mode `600`. It now contains
`PLANNER_MODE=llm`, `GENERAL_MODE_ENABLED=true`, `EXPLANATION_MODE=llm`,
`GENERAL_ANSWER_MAX_CHARS=2000`, `EXPLANATION_MAX_CHARS=900`, and four Groq
credentials from the same organization. No secret value is recorded here.

### 8.2 In-container verification

```text
general_module: True
orchestrator: /app/app/agent/orchestrator.py
planner_mode: llm
general_mode_enabled: True
explanation_mode: llm
llm_provider: groq
llm_primary_model: openai/gpt-oss-120b
groq_key_set: True
general_prompt_chars: 1756
explanation_prompt_chars: 1268
```

Direct read-only database check from the same container: `select count(*) from
deals` returned `500000`.

### 8.3 Provider limit observed

The first post-deploy data smoke returned `UNVERIFIED` with no data. The cause
was not application code and not PostgreSQL. A direct provider request with the
exact planner body returned:

```text
HTTP 429 tokens rate_limit_exceeded
Rate limit reached for model openai/gpt-oss-120b ... on tokens per day (TPD):
Limit 200000, Used 198150, Requested 4107
```

The four keys configured at that moment belonged to one organization, so
rotation could not add quota; this is enforced by design (`rotate_on_429:
false`). One planner call requests about 4.1k tokens (prompt + strict JSON
Schema + `max_tokens`), which caps one free-tier organization at roughly 40–50
agent turns per day across all users.

### 8.4 Production smoke results

Run sequentially on 2026-11-05 after the quota reset, through the real
`Orchestrator` inside the production image:

| Question | Status | Mode | Validation | Data / evidence |
|---|---|---|---|---|
| `Сколько сделок было в 2024 году?` | `FOUND` | `data` | `grounded` | `106647`, evidence rows `106647` |
| `Что такое экспорт?` | `GENERAL` | `general` | `general_validated` | no `data`, no `evidence`, 2819 chars |
| `Почему экспортных сделок стало меньше?` | `FOUND` | `data_explanation` | `explanation_validated` | grounded trend + labelled hypotheses |

The causal answer returned the verified export-deal trend first
(2022: 1317, 2023: 1209, 2024: 1327, 2025: 1367, 2026: 1370) and only then the
hypothesis block. No answer invented a company, INN, sum or date.

## 9. Cross-org failover and controlled-filter deployment

Recorded: 2026-11-05, 18:00–18:10 UTC.

### 9.1 What changed

| Item | State |
|---|---|
| Source delta | `app/config.py`, `app/agent/planner.py`, `app/agent/llm_adapter.py`, `app/validation/plan_validator.py` |
| Backups | `/opt/uzex/backup/pre-failover-latest/`, `.env.pre-failover.*`, `.env.pre-sameorg-clear.*`, `docker-compose.yml.pre-failover.*` |
| Image | `uzex-data-agent-backend:local` rebuilt, id `2e9a5fe867e0` |
| Container | `telegram-bot` recreated, healthy, polling started |
| Primary scope | `groq-org-b` / `groq-org-b-default`, 2 credentials (org B) |
| Failover scope | `org_01m6nj8akmeb5sv4p3d8b0s2mb` / `groq-org-a-default`, 2 credentials (org A) |
| Failover enabled | `same_provider_rate_limit_failover: true` |

The org A keys were moved out of the legacy `*_SAME_ORG` fields into
`GROQ_FAILOVER_API_KEYS`, and those fields were cleared. This keeps the primary
registry single-organization: primary `credential_count` fell from 4 to 2.
Mixing the organizations in one registry would have violated the same-scope
rotation rule even though it could not bypass a 429.

### 9.2 Failover policy

- Short RPM/TPM 429: allowed across an independent quota scope only. Two keys
  in the same organization share the budget, so they never fail over to each
  other.
- Long/daily 429: allowed to fail over only when the fallback scope ID differs
  from the primary scope ID.
- `401` invalid/revoked key: rotate only within the same organization.
- Semantically invalid plans (`UnverifiedQuery`) never fail over.

### 9.3 Verification

In-container `build_planner` inspection showed `FailoverPlanner` with
`fallback_policy=technical_provider_error_only`,
`fallback_qualified=True`, and distinct primary/fallback scopes. The three
production smoke questions in section 8.4 were then re-run successfully on this
image. An actual long-429 failover was not forced in production because doing so
would deliberately exhaust the live daily quota; the path is covered by the
unit tests in `backend/tests/test_llm_providers.py`.

## 10. General AI Mode reliability fix

Recorded: 2026-11-06, 02:40-02:56 UTC (deploy at 02:48 UTC).

### 10.1 Reported problem

After the previous rollout, ordinary questions through `@example_bot`
sometimes returned `Не могу достоверно подтвердить ответ по загруженным
данным` instead of an answer, while registry questions still worked. Examples
included `Ты кто?`, `Что такое аниме?` and `Что такое криптография?`.

### 10.2 Root cause

The failure was not PostgreSQL and not a wrong registry match. Live provider
inspection showed Groq free-tier pressure on the primary organization:

- planner call ≈ 3.9-4.1k tokens (prompt + strict JSON Schema + `max_tokens`);
- general answer ≈ 0.9k tokens;
- free tier 8 000 TPM and 200 000 TPD per organization;
- a short burst of turns returned HTTP 429 / `groq quota cooldown is active`.

The general layer reported that technical provider error through the registry
`UNVERIFIED` path, so a quota problem looked like "the data cannot be
confirmed". In parallel, the registry guard could treat a substring such as
`пример` inside `например` as a definition, which made routing brittle.

### 10.3 Code changes

| Area | Change |
|---|---|
| `app/agent/general.py` | Split registry-entity and domain-term cues; token-aware phrase matching (fixes `например` vs `пример`); tightened `looks_like_registry_question`; added conservative `looks_like_general_question` pre-router |
| `app/agent/orchestrator.py` | Deterministic GENERAL pre-router before the planner; `GENERAL_UNAVAILABLE` for provider, validation and unexpected general errors; `request_id` diagnostics; sanitized `_exception_diagnostics` |
| `app/domain/enums.py` | New `ResponseStatus.GENERAL_UNAVAILABLE` |
| `app/responses/formatter.py`, `app/responses/__init__.py` | `format_general_unavailable` |
| `app/language/ru.json`, `uz_latn.json`, `uz_cyrl.json` | `general_unavailable` message; deal line now includes delivered quantity and delivery date |
| `app/agent/llm_adapter.py` | Rate-limit failover allowed across independent quota scopes for short and long 429; same-scope still refused |
| `app/llm/errors.py` | Sanitized `ProviderTechnicalError.diagnostics` |
| prompts / `frontend/src/App.jsx` | Prompt broadened to off-topic questions; status meta for the new state |

Gate guarantees after the change:

- a general turn never opens a database connection and never receives registry
  rows or SQL tools;
- a general provider/validation failure returns `GENERAL_UNAVAILABLE`, never the
  registry `UNVERIFIED` wording;
- registry questions still go through the full planner, validator, resolver and
  read-only SQL path;
- deal lookup shows delivered quantity and delivery date, and renders missing
  values as `нет данных`, never as `0`.

### 10.4 Regression tests

Targeted suite (new and updated cases):

```text
python -m pytest backend/tests/test_general_mode.py backend/tests/test_formatter.py \
  backend/tests/test_llm_providers.py backend/tests/test_provider_reliability.py \
  backend/tests/test_telegram.py -q
124 passed
```

Additional coverage added:

- 7 GENERAL questions pre-routed with no planner call and a failing connection
  factory (`db_calls=0`);
- 3 registry questions are not pre-routed;
- `например` / `пример` regression;
- general provider error and unexpected error produce `GENERAL_UNAVAILABLE`,
  not `UNVERIFIED`;
- log record contains `request_id` and sanitized failure fields, and no key;
- short-429 cross-org failover and same-scope refusal;
- deal line delivered/date and `NULL` -> `нет данных`.

Non-DB unit suite (with `PLANNER_MODE=local` and DB-only modules ignored):
`420 passed, 7 deselected`. Remaining local failures are only the
PostgreSQL-unavailable modules, because the local Docker/Postgres cluster at
`127.0.0.1:55432` is not running.

### 10.5 Deployment

| Item | State |
|---|---|
| Backup | `/opt/uzex/backup/pre_general_router_20261106.tgz` (source, frontend, compose) |
| Image | `uzex-data-agent-backend:local`, id `156cd448cffb`, built 2026-11-06 02:48 UTC |
| Container | `uzex-data-agent-telegram-bot-1`, `Up (healthy)`, polling started |
| Disk | `603 MB` free, 92% used (build cache pruned, 214.5 MB reclaimed) |

Deployment command that actually rebuilds the backend image:

```bash
cd /opt/uzex/app
sudo docker compose build migrate
sudo docker compose up -d --no-deps --force-recreate telegram-bot
```

The `api` service has no `build:` section, so building it is a no-op.

### 10.6 Live verification through the real Telegram pipeline

Run inside the production container with the production `Settings`, the real
`Orchestrator`, the real `TelegramHandlers` and the real read-only database.
A counting wrapper around the connection factory recorded database access.

| # | Question | Status | Mode | Provider | LLM | DB calls | Data / evidence |
|---|---|---|---|---|---|---|---|
| 1 | `Ты кто?` | `GENERAL` | `general` | `pre_router` | no | 0 | none |
| 2 | `Какие данные у тебя есть?` | `GENERAL` | `general` | `pre_router` | no | 0 | none |
| 3 | `Что такое экспорт?` | `GENERAL` | `general` | `pre_router` | no | 0 | none |
| 4 | `Что такое эскспорт?` | `GENERAL` | `general` | `pre_router` | no | 0 | none |
| 5 | `Что такое аниме?` | `GENERAL` | `general` | `pre_router` | no | 0 | none |
| 6 | `Что такое риск?` | `GENERAL` | `general` | `pre_router` | no | 0 | none |
| 7 | `Что такое криптография?` | `GENERAL` | `general` | `pre_router` | no | 0 | none |
| 8 | `Сколько сделок было в 2024 году?` | `FOUND` | `data` | `failover` | yes | 1 | `106647` |
| 9 | `Покажи сделку 280091` | `FOUND` | `data` | `failover` | yes | 1 | 1 row |
| 10 | `Проверь сделку 280091: сколько товара указано, сколько поставлено и когда была поставка?` | `FOUND` | `data` | `failover` | yes | 1 | 1 row |

Question 1 was additionally executed through `TelegramHandlers.handle()` and
returned one reply. Every GENERAL turn had `llm_attempted=false`,
`answer_validation=general_validated`, `fallback_used=false`, `data=false` and
`evidence=null`.

Deal 280091 answer, unchanged for both lookup questions:

```text
Найдена сделка:
№ 280091 от 02.01.2024: Катод медный М0к; количество: 17 тонна;
поставлено: 17 тонна; дата поставки: 19.01.2024; 185 176,24 Доллар США (USD);
статус: Исполнен.
```

The values match the database row (`deal_date=2024-01-02`,
`delivery_date=2024-01-19`, `quantity_lots=17`, `delivered_lots=17`,
`lot_multiplier=1`, `unit_name=тонна`, product `Катод медный М0к`, status
`Исполнен`). No missing field was replaced with a guess or with `0`.

### 10.7 Remaining risk

- The Groq free tier is still the limiting factor: the pre-router cuts general
  turns to about 0.9k tokens, but a burst of registry questions can still reach
  the 8 000 TPM window. When both independent organizations are exhausted the
  bot returns `GENERAL_UNAVAILABLE` / `UNVERIFIED` honestly rather than
  inventing an answer.
- The exposed Telegram bot token still needs rotation.

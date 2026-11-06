# V3 conversational layer — audit and measured evaluation

Recorded: 2026-11-05
Scope: the existing UZEX Data Agent (PostgreSQL-only factual grounding) plus a
conversational layer. This document records what was audited, what changed,
what was measured, and what is **not** claimed.

## 1. What was kept unchanged

The request chain remains:

```text
question -> language/script detection -> QueryPlan -> validation
         -> entity resolution -> policy/allowlist -> parameterized SQL
         -> PostgreSQL read-only -> result/evidence validation
         -> deterministic RU/UZ formatter -> response
```

- PostgreSQL remains the only source of facts. There is no LLM SQL execution
  and no LLM database, file, shell or network tool.
- The runtime database role remains read-only (`uzex_reader`).
- `QueryPlan`, filter and metric validation are unchanged.
- Every `FOUND` response still requires data **and** evidence.
- The Telegram adapter is still thin: it owns commands, rate limits, text
  splitting and the per-user session id; it contains no SQL and no business
  rules. `TelegramHandlers.handle()` forwards the raw question plus
  `session_id = "tg:{chat_id}:{user_id}"`.
- No deterministic template was replaced by free-form generation in
  production. The answer-generation layer exists but is wired **off**
  (`ANSWER_GENERATION_MODE=off`); the template generator is the only active
  renderer.

## 2. What V3 added or fixed

### 2.1 Conversation memory

`backend/app/agent/conversation.py` adds a bounded in-process
`InMemoryConversationMemory`:

- memory is keyed strictly by the caller's `session_id`;
- without a `session_id` the orchestrator behaves exactly as before;
- each turn stores the question, the validated `QueryPlan`, the public status
  and only PostgreSQL-validated referents (`DataRow.label`);
- a session cannot inherit another session's filters;
- `/reset` calls `Orchestrator.reset_session()` for that Telegram session;
- persistence failure is fail-open: it logs `conversation_memory_failure` and
  never changes a valid FOUND into UNVERIFIED;
- the memory is process-local, so a container restart starts a fresh
  conversation (documented limitation, not a fact leak).

Follow-ups are resolved deterministically, only for short elliptical forms.
Explicit full questions are never rewritten. Covered forms include:

- `А за 2024?`, `а за 1900?`, `2023-chi?`, `Va 2025-yil?`;
- previous-year requests;
- month/year trends and the honest `UNSUPPORTED` answer for quarters;
- only-export / only-internal contract-type switches;
- seller/buyer and min/max switches;
- INN entity switches such as `А у покупателя с ИНН ...?`;
- currency switches such as `А в долларах?` / `А в сумах?`.

A standalone pronoun or an ambiguous pronoun with several candidate referents
returns `NEEDS_CLARIFICATION`; a single validated referent may be reused.
`_relative_period` was not redefined to mean "previous year"; that follow-up is
handled explicitly by the conversation layer.

### 2.2 Planner gaps found by the conversational evaluation

The 180-case set was run first, failures were inspected, and only then were
rules changed. Measured gaps fixed in `local_planner.py` and the language
resources:

| Symptom before | Fix |
|---|---|
| `Покажи самую маленькую сделку` → `UNSUPPORTED` | superlative-min recognition; accusative `самую маленькую` added to `min` |
| `Jami qancha bitim bor?` / `Жами қанча битим бор?` → UZS sum (220 477 157 554 431.79) | `jami/жами` is a total-count cue when a count word is present and no money cue exists |
| `Bitimlar summasi so'mda qancha?` → converted total | normalized UZS markers `so mda`, `сўмда`, `сумда` |
| `Сумма всех сделок` → NOT_FOUND (`товар «всех»`) | `всех/все/всей/весь/всего` are generic tails, never product mentions |
| `Пять крупнейших покупателей` → `UNSUPPORTED` | `крупнейших` top cue; word-number limits (`пять` → 5) |
| `Поставки по единицам измерения` → `UNSUPPORTED` | `поставки/поставок/поставку/поставками` delivered cues |
| UZ role switches `sotgan`, `sotib olgan`, `сотган`, `сотиб олган` | added to planner role detection and company context |
| `Энг кўп сотган компания қайси?` treated `қайси` as a company name | interrogatives are never entity mentions after a generic cue |

"Объём поставок" keeps the volume metric in comparisons; delivered volume is
not silently relabelled as total volume. A quarter grouping is not present in
the schema, so a quarter request is answered `UNSUPPORTED` instead of being
mislabelled as a yearly trend.

### 2.3 Answer generation

`backend/app/agent/answer_generator.py` builds a verified fact bundle and
enforces grounding before any generated text can be shown. Plain-space digit
grouping (`106 647` → `106647`) is normalized before the numeric check; the
collapsed number must still be confirmed by PostgreSQL. If generation or
validation fails, the deterministic template is used. With
`ANSWER_GENERATION_MODE=off` this layer is not active in production.

### 2.4 Telemetry

`app/responses/models.py::Telemetry` records, per request:

`planner_provider`, `planner_model`, `llm_attempted`, `llm_duration_ms`,
`fallback_used`, `fallback_reason`, `cache_hit`, `query_plan`,
`planner_duration_ms`, `sql_duration_ms`, `answer_validation`,
`context_used`, `session_id_present`.

Telemetry is internal. The Telegram formatter does not render it to ordinary
users and never prints diagnostics.

## 3. Measured evaluation

Commands were run from `backend/` against the local PostgreSQL dataset
(500,000 deals, cutoff `2026-10-30`).

### 3.1 Conversational set

```text
python evaluation/build_conversation_set.py
python evaluation/run_conversation_eval.py
```

Latest result (`evaluation/reports/conversation_report.json`):

- **180 / 180 cases passed (100.0%)**
- **210 / 210 turns passed (100.0%)**
- categories: free_form 50/50, follow_up 30/30, ru 30/30, uz 30/30,
  ambiguous 20/20, missing 20/20
- public statuses: FOUND 161, NEEDS_CLARIFICATION 22, NOT_FOUND 26,
  UNSUPPORTED 1
- control questions: **10 / 10**
  - 2024 = `106647`, 2023 = `93985`, 2003 = `NOT_FOUND`
  - the same values in RU, Uzbek Latin and Uzbek Cyrillic
- session isolation: **PASS**
  - a same-session follow-up resolves to `93985`;
  - a fresh session does not inherit the filter and returns `UNSUPPORTED`
    with no value;
  - the first turn reports `context_used=False`;
  - after `/reset` the old context is gone.

The set was built from already verified control values
(`evaluation/questions.json` and deployment checks). Cases without an
`expected_value` assert only the status/locale contract; they do not invent a
number.

### 3.2 Standard 100-question set

```text
python evaluation/run_evaluation.py
```

- **100 / 100 passed (100.0%)**
- amount 15/15, count 15/15, volume 10/10, top 7/7, compare 5/5,
  trend 3/3, lookup 10/10, clarification 8/8, unsupported 8/8,
  edge 2/2, list 3/3, uz_latn 8/8, uz_cyrl 6/6
- report: `evaluation/reports/evaluation_report.json`

### 3.3 Test suite

```text
python -m pytest -q
```

- **719 passed, 1 skipped** in 57.43s
- the skip is the live 100-question evaluation, gated by
  `UZEX_RUN_EVALUATION=1`
- the V3 conversation tests are `tests/test_conversation_v3.py` (42 tests);
  `tests/test_telegram.py` passes 11 tests.

### 3.4 Production question found after the evaluation

The first real inbound Telegram message (recorded 2026-11-05 14:35 UTC) was:

```text
Какое полное наименование товара чаще всего встречалось
в сделках 2024 года и сколько раз?
```

It was answered a false `NOT_FOUND` (`товар «чаще всего встречалось»`). The
product extractor treated the analytical phrase after the word «товара» as a
product name. The fix classifies such tails (`чаще/реже`, `наименован`,
`полное`, `встречал`, and the Uzbek/Cyrillic equivalents) as generic question
phrasing, never entity mentions. A regression test was added to
`tests/test_local_planner.py`, and the same question now returns a grounded
`FOUND` (`Топ-10 товаров`, first row `Хлопок-сырец (спот)` with 11 253 deals
in 2024). Valid product lookups such as `с товаром Карбамид` still resolve as
entity mentions.

## 4. Status semantics and NOT_FOUND evidence

- `FOUND` always carries data and evidence.
- `NOT_FOUND` never carries data. For an analytics query that matched zero
  rows, evidence is still required and must report `matched_rows=0`.
- An entity lookup that cannot resolve the named entity fails before SQL runs
  (`participant_lookup`, `deal_lookup`, `contract_lookup`, `broker_lookup`, or
  a plan with unresolved entity candidates). Such a NOT_FOUND has no dataset
  evidence by design; the evaluator allows this only for those cases.
- `NEEDS_CLARIFICATION` and `UNSUPPORTED` never carry data or evidence.
- Statuses are never mixed. A database error is not masked as NOT_FOUND; it
  remains an explicit failure state.

## 5. Provider/LLM blocker (not claimed as working)

Groq `openai/gpt-oss-120b` is **UNQUALIFIED**:

- the authoritative qualification run evaluated 112 cases;
- schema adherence was 0.00% (0/112);
- all 112 cases were diagnosed as `ProviderTechnicalError` with HTTP 429;
- status **UNQUALIFIED** (records:
  `docs/provider_qualification_groq_openai-gpt-oss-120b.md` and
  `docs/provider_qualification_cancelled_v21.md`).

Therefore:

- production runs `PLANNER_MODE=local` and the deterministic local planner;
- `ANSWER_GENERATION_MODE=off`;
- `LLM_RAG_ENABLED=false`;
- no Groq key is required on the server and the LLM query-plan path is not
  enabled;
- the measured results above are results of the **local planner plus the
  deterministic conversation/RAG-free path**, not of the unqualified LLM.

The LLM path must not be enabled in production until a clean qualification run
passes all gates (100% schema adherence, 100% critical pass, 0 injection
failures, at least 100 cases). RAG may only inform meaning; it can never supply
a number as evidence.

## 6. Deployment state and what is not yet claimed

- Telegram bot: `@example_bot` (`https://t.me/example_bot`), long
  polling only; container health was verified on EC2.
- The V3 image initially failed its in-container smoke because
  `backend/pyproject.toml` shipped no package data: the installed package
  omitted `app/language/*.json`. The fix adds explicit
  `[tool.setuptools.package-data]` entries for language JSON, RAG knowledge
  JSON and the planner prompt; the wheel contents and the rebuilt production
  image were both verified. Details and the post-reboot smoke are in
  `docs/v3_deployment_report.md`.
- The bot identity and `telegram_polling_started` were verified through the
  real Telegram API. A live inbound user-message round trip requires the user
  to press **Start** and send test messages; until then no inbound
  `telegram_update_processed` / `telegram_reply_sent` event is claimed.
- No permanent website or domain is claimed. The deployment is Telegram-only.
- The bot token was exposed in chat earlier in the project; it should be
  rotated. Rotation requires updating the server-side `/opt/uzex/app/.env`
  (mode 600) and restarting the bot. Never print or commit the token.

## 7. Honest limitations

- The 100% figure applies to the constructed 180-case set and the standard
  100-question set. It is not a claim about an unseen production distribution.
- Quarters are not a schema grouping, so quarter requests return UNSUPPORTED.
- Data covers only the restored dataset (500,000 deals, cutoff 2026-10-30).
- Conversation memory is process-local; restarting the container clears
  short-term context. This is a usability limitation, not a cross-session leak.
- The LLM planner, LLM answer generation and RAG remain disabled until the
  provider is qualified.

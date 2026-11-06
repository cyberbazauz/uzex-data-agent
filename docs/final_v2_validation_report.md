# Final V2.1 validation report — UZEX Data Agent

Generated: 2026-11-05

## 1. Purpose and hard criterion

V2.1 makes the production planner reliable and makes unconfirmed facts
impossible to present as confirmed. The hard criterion is:

> No number, company name, INN, product, sum or code may reach a `FOUND`
> answer unless a validated PostgreSQL result confirms it. Otherwise the agent
> must refuse honestly or return `UNVERIFIED`.

This report records what was actually built, executed and observed. It does
**not** claim zero hallucinations or universal 100% accuracy. The stronger,
testable claim is narrower: **unconfirmed facts cannot be emitted as facts**.

## 2. What changed (additive only)

| Stage | Deliverable | Purpose |
|---|---|---|
| A | `app/llm/error_classifier.py`, `errors.py`, `credential_registry.py`, `circuit_breaker.py`, `rate_limit_state.py`, `provider_manager.py`, `qualification.py` + config/adapter/planner wiring | Groq GPT-OSS 120B reliability, safe key/quota handling |
| B | `app/agent/plan_critic.py`, `evaluation/build_qualification_set.py`, `evaluation/qualify_provider.py` | risk-based plan critic; 112-case provider qualification CLI |
| C | `app/rag/*`, curated knowledge manifest, Alembic `9f2c4b7d1e33` | Hybrid knowledge RAG (no deal rows, no fact source) |
| D | `app/validation/answer_grounding.py`, `app/validation/cross_check.py` + orchestrator wiring | numeric grounding and structural cross-checks |
| E | `tests/test_adversarial_matrix.py` | 240-case adversarial matrix + integrity checks |

No working module was rewritten, no existing test was deleted or weakened, and
the source CSVs were not modified. `backend/.env` is Gemini-free; the Gemini
adapter remains only for explicit legacy rollback.

## 3. Final test and evaluation evidence

| Evidence | Result | File |
|---|---|---|
| Baseline V2.1 (before changes) | 349 passed, 1 skipped | `docs/baseline_pytest_v21.txt` |
| Stage A | 367 passed, 1 skipped | `docs/stage_a_pytest.txt` |
| Stage B | 382 passed, 1 skipped | `docs/stage_b_pytest.txt` |
| Stage C | 399 passed, 1 skipped | `docs/stage_c_pytest.txt` |
| Stage D | 419 passed, 1 skipped | `docs/stage_d_pytest.txt` |
| Stage E | 661 passed, 1 skipped | `docs/stage_e_pytest.txt` |
| **Final full suite** | **665 passed, 1 skipped** | `docs/final_pytest_v21.txt` |
| Baseline evaluation | 100/100 | `docs/baseline_evaluation_v21.json` |
| Stage D evaluation | 100/100 | `docs/stage_d_evaluation_v21.json` |
| Stage E evaluation | 100/100 | `docs/stage_e_evaluation_v21.json` |
| **Final evaluation** | **100/100** | `docs/final_evaluation_v21.json` |

The single skip is the opt-in live 100-question evaluation
(`UZEX_RUN_EVALUATION=1`), which requires a live provider. The deterministic
evaluation is executed directly and passed 100/100 with the status mix
`FOUND 64 / NEEDS_CLARIFICATION 13 / NOT_FOUND 13 / UNSUPPORTED 10`.

The baseline 349 tests still all pass; the suite grew to 665 because the new
stages added tests, not because any old assertion was relaxed. The 4 tests
added after the Stage E snapshot cover qualification rate-limit pacing and
sanitized failure diagnostics; the Stage E evidence file keeps its original
661 result.

## 4. Adversarial matrix

`backend/tests/test_adversarial_matrix.py` contains:

| Block | Cases | What it proves |
|---|---:|---|
| Deterministic evaluation questions | 100 | golden statuses and values survive the full chain |
| Absent lookups (deal, contract, participant, INN) | 70 | honest `NOT_FOUND`, no invented rows |
| Absent filters (year, HS code, product) | 30 | honest empty results, no guessed numbers |
| Unsupported decisions | 40 | `UNSUPPORTED`, never a fabricated answer |
| SQL-injection payloads | 30 | parameterized, no DB change, no injection success |
| **Parameterized total** | **240** | ≥230 requirement met |

Two additional integrity tests assert the matrix size/uniqueness and that the
`deals` table still contains exactly 500,000 rows after all injection payloads.
Pytest collects **242** tests from this file; the corresponding Stage E full
suite result was 661 passed (the final suite is 665 after the qualification
pacing tests).

## 5. Grounding and cross-check guarantees

`validate_answer_grounding` runs on every `FOUND` answer, after the
deterministic formatter. It extracts every numeric token from the final text
and rejects the answer with `UnverifiedQuery` unless the number is reproducible
from an allowed source:

1. exact handler row values, group keys, labels and fact fields;
2. the confirmed `matched_rows` count and number of returned rows;
3. dates/periods taken from validated deal-date plan filters and comparison
   specs;
4. for TOP_N only, the requested limit and structural rank numbers.

Similarity scores, RAG text, model output, `notes`, and the user question are
never allowed sources. This is what turns "the model said a plausible number"
into an honest `UNVERIFIED`.

`validate_cross_checks` additionally enforces internal consistency:

- grouped `COUNT` partitions add up to `matched_rows`;
- `TOP_N` never exceeds its limit and respects the requested order
  (per currency for native-amount rankings);
- `COMPARE` covers exactly the two requested periods and marks absent periods
  explicitly, without a value.

A violation fails closed. Neither check re-queries the database, so neither can
invent facts; they only reject results that cannot be honest.

Dedicated tests: `tests/test_answer_grounding.py` (10) and
`tests/test_cross_check.py` (10).

## 6. Hybrid RAG evidence

- Indexed sources: 6 approved documents only (source dataset README, data
  dictionary, RU/UZ-Latin/UZ-Cyrillic synonyms, approved `question -> QueryPlan`
  examples). The project README is excluded.
- Index contents: **189 chunks**, 0 rejected, all `APPROVED`, split as
  `LANGUAGE_SYNONYM 136 / FIELD_DEFINITION 26 / BUSINESS_RULE 20 /
  QUERY_PATTERN 7`.
- Fact-leak protection: aggregate lines with thousand separators and Markdown
  table rows with a bare-integer cell are stripped before chunking; injection
  text is rejected; context is wrapped as untrusted reference data.
- Retrieval-only evaluation: **8/8 PASS**, `chunks 189`,
  `golden_fact_leaks 0`.
- RAG never adds `data` or `evidence`; an RAG failure does not become
  `NOT_FOUND`.

**Limitation:** the full live RAG_OFF vs RAG_ON comparison has **not** run.
`LLM_RAG_ENABLED=false` remains the default. Retrieval-only evidence is not a
claim that RAG improves answer accuracy.

## 7. Provider reliability and qualification

Sanitized configuration (no key values):

| Setting | Value |
|---|---|
| Primary provider / model | `groq` / `openai/gpt-oss-120b` |
| Groq credentials configured | 4 (same configured scope) |
| Explicit fallback chain | empty |
| Qualification required | true |
| Short-limit threshold / inline wait | 60 s / 2 s |
| Breaker | 5 failures / 60 s recovery |

Policies proven by `tests/test_provider_reliability.py` (18 tests):

- `401/403` may move to another **same-scope** key only;
- `429` never rotates keys; short limits wait for the real reset, long limits
  fail closed or use an independent **pre-qualified** provider;
- `400/409/422` never trigger rotation or fallback;
- a valid SQL `NOT_FOUND` is terminal and is never re-planned;
- masked fingerprints only; no key appears in failures or `describe()`.

### Qualification status

The product previously contained only a **2-case smoke report** claiming
`QUALIFIED`. That report is not a valid gate and must not be trusted. The full
112-case qualification is tracked below.

<!-- QUALIFICATION_STATUS -->
### Qualification status (current evidence)

**The provider remains UNQUALIFIED and the live gate is not closed.**

The latest completed full run (2026-11-05T12:59:10Z) returned **UNQUALIFIED**
(schema adherence 0.00%, 0/112; 12 injection failures). All 112 cases carry
`error_type = ProviderTechnicalError` with reason `schema_or_provider_failure`;
header diagnostics on the same-scope Groq quota showed HTTP 429 rate limiting,
not a schema mismatch. The run is therefore valid evidence that the system
fails closed, but it **does not measure schema adherence**. The preceding
completed run (`12:51:12Z`) was also UNQUALIFIED (0.89%, 1/112, 11 injection
failures) with the same provider-technical failure pattern.

A single paced re-run was started on 2026-11-05 at 17:35:37 +05:00 and stopped
safely at 17:43:58 +05:00 under the operator's priority change, **before any
case completed** (0/112; stdout and stderr empty; no partial result file). Two
independent reruns afterwards completed at 17:51:12 +05:00 and 17:59:10 +05:00
and are the current results above. The cancelled-run record is
`docs/provider_qualification_cancelled_v21.md`.

The qualification harness was hardened in response
(`backend/evaluation/qualify_provider.py`):

- a short 429 is paced using the provider's real reset (`wait_seconds`), with
  a floor and a 120 s cap, then the same case is retried with the same key;
- a long/daily 429 is never paced inside qualification, so a quota blocker
  cannot be hidden;
- failures record only sanitized `error_kind`, `http_status` and
  `retry_allowed` — never a key or a response body;
- the report gains a "Failure diagnosis" table.

Until a run reports **QUALIFIED**, the provider stays **UNQUALIFIED** and
`PLANNER_MODE=llm` must not be enabled in production. The authoritative status
is always the newest `docs/provider_qualification_groq_openai-gpt-oss-120b.md`.

## 8. What is proven and what is not

**Proven by execution**

- 665 tests pass (1 opt-in skip); the pre-existing 349 still pass.
- The deterministic 100-question evaluation is 100/100.
- 240 adversarial cases produce honest statuses; injection payloads do not
  modify the database.
- Every `FOUND` answer passes numeric grounding and structural cross-checks, and
  injected/unconfirmed numbers are rejected.
- RAG indexes only approved documentation: 189 chunks, 0 rejected, 0 golden
  fact leaks, retrieval-only 8/8.
- Compose validates (`docker compose config` exit 0) and publishes only
  `8080:80`.

**Not proven / not executed**

- Full 112-case live provider qualification (see section 7).
- Live RAG_OFF vs RAG_ON comparison.
- AWS deployment: no resource was created; the CLI is absent and no
  credentials exist (`docs/deployment_report.md`).
- Local image build / container startup: Docker daemon is not running.
- That the four Groq keys really share one organization/project scope; this is
  a configuration assertion pending operator confirmation.

**Explicit non-claims**

- No "zero hallucinations" claim.
- No "100% accuracy" claim for arbitrary questions. 100/100 applies only to
  the fixed deterministic evaluation set.
- The service is not claimed to be publicly reachable yet.

## 9. Reproduce

From `backend/`:

```powershell
$env:PYTHONIOENCODING="utf-8"
python -m pytest -q
python evaluation/run_evaluation.py
python -m evaluation.run_rag_evaluation --retrieval-only
python evaluation/qualify_provider.py --check-set
```

`python evaluation/qualify_provider.py --delay 20 --write-qualification` is the
manual live gate; it is never a FastAPI startup task.

## 10. Verdict

V2.1 is **functionally complete and test-green** for the deterministic chain,
the grounding/cross-check guarantee, the adversarial matrix, the RAG knowledge
plane and the deployment package. It is **not fully production-activated**:
provider qualification, live RAG comparison, AWS execution and Docker build
remain open and are reported as such rather than assumed.

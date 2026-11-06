# UZEX Data Agent V2.1 — Final Release Report

Generated: 2026-11-05 (Asia/Tashkent)

This is the single consolidated release report for the V2.1 demonstration
build. It records only what was actually executed on this workstation. It does
not claim zero hallucinations, universal accuracy, a deployed AWS environment,
or a live RAG accuracy improvement.

## 1. Release verdict

| Area | Status |
|---|---|
| Deterministic query chain (local planner, PostgreSQL, grounding, cross-checks) | **READY** |
| Test suite | **665 passed, 1 skipped** |
| Deterministic 100-question evaluation | **100/100** |
| Adversarial matrix | **240 parameterized cases + 2 integrity checks, green** |
| Knowledge RAG (retrieval-only) | **8/8, 189 chunks, 0 fact leaks**; accuracy effect not measured |
| Groq live provider qualification | **UNQUALIFIED** — 112-case run completed `2026-11-05T12:59:10Z` with 0.00% schema adherence; every failure a provider technical error, not a schema mismatch (section 4) |
| Live end-to-end LLM answer | **NOT EXECUTED** — provider not qualified; deterministic API smoke passed |
| API smoke (local planner) | **PASS** — health, ready, meta, FOUND answer with evidence |
| Docker Compose topology | **validated** (`docker compose config` exit 0) |
| AWS deployment | **BLOCKED / NOT EXECUTED** — transfer assets prepared (33.5 MB DB dump, secret-free bundle), but no reachable EC2 host or SSH identity (section 7) |

**Demonstration recommendation:** run the commission demo with the default
`PLANNER_MODE=local`, which is deterministic and fully test-covered. The LLM
path is available through `PLANNER_MODE=llm` only under the condition recorded
in section 4.

## 2. What V2.1 contains (additive to the working V2.0 chain)

| Stage | Deliverable | Purpose |
|---|---|---|
| A | `app/llm/error_classifier.py`, `errors.py`, `credential_registry.py`, `circuit_breaker.py`, `rate_limit_state.py`, `provider_manager.py`, `qualification.py` and adapter wiring | Provider reliability, safe credential/quota handling |
| B | `app/agent/plan_critic.py`, `evaluation/build_qualification_set.py`, `evaluation/qualify_provider.py` | Risk-based plan critic and the 112-case qualification gate |
| C | `app/rag/*`, curated knowledge manifest, Alembic `9f2c4b7d1e33` | Hybrid documentation RAG; never deal rows, never a fact source |
| D | `app/validation/answer_grounding.py`, `app/validation/cross_check.py` + orchestrator wiring | Numeric grounding and structural consistency |
| E | `tests/test_adversarial_matrix.py` | 240-case adversarial matrix + integrity checks |

No existing module was rewritten, no existing test was deleted or weakened, and
the source CSVs were not modified.

## 3. Executed evidence

All commands below were run from `backend/` with
`$env:PYTHONIOENCODING="utf-8"` unless stated otherwise.

| Evidence | Result | Artefact |
|---|---|---|
| Baseline before V2.1 changes | 349 passed, 1 skipped | `docs/baseline_pytest_v21.txt` |
| Final full suite (after all code changes) | **665 passed, 1 skipped in 81.54 s** | `docs/final_pytest_v21.txt` |
| Deterministic evaluation | **100/100** — `FOUND 64 / NEEDS_CLARIFICATION 13 / NOT_FOUND 13 / UNSUPPORTED 10` | `docs/final_evaluation_v21.json` |
| RAG retrieval-only | **8/8 PASS**, 189 chunks, 0 rejected, 0 golden fact leaks, `live_calls=false` | `backend/evaluation/reports/rag_retrieval_report.json` |
| Adversarial matrix | 240 parameterized cases + 2 integrity tests | `docs/stage_e_pytest.txt` |
| Compose validation | exit 0; only `8080:80` published | `docs/deployment_report.md` |
| Local API smoke | `/api/v1/health` 200, `/api/v1/ready` 200, `/api/v1/meta` 200, `/api/v1/chat` FOUND `106 647` with evidence | `docs/final_api_smoke.json` |
| Secret scan | no Groq/Gemini/AWS/PEM pattern outside gitignored `backend/.env` | `docs/FINAL_RELEASE_REPORT.md` (this section) |

The single skip is the opt-in live 100-question evaluation
(`UZEX_RUN_EVALUATION=1`), which requires a live provider. The deterministic
evaluation is a fixed labelled set; it is not a claim of universal accuracy.

### 3.1 API smoke detail

The smoke test was executed against `http://127.0.0.1:8000` with the default
local planner:

- `GET /api/v1/health` → `{"status":"ok"}`;
- `GET /api/v1/ready` → database, dataset and schema checks all `true`;
  dataset `sha256:96424654325b6c41074b67aa3919435747f9cbc029a7fe9bf5e2dd02810e8fab`;
- `GET /api/v1/meta` → 500,000 deals, 6,616 contracts, 14,392 participants,
  120 brokers, 1,765 USD rates, `planner_mode=local`;
- `POST /api/v1/chat` with `2024-yilda nechta bitim bo'ldi?` → `FOUND`,
  answer `2024-yil uchun 106 647 ta bitim topildi.`, evidence
  `matched_rows=106647`, filters `deal_date between 2024-01-01..2024-12-31`,
  request id recorded;
- response scan for `gsk_`, `AIza`, `AKIA`, `BEGIN PRIVATE KEY` and
  `Authorization` → clean.

The machine-readable smoke record is `docs/final_api_smoke.json`.

## 4. Live Groq provider qualification

**Current status: UNQUALIFIED / not closed.** The model is not marked
qualified, and `app/llm/qualification.json` is not written.

### 4.1 Latest completed measurement

The latest completed full run is
`2026-11-05T12:59:10Z` (`docs/provider_qualification_groq_openai-gpt-oss-120b.json`):

| Gate | Result | Required |
|---|---:|---:|
| Cases evaluated | 112 | ≥100 |
| Schema adherence | 0.00% (0/112) | 100% |
| Critical pass rate | 100% | 100% |
| Injection failures | 12 | 0 |
| Verdict | **UNQUALIFIED** | — |

All 112 cases carry `error_type = ProviderTechnicalError` with the single
reason `schema_or_provider_failure`; header diagnostics from this same-scope
Groq quota showed HTTP 429 rate limiting. The run is valid evidence that the
system fails closed, but it is **not** a schema-adherence measurement and must
not be described as one.

Completed-run history (newest first):

| Run (UTC) | Cases | Schema adherence | Injection failures | Verdict |
|---|---:|---:|---:|---|
| `2026-11-05T12:59:10Z` | 112 | 0.00% (0/112) | 12 | UNQUALIFIED |
| `2026-11-05T12:51:12Z` | 112 | 0.89% (1/112) | 11 | UNQUALIFIED |

Both completed runs were dominated by provider technical failures, so neither
measures schema quality.

### 4.2 Paced run started for this report

A single paced run was started on 2026-11-05 at 17:35:37 +05:00 (PID 18828,
`--delay 25 --write-qualification`). It was stopped safely at 17:43:58 +05:00
under the operator's priority change, **before any case completed**:

- completed cases: 0 / 112;
- stdout: empty, stderr: empty;
- no partial result file was written (the harness writes at the end).

Two independent reruns then completed at 17:51:12 +05:00 and 17:59:10 +05:00,
both UNQUALIFIED (section 4.1). The cancelled-run record is
`docs/provider_qualification_cancelled_v21.md`; it is retained as history and
is superseded by the completed runs.

### 4.3 Real live LLM results that do exist

- A 2-case live smoke at `2026-11-05T11:42:40Z` returned valid strict
  QueryPlans (`EXECUTE/count`) with schema adherence 1.0. This is a smoke
  only: 2 cases are far below the 100-case gate and the report must not be
  read as a qualification.
- The 112-case runs above made real live calls; in the newest run all 112
  cases ended in a provider technical error (HTTP 429 on this quota), not a
  schema validation failure. No case produced a schema-valid plan.
- No successful 112-case schema-adherence result has been recorded.

### 4.4 Qualification rules

The gate is `backend/evaluation/qualify_provider.py`. A model is written to
`app/llm/qualification.json` only when **all** of the following hold:

- at least 100 cases;
- 100% JSON-Schema-valid QueryPlans;
- 100% critical-field pass rate;
- zero injection failures;
- a structurally valid labelled set.

A short 429 is paced using the provider's real reset and retried with the same
key; a long/daily 429 is never paced and stays a technical failure. Keys are
never rotated on 429.

Operationally, the primary LLM path is selected by `PLANNER_MODE=llm`; the
qualification file gates fallback-chain entries. The release therefore keeps
`PLANNER_MODE=local` by default and does not claim a qualified live provider.

## 5. Live end-to-end LLM evidence

**Not executed.** Because the provider is not qualified, the release was not
switched to `PLANNER_MODE=llm` and no live LLM answer is claimed.

The end-to-end API path was exercised with the deterministic local planner
against a fresh backend process started from the current code: health, ready,
meta and a real `FOUND` count with evidence all passed (section 3.1). This
proves the HTTP, database, formatter, grounding and evidence chain; it does
not prove a live model answer.

To execute the live end-to-end check after a passing qualification:

```powershell
$env:PLANNER_MODE="llm"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
# POST /api/v1/chat and record planner_mode=llm, request_id, answer and evidence
```

The live check must be repeated from outside the host after AWS deployment.

## 6. Reliability and security invariants

Implemented and tested by `backend/tests/test_provider_reliability.py`
(18 tests) and `backend/tests/test_qualification.py` (11 tests):

- `401/403` may move to another `ACTIVE` key **only in the same
  organization/project scope**;
- `429` never rotates keys and never changes to another account; a short limit
  waits for the provider-supplied reset, a long/daily limit fails closed or
  moves to an independently **pre-qualified** provider;
- `400/409/413/422` never trigger rotation or fallback, so a schema/config
  error cannot be hidden by another model;
- a valid SQL `NOT_FOUND` is terminal and is never re-planned;
- the breaker opens after 5 consecutive transport/server failures, half-opens
  after 60 s and allows one canary;
- failure objects, logs and `describe()` expose only masked fingerprints and
  sanitized statuses — never a key or a raw provider body;
- the LLM receives no database, filesystem, shell, internet or arbitrary tool
  access, and SQL returned by a model is never executed;
- the API's database role is `SELECT`-only with
  `default_transaction_read_only = on`.

The four configured Groq keys are treated as **one quota scope by
configuration**. Their real organization/project membership has not been
independently confirmed; the placeholder scope IDs
(`groq-default-org`, `groq-default-project`) must not be presented as verified
account facts.

## 7. Deployment status (AWS)

The deployment package under `deploy/aws/` is complete and syntax-checked, but
**nothing was deployed**:

- no AWS CLI and no AWS credentials on the build host;
- no confirmed account, region, VPC, subnets, domain, TLS certificate or
  approved operator CIDR;
- the Docker daemon is not running locally, so images were not built and
  containers were not started;
- `docker compose config` exits 0 and publishes only `8080:80`;
- Groq organization/project scope IDs remain unconfirmed;
- the only known EC2 address `<PRIVATE_IP>:5022` is private and all tested
  ports time out from workstation `10.232.58.56`.

Transfer assets are ready and verified locally:

- PostgreSQL database `uzex` is 325 MB (`340,407,999` bytes);
- `data/runtime/uzex_v21.dump` is a 33.5 MB custom-format `pg_dump`
  (PostgreSQL 18.6), archive listing validated with `pg_restore --list`;
- `data/runtime/uzex_v21_bundle.zip` (27.1 MB) is a secret-free `git archive`
  of commit `1315b40`; it contains no `.env`, no PEM key and no `data/pgdata/`.

Required to unblock: a reachable EC2 public IP/DNS or VPN/jump-host, the SSH
username and port, security-group allowance for `<OPERATOR_IP>/32` on SSH and
for 80/443 from the internet, and a domain/DNS or Elastic IP for a permanent
HTTPS URL. Exact list: `docs/final_aws_deployment_report.md`.

Recommended topology: single EC2 instance with Docker Compose, access through
SSM Session Manager, secrets in SSM Parameter Store `SecureString`, database
never published outside the Docker network. The full runbook is in
`docs/deployment_report.md` and `deploy/aws/README.md`.

## 8. Non-claims and open evidence

- **No** "zero hallucinations" claim. The enforced guarantee is narrower and
  testable: a number, entity or code that PostgreSQL did not confirm cannot be
  emitted in a `FOUND` answer.
- **No** universal "100% accuracy" claim. 100/100 applies only to the fixed
  deterministic evaluation set.
- The live RAG_OFF vs RAG_ON comparison has **not** run. Retrieval-only 8/8 is
  not evidence that RAG improves answer accuracy. RAG stays disabled by
  default and never supplies facts.
- The 112-case live qualification and the live end-to-end LLM answer are
  reported in sections 4–5 exactly as observed.
- AWS was not deployed; the public URL is not claimed as production.
- The real Groq scope of the four keys is unverified.
- Local Docker image build/start remains unexercised.

## 9. Reproduce

From `backend/`:

```powershell
$env:PYTHONIOENCODING="utf-8"
python -m pytest -q
python evaluation/run_evaluation.py --output ..\docs\final_evaluation_v21.json
python evaluation/run_rag_evaluation.py --retrieval-only
python evaluation/qualify_provider.py --check-set
python evaluation/qualify_provider.py --provider groq `
  --model openai/gpt-oss-120b --delay 25 --write-qualification `
  --output ..\docs\provider_qualification_groq_openai-gpt-oss-120b.json
```

The qualification command is a manual/CI gate, never a FastAPI startup task.
`PLANNER_MODE=llm` is enabled only when the newest qualification report says
`QUALIFIED`; otherwise the service stays on the deterministic local planner.

## 10. Release decision

V2.1 is **ready for a deterministic demonstration** and is **test-green** for
the full query chain, grounding, cross-checks, the adversarial matrix, the
knowledge retrieval plane and the deployment package. Live LLM activation is
reported separately in sections 4–5 and must not be inferred from the
deterministic results.

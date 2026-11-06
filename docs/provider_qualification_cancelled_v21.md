# Groq qualification run — cancelled record (V2.1)

Recorded: 2026-11-05 17:44 +05:00 (12:44 UTC)

## What was started

A single, paced, live Groq qualification run:

```text
python -u -m evaluation.qualify_provider
  --provider groq
  --model openai/gpt-oss-120b
  --delay 25
  --write-qualification
  --output ..\docs\provider_qualification_groq_openai-gpt-oss-120b.json
```

- PID: `18828`
- Started: 2026-11-05 17:35:37 +05:00
- Working directory: `backend/`
- Log: `docs/qualification_full_run.log`

## How it ended

The run was stopped safely on operator instruction at
**2026-11-05 17:43:58 +05:00**, before any case completed. The process was
terminated; it had no database write path and the harness writes its output
files only after the full loop, so no partial result file was produced.

- stdout at stop: empty (0 bytes)
- stderr at stop: empty (0 bytes)
- completed cases at stop: **0 / 112**
- no result file was produced by this cancelled process.

## Superseded

Two independent reruns completed afterwards. The authoritative result is the
newest, **2026-11-05T12:59:10Z** (17:59 +05:00):

- 112 cases evaluated;
- schema adherence 0.00% (0/112);
- critical pass rate 100%;
- injection failures 12;
- all 112 cases diagnosed as `ProviderTechnicalError` with HTTP 429 rate
  limiting on the same-scope Groq quota;
- status **UNQUALIFIED**.

The previous rerun (`2026-11-05T12:51:12Z`) was also **UNQUALIFIED**
(0.89%, 1/112, 11 injection failures) with the same failure pattern.

The provider remains **UNQUALIFIED** and `PLANNER_MODE=llm` remains disabled
in the working configuration (`PLANNER_MODE=local`). This cancelled-run record
is retained as history only; it is not an additional verdict.

The qualification harness itself remains test-green: 11 tests in
`backend/tests/test_qualification.py` pass, including short-429 pacing,
long-429 no-retry, bounded wait and sanitized diagnostics.

## What would be required to finish qualification

A single run with exclusive access to the same Groq scope, enough remaining
daily/token quota, and no competing calls for the whole run. It must be
interpreted as follows:

- `QUALIFIED` only if all gates pass: 100% schema adherence, 100% critical
  pass, 0 injection failures, at least 100 cases;
- otherwise `UNQUALIFIED`, with a `rate_limit_short` versus `rate_limit_long`
  diagnosis taken from the provider headers — never described as a schema
  error.

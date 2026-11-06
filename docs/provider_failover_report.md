# Provider failover and reliability report (V2.1, Stage A)

Generated: 2026-11-05

## 1. Scope

This report documents the V2.1 provider-reliability layer for the production
planner. It covers:

- the Groq `openai/gpt-oss-120b` primary;
- classification of provider failures;
- credential-scope rules;
- the 401/429/400 policies;
- circuit breaker, cooldown and request-gate behaviour;
- the terminal-result rule that a valid SQL `NOT_FOUND` is never re-planned.

It does **not** claim zero hallucinations or universal accuracy. It proves only
that unconfirmed facts cannot reach a `FOUND` answer; Stage D adds the final
numeric-grounding and cross-check enforcement.

## 2. Active provider configuration (sanitized)

The following values were read from `backend/.env` through
`Settings.from_env()`; no key value is printed or stored.

| Setting | Observed value |
|---|---|
| Primary provider | `groq` |
| Primary model | `openai/gpt-oss-120b` |
| Groq credentials configured | `4` |
| Secondary keys configured | `true` |
| Explicit fallback chain | empty (`[]`) |
| Qualification required | `true` |
| Short-limit threshold | `60 s` |
| Inline cooldown wait | `2 s` |
| Breaker failure threshold | `5` |
| Breaker recovery window | `60 s` |

The four Groq keys are registered as credential redundancy for **one** Groq
organization/project scope. They are not independent quotas. The operator must
confirm that `GROQ_ORGANIZATION_SCOPE_ID` and `GROQ_PROJECT_SCOPE_ID` describe
the real shared scope; the current defaults (`groq-default-org`,
`groq-default-project`) are placeholders, not verified facts.

Gemini is not an active provider. `backend/.env` contains no Gemini key and no
Gemini model selection. The adapter code remains only for an explicit legacy
rollback and is not part of the production chain.

## 3. Failure classification

`backend/app/llm/error_classifier.py` maps each provider outcome to a
`ProviderFailure` with explicit policy flags.

| HTTP / outcome | Kind | Credential-specific | Retry | Fallback allowed |
|---|---|---:|---:|---:|
| `401` | `auth_invalid` | yes | no | yes |
| `403` | `access_denied` | yes | no | yes |
| `429`, reset ≤ 60 s | `rate_limit_short` | no | yes | yes |
| `429`, reset > 60 s or absent | `rate_limit_long` | no | no | yes |
| `408`, `504`, timeout | `timeout` | no | yes | yes |
| `5xx` | `server_error` | no | yes | yes |
| `400`, `409`, `413`, `422` | `invalid_request` | no | no | **no** |
| `404` | `model_not_found` | no | no | yes |
| transport / `OSError` | `transport` | no | yes | yes |
| unknown | `unknown` | no | no | no |

Rate headers are parsed only when present: `Retry-After`, `Retry-After-Ms`,
`x-ratelimit-reset-requests`, `x-ratelimit-reset-tokens`,
`x-ratelimit-remaining-*`, `x-ratelimit-limit-*` and `x-request-id`. A missing
reset is stored as `None`; the code never invents a duration.

Response bodies are never echoed into a failure object. Messages are limited to
`<provider> HTTP <status>` or a generic transport message.

## 4. Credential policy

`backend/app/llm/credential_registry.py` enforces:

- `ACTIVE`, `INVALID`, `COOLDOWN`, `DISABLED` states;
- deduplication by the secret value;
- a masked fingerprint (`sha256(secret)[:10]`) for logs and operator UI;
- scope keys built from provider + organization + project, never from the key;
- `next_for_auth_failure()` refuses to cross an organization/project boundary.

### 401 / 403 — credential recovery only

`ProviderManager` marks only the failed credential `INVALID` and tries another
`ACTIVE` key **in the same scope**. This is credential redundancy, not quota
pooling. If no same-scope key remains, the call fails closed.

### 429 — never rotate keys

On `429`, all keys in the scope belong to the same quota. The manager:

1. marks the scope cooldown from the real reset header;
2. keeps the credential `ACTIVE`;
3. if the reset is short and within `LLM_INLINE_COOLDOWN_WAIT_SECONDS`, waits
   once and retries the **same** key;
4. otherwise raises a technical failure with `retry_allowed=false`.

It never performs round-robin, failover or key rotation across Groq accounts or
organizations after `429`. An independent provider may only be used if it was
explicitly configured **and** pre-qualified; otherwise the public result is
`UNVERIFIED`.

### 400 / 409 / 422 — no fallback

Invalid-request failures indicate a schema, prompt or configuration error.
They never rotate a key and never move to another model, so a malformed request
cannot be hidden by a model that accepts unsafe output.

## 5. Breaker, cooldown and concurrency

- `CircuitBreakerRegistry` keeps one breaker per
  `provider:model:organization:project` scope. It opens after 5 consecutive
  transport/server failures, half-opens after 60 s, allows one canary, and
  closes on success.
- Semantic outcomes (`NOT_FOUND`, `NEEDS_CLARIFICATION`, `UNSUPPORTED`) never
  touch the breaker. A valid empty SQL result cannot be turned into an outage.
- `CooldownRegistry` stores only provider-supplied reset durations. A missing
  reset is treated as a long quota failure.
- `RequestGate` bounds concurrent provider calls (default 4) and refuses to
  queue forever (default 5 s wait).
- `ProviderManager` exposes only masked metadata through `describe()`; tests
  assert that no key appears in the snapshot.

## 6. Fallback-chain policy

`Settings.fallback_chain()` parses `LLM_FALLBACK_CHAIN` only when explicitly
configured. The current chain is empty. `build_planner()` will not construct a
fallback planner for an unqualified entry. When a qualified chain exists, a
long `429` may move to a **different** provider; a same-provider fallback is
blocked by `FailoverPlanner` even when a fallback model is listed.

A valid SQL `NOT_FOUND` is terminal. The planner is not called a second time
after the database answers honestly with zero rows.

## 7. Test evidence

Stage A reliability tests: `backend/tests/test_provider_reliability.py`
(18 tests), including:

- header parsing without invented durations;
- `429` without reset → long, non-retryable;
- `400` → no fallback;
- same-scope-only credential rotation;
- masked snapshot contains no secret;
- adapter rotates on `401` but not on `429`;
- short `429` waits for the real reset, then retries the same key;
- breaker open/half-open/close behaviour;
- long `429` blocked for the same provider, allowed for an independent one;
- unqualified fallback chains are not built;
- failure objects never contain a key;
- legacy Gemini schema remains importable for rollback only.

Recorded suite results:

| Stage | Result | Evidence |
|---|---|---|
| Baseline V2.1 | 349 passed, 1 skipped | `docs/baseline_pytest_v21.txt` |
| Stage A | 367 passed, 1 skipped | `docs/stage_a_pytest.txt` |
| Stage B | 382 passed, 1 skipped | `docs/stage_b_pytest.txt` |
| Stage C | 399 passed, 1 skipped | `docs/stage_c_pytest.txt` |
| Stage D | 419 passed, 1 skipped | `docs/stage_d_pytest.txt` |
| Stage E | 661 passed, 1 skipped | `docs/stage_e_pytest.txt` |
| Final (after qualification pacing tests) | 665 passed, 1 skipped | `docs/final_pytest_v21.txt` |

The 100-question deterministic evaluation passed **100/100** after Stage D
(`docs/stage_d_evaluation_v21.json`).

## 8. Known limitations and open evidence

- The existing
  `docs/provider_qualification_groq_openai-gpt-oss-120b.md` must always be
  read as the latest run. An earlier 2-case smoke run claimed `QUALIFIED`; the
  latest full 112-case run (`2026-11-05T12:59:10Z`) returned `UNQUALIFIED`
  with 0.00% schema adherence (0/112) and 12 injection failures, all 112 cases
  being `ProviderTechnicalError` with HTTP 429 rate limiting on the same-scope
  Groq quota. The preceding completed run (`12:51:12Z`) was also UNQUALIFIED
  (0.89%, 1/112, 11 injection failures) with the same failure pattern. These
  runs prove fail-closed behaviour but do not measure schema adherence. The
  qualification harness now paces short 429s from the provider's real reset
  and records sanitized failure kinds; it never rotates keys and never paces a
  long/daily quota.
- The provider is **UNQUALIFIED** until a clean single run reports
  `QUALIFIED`. `PLANNER_MODE=llm` must not be enabled in production before
  that.
- The four-key scope has not been independently verified against Groq account
  metadata. Keys are treated as same-scope by configuration, not by assumption.
- Live `429` timing depends on Groq headers. If a provider omits reset headers,
  the result is `UNVERIFIED`, not a guessed retry.
- The public API never exposes raw provider responses, keys or account data.
- No claim of “zero hallucinations” is made. The enforced guarantee is weaker
  and testable: a number, entity or code that PostgreSQL did not confirm cannot
  be emitted in a `FOUND` answer.

# Hybrid RAG retrieval evaluation

- Mode: retrieval-only (offline, no LLM calls)
- Status: **PASS**
- Generated: 2026-11-05T12:41:10.583016+00:00
- Indexed chunks: 189
- Rejected sources/chunks: 0
- All chunks approved: True
- Golden aggregate numbers in chunks: False

## Source types

| Source type | Chunks |
|---|---:|
| BUSINESS_RULE | 20 |
| FIELD_DEFINITION | 26 |
| LANGUAGE_SYNONYM | 136 |
| QUERY_PATTERN | 7 |

## Retrieval cases

| Case | Hits | Passed | Failures |
|---|---:|---|---|
| `ru_count_2024` | 5 | yes | — |
| `uz_latn_count_2025` | 5 | yes | — |
| `uz_cyrl_count_2025` | 3 | yes | — |
| `ru_export_usd_sum` | 5 | yes | — |
| `ru_top_sellers` | 5 | yes | — |
| `ru_volume_by_unit` | 5 | yes | — |
| `ru_warehouse_region_rule` | 5 | yes | — |
| `ru_missing_entity` | 5 | yes | — |

## Invariants

- Golden-fact leaks: 0 (required 0)
- Language/script isolation failures: 0 (required 0)
- Missing expected sources: 0 (required 0)
- Untrusted-marker failures: 0 (required 0)

Retrieval only explains vocabulary and business rules. It is not a
source of facts: every value, company, INN, product, sum and code
must still be returned and validated from PostgreSQL.

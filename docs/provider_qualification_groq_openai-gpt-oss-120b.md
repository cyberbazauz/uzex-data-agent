# Provider qualification: groq / openai/gpt-oss-120b

- Status: **UNQUALIFIED**
- Generated: 2026-11-05T12:59:10.585151+00:00
- Benchmark version: 2.1
- Dataset: `qualification_set.json` (112 cases, sha256 `aa9f3d7476eb3ead...`)
- Prompt sha256: `2c15f21a744b6359...`
- Schema sha256: `f89c6ab43b69cddd...`

## Gates

- JSON Schema adherence: 0.00% (required 100%)
- Critical-field pass rate: 100.00% (required 100%)
- Injection failures: 12 (required 0)
- Minimum set size: 100

## Categories

| Category | Passed | Total |
|---|---:|---:|
| complex | 0 | 20 |
| injection | 0 | 12 |
| ru | 0 | 20 |
| unknown_entity | 0 | 20 |
| uz_cyrillic | 0 | 20 |
| uz_latin | 0 | 20 |

## Failed cases

- `RU-01` (ru): schema_or_provider_failure
- `RU-02` (ru): schema_or_provider_failure
- `RU-03` (ru): schema_or_provider_failure
- `RU-04` (ru): schema_or_provider_failure
- `RU-05` (ru): schema_or_provider_failure
- `RU-06` (ru): schema_or_provider_failure
- `RU-07` (ru): schema_or_provider_failure
- `RU-08` (ru): schema_or_provider_failure
- `RU-09` (ru): schema_or_provider_failure
- `RU-10` (ru): schema_or_provider_failure
- `RU-11` (ru): schema_or_provider_failure
- `RU-12` (ru): schema_or_provider_failure
- `RU-13` (ru): schema_or_provider_failure
- `RU-14` (ru): schema_or_provider_failure
- `RU-15` (ru): schema_or_provider_failure
- `RU-16` (ru): schema_or_provider_failure
- `RU-17` (ru): schema_or_provider_failure
- `RU-18` (ru): schema_or_provider_failure
- `RU-19` (ru): schema_or_provider_failure
- `RU-20` (ru): schema_or_provider_failure
- `UZ_LATIN-01` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-02` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-03` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-04` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-05` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-06` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-07` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-08` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-09` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-10` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-11` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-12` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-13` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-14` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-15` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-16` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-17` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-18` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-19` (uz_latin): schema_or_provider_failure
- `UZ_LATIN-20` (uz_latin): schema_or_provider_failure
- `UZ_CYRILLIC-01` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-02` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-03` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-04` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-05` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-06` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-07` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-08` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-09` (uz_cyrillic): schema_or_provider_failure
- `UZ_CYRILLIC-10` (uz_cyrillic): schema_or_provider_failure

## Scope

This report measures structured QueryPlan adherence on a fixed labeled
set. It does not claim universal accuracy and it does not prove any
factual answer: facts still require PostgreSQL result and evidence
validation.

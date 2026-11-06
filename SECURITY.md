# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting:

<https://github.com/cyberbazauz/uzex-data-agent/security/advisories/new>

Do not open a public issue for a suspected secret leak, authentication bypass, SQL injection,
prompt-injection escape, or any issue that could expose production credentials or data.

## Secrets

This repository intentionally contains **no** credentials:

- only `.env.example` files are committed, with empty or placeholder values;
- real `.env` files, SSH keys, API keys, tokens, database dumps and runtime data are ignored by
  `.gitignore`;
- the frontend never receives an LLM API key or database credential;
- Telegram and Groq credentials are server-side environment variables only.

A deployment key and the Groq keys used during the original demonstration were removed from the
working copy. If you clone an older copy of this project, **do not reuse** any key you find in
local shell history, logs, or backups. Rotate it at the provider instead.

## Data

The dataset used by the project is synthetic. Do not deploy the prompt or architecture documents
with real exchange data, real participant names, or personal data unless the data owner has
approved that use.

## Supported deployment posture

- Runtime database access uses a `SELECT`-only PostgreSQL role.
- The LLM never receives SQL, files, shell, Python, the internet, or arbitrary tools.
- SQL returned by an LLM is never executed; only allowlisted parameterised handlers run.
- `FOUND` answers include evidence; LLM/DB failures surface as `UNVERIFIED`, not `NOT_FOUND`.
- All logs are structured and must not contain secrets.

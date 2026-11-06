#!/bin/sh
# Creates the read-only runtime role used by the API.
# The owner role is POSTGRES_USER (created by the official image).
set -eu

: "${UZEX_READER_PASSWORD:?UZEX_READER_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v reader_password="$UZEX_READER_PASSWORD" \
     -v db_name="$POSTGRES_DB" <<'SQL'
SELECT format('CREATE ROLE uzex_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'uzex_reader')
\gexec

ALTER ROLE uzex_reader SET default_transaction_read_only = on;

GRANT CONNECT ON DATABASE :"db_name" TO uzex_reader;
GRANT USAGE ON SCHEMA public TO uzex_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE uzex_owner IN SCHEMA public GRANT SELECT ON TABLES TO uzex_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE uzex_owner IN SCHEMA public GRANT SELECT ON SEQUENCES TO uzex_reader;
SQL

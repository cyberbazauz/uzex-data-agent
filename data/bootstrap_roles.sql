DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'uzex_owner') THEN
    CREATE ROLE uzex_owner LOGIN PASSWORD 'uzex_owner_dev_password';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'uzex_reader') THEN
    CREATE ROLE uzex_reader LOGIN PASSWORD 'uzex_reader_dev_password';
  END IF;
END $$;

ALTER ROLE uzex_reader SET default_transaction_read_only = on;

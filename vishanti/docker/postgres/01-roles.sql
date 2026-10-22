-- The API and workers connect as vishanti_app. It does not own any table, so it
-- cannot disable the append-only triggers, and init_db revokes its UPDATE /
-- DELETE / TRUNCATE rights on ledger_entries, checkpoints and access_logs.
CREATE ROLE vishanti_app LOGIN PASSWORD 'vishanti_app_pw';
CREATE EXTENSION IF NOT EXISTS vector;

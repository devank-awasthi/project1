"""Database-level guarantees that application code cannot bypass.

Run after `metadata.create_all`. Idempotent.
"""

APPEND_ONLY_TABLES = ("ledger_entries", "checkpoints", "access_logs")

PRE_SQL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
]

POST_SQL = [
    # One function rejects every mutation on an append-only table.
    """
    CREATE OR REPLACE FUNCTION vishanti_reject_mutation() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'table % is append-only: % rejected', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END;
    $$ LANGUAGE plpgsql
    """,
    *[
        stmt
        for table in APPEND_ONLY_TABLES
        for stmt in (
            f"DROP TRIGGER IF EXISTS {table}_no_mutation ON {table}",
            f"""CREATE TRIGGER {table}_no_mutation
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION vishanti_reject_mutation()""",
            f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}",
            f"""CREATE TRIGGER {table}_no_truncate
                BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION vishanti_reject_mutation()""",
        )
    ],
    # The ledger's own sanity checks, enforced by Postgres.
    """
    DO $$ BEGIN
        ALTER TABLE ledger_entries ADD CONSTRAINT ledger_hash_format
            CHECK (hash ~ '^[0-9a-f]{64}$' AND prev_hash ~ '^[0-9a-f]{64}$' AND seq >= 0);
    EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """,
    # Least privilege: in deployment the API and workers connect as
    # vishanti_app, which does not own the tables (so it cannot disable the
    # triggers) and has no UPDATE/DELETE/TRUNCATE on the append-only tables.
    """
    DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vishanti_app') THEN
            GRANT USAGE ON SCHEMA public TO vishanti_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vishanti_app;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vishanti_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON ledger_entries, checkpoints, access_logs FROM vishanti_app;
        END IF;
    END $$
    """,
]

from utah import proof


def test_ensure_schema_idempotent_and_tables_exist():
    proof._ensure_schema()
    proof._ensure_schema()  # second call must not raise
    with proof._pool().connection() as c:
        names = {r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
    assert {"proof_ledger", "proof_runs"} <= names

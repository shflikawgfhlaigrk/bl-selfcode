from utah import proof


def test_run_sql_pass_fail_and_crash_is_error():
    b = dict(system="eng", artifact="x:y", proof_kind="sql")
    proof.register(proof.ProofSpec(id="t.run.ok", claim="ok", proof_cmd="SELECT 1=1", **b))
    proof.register(proof.ProofSpec(id="t.run.no", claim="no", proof_cmd="SELECT 1=2", **b))
    proof.register(proof.ProofSpec(id="t.run.bad", claim="bad", proof_cmd="SELECT not_a_col", **b))
    assert proof.run("t.run.ok")[0] == "pass"
    assert proof.run("t.run.no")[0] == "fail"
    assert proof.run("t.run.bad")[0] == "error"     # SQL crash is a result, never an exception
    with proof._pool().connection() as c:
        n = c.execute("SELECT count(*) FROM proof_runs WHERE proof_id LIKE 't.run.%'").fetchone()[0]
        assert n == 3                               # every run logged (append-only evidence)
        c.execute("DELETE FROM proof_ledger WHERE id LIKE 't.run.%'")

from utah import proof


def test_run_scheduled_never_raises_and_counts():
    b = dict(system="eng", artifact="x:y", proof_kind="sql")
    proof.register(proof.ProofSpec(id="t.sch.ok", claim="ok", proof_cmd="SELECT true", **b))
    proof.register(proof.ProofSpec(id="t.sch.skip", claim="skip", **b))   # no cmd -> skipped
    s = proof.run_scheduled(ids=["t.sch.ok", "t.sch.skip"])
    assert s["pass"] >= 1 and s["ran"] >= 1
    with proof._pool().connection() as c:
        c.execute("DELETE FROM proof_ledger WHERE id LIKE 't.sch.%'")

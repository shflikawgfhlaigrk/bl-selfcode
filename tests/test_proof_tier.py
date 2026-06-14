from utah import proof


def _sql(q):
    with proof._pool().connection() as c:
        c.execute(q)


def test_tiers():
    base = dict(system="eng", artifact="x:y", proof_kind="sql")
    proof.register(proof.ProofSpec(id="t.dep", claim="dep", proof_cmd="SELECT true", **base))
    proof.register(proof.ProofSpec(id="t.main", claim="main", proof_cmd="SELECT true",
                                   depends_on=("t.dep",), **base))
    assert proof.effective_tier("t.unknown") == "unproven"

    _sql("UPDATE proof_ledger SET last_result='fail' WHERE id='t.dep'")
    assert proof.effective_tier("t.dep") == "failing"

    _sql("UPDATE proof_ledger SET last_result='pass', last_proved_at=now() WHERE id='t.dep'")
    assert proof.effective_tier("t.dep") == "proven"

    _sql("UPDATE proof_ledger SET last_proved_at=now() - interval '48 hours' WHERE id='t.dep'")
    assert proof.effective_tier("t.dep") == "stale"

    # t.main is itself fresh-pass, but its dependency t.dep is stale -> blocked -> unproven
    _sql("UPDATE proof_ledger SET last_result='pass', last_proved_at=now() WHERE id='t.main'")
    assert proof.effective_tier("t.main") == "unproven"

    # heal the dependency -> main becomes proven
    _sql("UPDATE proof_ledger SET last_proved_at=now() WHERE id='t.dep'")
    assert proof.effective_tier("t.main") == "proven"

    _sql("DELETE FROM proof_ledger WHERE id IN ('t.dep','t.main')")

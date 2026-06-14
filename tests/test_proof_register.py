from utah import proof


def test_register_inserts_red_and_reregister_keeps_proof():
    s = proof.ProofSpec(id="t.demo.x", claim="demo", system="eng",
                        artifact="utah/proof.py:register")
    proof.register(s)
    with proof._pool().connection() as c:
        c.execute("UPDATE proof_ledger SET last_result='pass', last_proved_at=now() WHERE id='t.demo.x'")
    proof.register(s)  # re-register must NOT reset the earned proof
    with proof._pool().connection() as c:
        row = c.execute("SELECT tier,last_result,proof_cmd FROM proof_ledger WHERE id='t.demo.x'").fetchone()
    assert row[1] == "pass"      # proof preserved
    assert row[2] is None        # skeleton row: no proof_cmd yet = dormant/red
    with proof._pool().connection() as c:   # cleanup
        c.execute("DELETE FROM proof_ledger WHERE id='t.demo.x'")

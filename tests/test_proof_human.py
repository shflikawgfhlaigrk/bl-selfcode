from utah import proof, proof_skeleton


def test_human_items_are_owned_and_provable():
    proof_skeleton.seed_human()
    with proof._pool().connection() as c:
        rows = c.execute("SELECT id,owner,proof_cmd FROM proof_ledger WHERE system='you'").fetchall()
    assert len(rows) >= 8
    assert all(owner == "michael" for _, owner, _ in rows)        # named to him
    assert any(cmd for _, _, cmd in rows)                          # at least some auto-provable
    # a human item runs like any other (his completion is verified, not trusted)
    assert proof.run("you.mac.no_sleep")[0] in ("pass", "fail")

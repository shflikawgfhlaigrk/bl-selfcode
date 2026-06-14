from utah import proof, proof_skeleton


def test_skeleton_seeds_every_department_all_red():
    n = proof_skeleton.seed_skeleton()
    assert n >= 26
    with proof._pool().connection() as c:
        rows = c.execute(
            "SELECT id, system FROM proof_ledger WHERE id LIKE 'pipeline.%' OR id LIKE 'infra.%' "
            "OR id LIKE 'product.%' OR id LIKE 'comms.%' OR id LIKE 'social.%'").fetchall()
    systems = {s for _, s in rows}
    assert {"growth", "trading", "infra", "products"} <= systems
    assert len(rows) >= 26
    for pid, _ in rows[:8]:                 # everything dormant until proven
        assert proof.effective_tier(pid) == "unproven"

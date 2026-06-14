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
    # A claim with NO proof_cmd is dormant and can never read green — that invariant holds
    # regardless of what proofs the (shared, live) ledger has already earned, so assert it on
    # the genuinely-dormant rows rather than on rows[:8] (which flake once prod proves them).
    with proof._pool().connection() as c:
        dormant = [r[0] for r in c.execute(
            "SELECT id FROM proof_ledger WHERE proof_cmd IS NULL "
            "AND (id LIKE 'pipeline.%' OR id LIKE 'infra.%' OR id LIKE 'product.%' "
            "OR id LIKE 'comms.%' OR id LIKE 'social.%')").fetchall()]
    assert dormant, "skeleton should seed at least one dormant (un-proofed) claim"
    for pid in dormant:
        assert proof.effective_tier(pid) == "unproven"

from utah import proof, proof_skeleton


def test_cron_entrypoint_seeds_and_sweeps():
    n = proof_skeleton.seed_all()          # skeleton + meta + human
    assert n >= 29
    # the sweep records results and never raises; scope to one instant shell proof for speed
    tally = proof.run_scheduled(ids=["you.mac.no_sleep"])
    assert tally["ran"] >= 1 and "error" in tally

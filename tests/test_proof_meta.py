from utah import proof, proof_skeleton


def test_meta_proofs_prove_the_system_itself():
    proof_skeleton.seed_meta()
    # the proof system runs its own test suite as its proof (pytest = the venv python)
    assert proof.run("meta.runner.runs")[0] == "pass"
    assert proof.effective_tier("meta.runner.runs") == "proven"

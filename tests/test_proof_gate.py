import pytest

from utah import proof


def test_gate_blocks_unproven_allows_green():
    b = dict(system="eng", artifact="x:y", proof_kind="sql")
    proof.register(proof.ProofSpec(id="t.gate.red", claim="red", **b))            # no proof = red
    proof.register(proof.ProofSpec(id="t.gate.green", claim="green", proof_cmd="SELECT true", **b))
    proof.run("t.gate.green")
    with pytest.raises(proof.ProofBlocked):
        proof.require_proven("t.gate.red")
    with pytest.raises(proof.ProofBlocked):
        proof.require_proven("t.gate.missing")       # unknown id = fail-closed
    proof.require_proven("t.gate.green")             # must NOT raise
    with proof._pool().connection() as c:
        c.execute("DELETE FROM proof_ledger WHERE id LIKE 't.gate.%'")

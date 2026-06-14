"""infra.verify.gate must reflect verify.json health, not launchd job presence."""
from __future__ import annotations

import json
import time

import pytest

from utah import proof
from utah.daemon import runtime


def _write_verify(tmp_path, payload: dict) -> None:
    (tmp_path / "verify.json").write_text(json.dumps(payload))


@pytest.fixture
def verify_run_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "RUN_DIR", tmp_path)
    proof.register(proof.ProofSpec(
        id="t.verify.gate", claim="verify gate runs", system="infra",
        artifact="ops/verify.py", proof_kind="verify_json", proof_cmd="1200",
        freshness_sla="20 minutes",
    ))
    yield tmp_path
    with proof._pool().connection() as c:
        c.execute("DELETE FROM proof_runs WHERE proof_id = 't.verify.gate'")
        c.execute("DELETE FROM proof_ledger WHERE id = 't.verify.gate'")


def test_verify_gate_missing_json_fails(verify_run_dir):
    result, output = proof.run("t.verify.gate")
    assert result == "fail"
    assert "missing" in output


def test_verify_gate_red_recent_fails(verify_run_dir):
    _write_verify(verify_run_dir, {
        "ok": False, "state": "red", "exit_code": 124,
        "confirmed": True, "ts": time.time(),
        "failures": ["suite timed out after 600s"],
    })
    result, output = proof.run("t.verify.gate")
    assert result == "fail"
    assert "red" in output


def test_verify_gate_stale_green_fails(verify_run_dir):
    _write_verify(verify_run_dir, {
        "ok": True, "state": "green", "exit_code": 0,
        "ts": time.time() - 3600,
    })
    result, output = proof.run("t.verify.gate")
    assert result == "fail"
    assert "stale" in output


def test_verify_gate_green_recent_passes(verify_run_dir):
    _write_verify(verify_run_dir, {
        "ok": True, "state": "green", "exit_code": 0,
        "ts": time.time(),
    })
    result, output = proof.run("t.verify.gate")
    assert result == "pass"
    assert "green" in output


def test_verify_gate_build_active_fails(verify_run_dir):
    _write_verify(verify_run_dir, {
        "ok": None, "state": "build_active", "ts": time.time(),
    })
    result, output = proof.run("t.verify.gate")
    assert result == "fail"
    assert "not green" in output

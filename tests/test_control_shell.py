"""Ace's deck terminal runs REAL shell commands (so Michael never opens Terminal.app), each
one a proof receipt, bounded by a timeout, with cwd that follows `cd` across the session."""
from __future__ import annotations

import os

import pytest

from utah import control


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    d = tmp_path / "activity"
    monkeypatch.setattr(control, "ACTIVITY_DIR", d)
    monkeypatch.setattr(control, "LEDGER", d / "actions.jsonl")
    return d


def test_runs_a_command_and_captures_stdout(tmp_path):
    r = control.run_shell("echo hello-ace", cwd=str(tmp_path))
    assert r["ok"] is True
    assert r["rc"] == 0
    assert "hello-ace" in r["out"]
    assert r["cwd"] == str(tmp_path)


def test_nonzero_exit_is_reported_not_hidden(tmp_path):
    r = control.run_shell("exit 3", cwd=str(tmp_path))
    assert r["ok"] is False
    assert r["rc"] == 3


def test_stderr_is_captured(tmp_path):
    r = control.run_shell("echo oops 1>&2", cwd=str(tmp_path))
    assert "oops" in r["out"]


def test_cd_follows_across_the_session(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    r = control.run_shell(f"cd {sub}", cwd=str(tmp_path))
    # the resolved new cwd comes back so the terminal can track it; the marker is stripped
    assert os.path.realpath(r["cwd"]) == os.path.realpath(str(sub))
    assert "__CWD__" not in r["out"]


def test_timeout_is_bounded_not_hung(tmp_path):
    r = control.run_shell("sleep 5", cwd=str(tmp_path), timeout=1.0)
    assert r["ok"] is False
    assert r["rc"] == 124  # conventional timeout code
    assert "timed out" in r["out"].lower()


def test_each_command_writes_a_proof_receipt(tmp_path):
    control.run_shell("echo proof", cwd=str(tmp_path))
    recent = control.activity()["recent"]
    assert any(r["action"] == "shell" for r in recent)
    shell_row = next(r for r in recent if r["action"] == "shell")
    assert shell_row["evidence"]["rc"] == 0
    assert "duration" in shell_row


def test_empty_command_is_a_noop(tmp_path):
    r = control.run_shell("   ", cwd=str(tmp_path))
    assert r["ok"] is False
    assert r["out"] == ""

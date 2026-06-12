"""Selfcode git boundary — every git call is BOUNDED (a hung `git push` is a network op
that would freeze the coding gate forever), and a timeout DEGRADES safely: not-clean,
no-files, no-sha, pushed=False — never an exception out of the gate. Plus the small
state helpers (_slug, supervised counter) that the tier policy rides on."""
from __future__ import annotations

import subprocess

from utah import selfcode


def _spy(seen):
    def run(args, **kw):
        seen.append((list(args), kw.get("timeout")))
        return subprocess.CompletedProcess(args, 0, "", "")
    return run


def test_every_git_boundary_passes_a_timeout(monkeypatch):
    seen: list = []
    monkeypatch.setattr(selfcode.subprocess, "run", _spy(seen))
    selfcode._real_changed_files(".")
    selfcode._real_branch("slug", repo=".")
    selfcode._real_discard(repo=".")
    selfcode._tree_clean(".")
    selfcode._real_commit_proposal("task", repo="/tmp/x")
    selfcode._real_merge("selfcode/x", "task", repo=".")
    assert seen, "git boundaries must actually shell out"
    unbounded = [args for args, t in seen if not (t and t > 0)]
    assert unbounded == [], f"git calls with NO timeout: {unbounded}"


def test_git_timeout_degrades_not_raises(monkeypatch):
    def hang(args, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout") or 1)

    monkeypatch.setattr(selfcode.subprocess, "run", hang)
    assert selfcode._tree_clean(".") is False               # timeout → NOT clean → automerge refused
    assert selfcode._real_changed_files(".") == []
    assert selfcode._real_commit_proposal("t", repo="/tmp/x") is None
    sha, pushed = selfcode._real_merge("b", "t", repo=".")
    assert pushed is False                                  # a hung push is reported, never assumed
    assert selfcode._real_branch("s", repo=".") == "selfcode/s"   # still names the branch
    selfcode._real_discard(repo=".")                        # must not raise


def test_tree_clean_true_only_on_rc0_empty_porcelain(monkeypatch):
    monkeypatch.setattr(selfcode.subprocess, "run",
                        lambda a, **k: subprocess.CompletedProcess(a, 0, " M utah/x.py\n", ""))
    assert selfcode._tree_clean(".") is False
    monkeypatch.setattr(selfcode.subprocess, "run",
                        lambda a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    assert selfcode._tree_clean(".") is True
    monkeypatch.setattr(selfcode.subprocess, "run",
                        lambda a, **k: subprocess.CompletedProcess(a, 128, "", "fatal"))
    assert selfcode._tree_clean(".") is False               # a broken repo is never "clean"


# ── small helpers the policy rides on ────────────────────────────────────────
def test_slug_normalizes_and_bounds():
    assert selfcode._slug("Fix: the THING!!") == "fix-the-thing"
    assert selfcode._slug("") == "change"
    assert selfcode._slug("???") == "change"
    assert len(selfcode._slug("x y " * 40)) <= 40


def test_supervised_counter_roundtrip_and_corruption(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "SUPERVISED_STATE", tmp_path / "s.json")
    assert selfcode._read_supervised() == 0                 # absent → 0
    selfcode._bump_supervised()
    selfcode._bump_supervised()
    assert selfcode._read_supervised() == 2
    (tmp_path / "s.json").write_text("NOT JSON")
    assert selfcode._read_supervised() == 0                 # corrupt → 0, never raises


def test_smoke_record_never_raises_and_skips_failure_feed(monkeypatch, tmp_path):
    from utah import failures
    from tests.fakes import FakeFailureStore

    store = FakeFailureStore()
    failures.set_store(store)
    monkeypatch.setattr(selfcode, "SMOKE_LOG", tmp_path / "smoke.jsonl")
    selfcode._smoke_record("selfcode", "off_limits", "synthetic")
    assert store.rows == []                                 # never pollutes the AUDIT feed
    assert "off_limits" in (tmp_path / "smoke.jsonl").read_text()

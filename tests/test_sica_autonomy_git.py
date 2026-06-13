"""SICA autonomy git plumbing: every git call is time-bounded and a timeout degrades to
a FAILED CompletedProcess (skip the cycle, never crash it), and propagate() reports a
failed fetch HONESTLY instead of misdiagnosing it as a divergence."""
from __future__ import annotations

import subprocess

from utah import sica_autonomy


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def test_git_timed_returns_failed_process_on_timeout(monkeypatch):
    monkeypatch.setattr(sica_autonomy, "GIT_TIMEOUT_S", 0.05)
    r = sica_autonomy._git_timed(["sleep", "5"], capture_output=True, text=True)
    assert r.returncode != 0                       # failed, so callers skip the cycle
    assert "timed out" in (r.stderr or "")


def test_git_timed_passes_through_success():
    r = sica_autonomy._git_timed(["git", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and "git version" in r.stdout


def test_propagate_missing_repos_is_honest(tmp_path):
    r = sica_autonomy.propagate(live=tmp_path / "a", clone=tmp_path / "b")
    assert r["propagated"] is False and "missing" in r["reason"]


def test_propagate_reports_fetch_failure_honestly(tmp_path):
    """A clone whose main can't be fetched (here: an empty repo with no commits) must
    surface as a FETCH failure — the old path fell through to a misleading
    'not a fast-forward' diagnosis."""
    live, clone = tmp_path / "live", tmp_path / "clone"
    live.mkdir(), clone.mkdir()
    _git(live, "init", "-q", "-b", "main")
    _git(live, "config", "user.email", "t@t")
    _git(live, "config", "user.name", "t")
    (live / "f.txt").write_text("1")
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "base")
    _git(clone, "init", "-q", "-b", "main")        # no commits → fetch main fails
    r = sica_autonomy.propagate(live=live, clone=clone)
    assert r["propagated"] is False
    assert "fetch" in r["reason"].lower()


def test_sync_repo_false_when_not_a_git_repo(tmp_path):
    assert sica_autonomy.sync_repo(tmp_path) is False


def test_scrub_conflict_refs_removes_space_refs_keeps_real(tmp_path):
    """iCloud conflict-copy refs ('refs/heads/main 2') break git fetch negotiation
    ('bad object ... did not send all necessary objects') and stalled propagation
    all night. The scrub removes space-named refs and never the real ones."""
    heads = tmp_path / ".git" / "refs" / "heads"
    heads.mkdir(parents=True)
    (heads / "main").write_text("0" * 40 + "\n")
    (heads / "main 2").write_text("deadbeef" + "0" * 32 + "\n")   # iCloud conflict copy
    removed = sica_autonomy._scrub_conflict_refs(tmp_path)
    assert removed == 1
    assert (heads / "main").is_file()           # real ref untouched
    assert not (heads / "main 2").exists()       # conflict copy gone


def test_propagate_survives_corrupt_conflict_ref_in_clone(tmp_path):
    """A corrupt 'main 2' ref in the clone used to abort the fetch ('bad object
    refs/heads/main 2'); the scrub guard lets propagation compound anyway."""
    live, clone = tmp_path / "live", tmp_path / "clone"
    live.mkdir()
    _git(live, "init", "-q", "-b", "main")
    _git(live, "config", "user.email", "t@t")
    _git(live, "config", "user.name", "t")
    (live / "f.txt").write_text("1")
    _git(live, "add", "-A"); _git(live, "commit", "-qm", "base")
    _git(tmp_path, "clone", "-q", str(live), "clone")
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    (clone / "f.txt").write_text("2")            # autonomous change → clone ahead
    _git(clone, "add", "-A"); _git(clone, "commit", "-qm", "autonomous fix")
    # iCloud conflict-copy ref pointing at a missing object — the failure from the logs
    (clone / ".git" / "refs" / "heads" / "main 2").write_text("deadbeef" + "0" * 32 + "\n")
    r = sica_autonomy.propagate(live=live, clone=clone)
    assert r["propagated"] is True, r
    assert (live / "f.txt").read_text() == "2"   # the autonomous change landed


def _live_and_ahead_clone(tmp_path):
    """A live repo + a clone one autonomous commit ahead (ff-able into live)."""
    live, clone = tmp_path / "live", tmp_path / "clone"
    live.mkdir()
    _git(live, "init", "-q", "-b", "main")
    _git(live, "config", "user.email", "t@t"); _git(live, "config", "user.name", "t")
    (live / "f.txt").write_text("1")
    _git(live, "add", "-A"); _git(live, "commit", "-qm", "base")
    _git(tmp_path, "clone", "-q", str(live), "clone")
    _git(clone, "config", "user.email", "t@t"); _git(clone, "config", "user.name", "t")
    (clone / "f.txt").write_text("2")
    _git(clone, "add", "-A"); _git(clone, "commit", "-qm", "autonomous change")
    return live, clone


def test_propagate_rolls_back_when_live_verify_red(tmp_path):
    """NO-REGRESSION GUARANTEE: a merged change whose live suite is RED is auto-reverted
    — the live tree returns to its prior state and the change does NOT land."""
    live, clone = _live_and_ahead_clone(tmp_path)
    before = _git(live, "rev-parse", "HEAD").stdout.strip()
    r = sica_autonomy.propagate(live=live, clone=clone, verify_fn=lambda _repo: False)
    assert r["propagated"] is False and r.get("rolled_back") is True, r
    assert (live / "f.txt").read_text() == "1"                       # change reverted
    assert _git(live, "rev-parse", "HEAD").stdout.strip() == before  # back to prior commit


def test_propagate_keeps_change_when_live_verify_green(tmp_path):
    """A merged change whose live suite is GREEN lands normally."""
    live, clone = _live_and_ahead_clone(tmp_path)
    r = sica_autonomy.propagate(live=live, clone=clone, verify_fn=lambda _repo: True)
    assert r["propagated"] is True, r
    assert (live / "f.txt").read_text() == "2"                       # change landed


def test_propagate_crashing_verifier_is_fail_closed(tmp_path):
    """A verifier that raises is treated as RED (fail-closed) → rolled back, never landed."""
    live, clone = _live_and_ahead_clone(tmp_path)
    def boom(_repo):
        raise RuntimeError("verifier blew up")
    r = sica_autonomy.propagate(live=live, clone=clone, verify_fn=boom)
    assert r["propagated"] is False and r.get("rolled_back") is True, r
    assert (live / "f.txt").read_text() == "1"

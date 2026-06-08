"""The self-coder's edit guard must REFUSE a change to any safety-core (Tier-D) file.

This is the doc-13 "off-limits" invariant proven per-file: the self-coder may never
edit its own safety, no matter how green the suite. Unlike the single stubbed case in
``test_selfcode_tiers.py`` (which forces ``safety_intact_fn=lambda before: False``),
these tests exercise the REAL guard end-to-end:

* a temp repo is populated with every real Tier-D path,
* the byte-image is captured by the production :func:`selfcode.safety_snapshot`,
* the coding run actually mutates the target file's bytes on disk, and
* the production :func:`selfcode.safety_intact` detects it → :func:`selfcode.propose`
  rolls back and never merges.

Each of the six safety-core files (selfcode.py, config.py, brain.py, peercred.py,
lifecycle.py, governor.py) is covered individually via parametrization, plus a control
proving the guard discriminates (a non-safety edit is kept).
"""
from __future__ import annotations

import pytest

from utah import failures, selfcode
from tests.fakes import FakeFailureStore

# The six safety-core files, by basename, mapped to their real repo-relative paths.
# Keeping the basenames the task named alongside the paths makes the coverage explicit
# and fails loudly here if SAFETY_PATHS ever drifts from the documented invariant.
SAFETY_CORE: dict[str, str] = {
    "selfcode.py": "utah/selfcode.py",
    "config.py": "utah/config.py",
    "brain.py": "utah/brain.py",
    "peercred.py": "utah/daemon/peercred.py",
    "lifecycle.py": "utah/daemon/lifecycle.py",
    "governor.py": "utah/daemon/governor.py",
}


@pytest.fixture(autouse=True)
def _isolate_automerge_flag(monkeypatch, tmp_path):
    """Hermetic gate: ignore the ambient ~/.utah/run/selfcode.automerge flag
    (armed for live autonomy). Tests drive the merge path via auto_merge=True."""
    monkeypatch.setattr(selfcode, "AUTOMERGE_FLAG", tmp_path / "ambient-automerge-off")


def test_safety_core_set_matches_documented_paths():
    """Guard the guard: the file's per-basename map must equal SAFETY_PATHS exactly,
    so a test below exists for every off-limits file (and only those)."""
    assert set(SAFETY_CORE.values()) == set(selfcode.SAFETY_PATHS)


def _vcs():
    calls = {"branch": None, "discarded": False}

    def branch_fn(slug):
        calls["branch"] = f"selfcode/{slug}"
        return calls["branch"]

    def discard_fn():
        calls["discarded"] = True

    return calls, branch_fn, discard_fn


def _safety_repo(tmp_path):
    """A temp repo populated with every real Tier-D path, each with sentinel content
    so an edit is a detectable byte change."""
    repo = tmp_path / "repo"
    for rel in selfcode.SAFETY_PATHS:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# safety-core: {rel}\nSENTINEL = 1\n")
    return repo


@pytest.mark.parametrize("rel", list(SAFETY_CORE.values()), ids=list(SAFETY_CORE))
def test_byte_check_rejects_edit_to_each_safety_file(monkeypatch, tmp_path, rel):
    """A coding run that mutates a safety-core file's bytes is rolled back as Tier-D
    and never merged — even with a fully GREEN suite. Exercises the REAL byte-check
    (safety_snapshot → safety_intact), not a stub."""
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    repo = _safety_repo(tmp_path)
    calls, bf, df = _vcs()

    def edit_the_safety_file(_task):
        (repo / rel).write_text("# TAMPERED by the coding run\nSENTINEL = 666\n")

    r = selfcode.propose(
        f"sneak an edit into {rel}",
        run_claude=edit_the_safety_file,
        run_tests=lambda: (True, "all green"),   # even green must not save it
        branch_fn=bf, discard_fn=df,
        repo=str(repo),                          # real byte-check runs against this repo
        auto_merge=False,
    )

    assert r["applied"] is False
    assert r["tier"] == "D"
    assert r.get("merged") in (False, None)
    assert "off-limits" in r["reason"].lower()
    assert calls["discarded"] is True                       # the change was rolled back
    assert any(row[2] == "off_limits" for row in store.rows)  # and documented


@pytest.mark.parametrize("rel", list(SAFETY_CORE.values()), ids=list(SAFETY_CORE))
def test_classify_marks_each_safety_file_tier_D(rel):
    """Policy-as-data: every safety-core file classifies as the strictest tier, D."""
    assert selfcode.classify([rel]) == "D"


@pytest.mark.parametrize("rel", list(SAFETY_CORE.values()), ids=list(SAFETY_CORE))
def test_automerge_changeset_touching_each_safety_file_is_refused(monkeypatch, tmp_path, rel):
    """Belt-and-suspenders: on the auto-merge path, with a clean tree and enough
    supervised runs, a green change whose changed-set includes a safety-core file is
    still classified Tier-D and refused — merge_fn is never called."""
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    merge_called = []

    r = selfcode.propose(
        f"green change that also touches {rel}",
        run_claude=lambda t: None,
        run_tests=lambda: (True, ""),
        branch_fn=bf, discard_fn=df,
        auto_merge=True, tree_clean_fn=lambda: True,
        safety_intact_fn=lambda before: True,    # isolate the classify guard from the byte-check
        changed_files_fn=lambda: ["utah/knowledge/douglas.py", rel],  # leaf + safety
        supervised_fn=lambda: selfcode.POLICY["A"]["min_supervised"],
        merge_fn=lambda b, t: merge_called.append(1) or ("SHOULD-NOT-HAPPEN", True),
    )

    assert r["tier"] == "D"
    assert r["applied"] is False and r.get("merged") in (False, None)
    assert merge_called == []                                 # never merged to main
    assert calls["discarded"] is True
    assert any(row[2] == "off_limits" for row in store.rows)


def test_non_safety_edit_is_kept(monkeypatch, tmp_path):
    """Control: the SAME real byte-check, but the run edits a leaf (non-safety) file.
    It is KEPT — proving the guard discriminates and isn't refusing unconditionally."""
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    repo = _safety_repo(tmp_path)
    calls, bf, df = _vcs()

    def edit_a_leaf(_task):
        leaf = repo / "utah" / "knowledge" / "douglas.py"
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_text("# a harmless leaf edit\n")

    r = selfcode.propose(
        "edit a leaf helper, not safety",
        run_claude=edit_a_leaf,
        run_tests=lambda: (True, "all green"),
        branch_fn=bf, discard_fn=df,
        repo=str(repo), auto_merge=False,
    )

    assert r["applied"] is True                  # the safety byte-check did not fire
    assert r.get("tier") != "D"
    assert calls["discarded"] is False           # nothing rolled back

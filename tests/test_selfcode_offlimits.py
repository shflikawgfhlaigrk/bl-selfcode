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
    calls = {"branch": None, "branches": 0, "discarded": False, "discards": 0}

    def branch_fn(slug):
        calls["branches"] += 1
        calls["branch"] = f"selfcode/{slug}"
        return calls["branch"]

    def discard_fn():
        calls["discarded"] = True
        calls["discards"] += 1

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
    (safety_snapshot → safety_intact), not a stub.

    Also pins the two properties the off-limits guard must hold per file:

    * ONE CLEAR REASON — a single, non-empty, single-line ``reason`` string that names
      the off-limits guard (no list of reasons, no multi-line dump), and
    * NO RETRY LOOP — the run executes exactly once, the guard short-circuits BEFORE
      the suite runs, and the rollback happens exactly once. There is no re-attempt,
      no "edit-again-and-recheck" loop trying to coax the change past the guard."""
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    repo = _safety_repo(tmp_path)
    calls, bf, df = _vcs()
    coded: list[int] = []   # every run_claude invocation
    tested: list[int] = []  # every run_tests invocation

    def edit_the_safety_file(_task):
        coded.append(1)
        (repo / rel).write_text("# TAMPERED by the coding run\nSENTINEL = 666\n")

    def run_tests():
        tested.append(1)
        return (True, "all green")               # even green must not save it

    r = selfcode.propose(
        f"sneak an edit into {rel}",
        run_claude=edit_the_safety_file,
        run_tests=run_tests,
        branch_fn=bf, discard_fn=df,
        repo=str(repo),                          # real byte-check runs against this repo
        auto_merge=False,
    )

    assert r["applied"] is False
    assert r["tier"] == "D"
    assert r.get("merged") in (False, None)

    # One clear reason: a single, non-empty, single-line string naming the guard.
    reason = r["reason"]
    assert isinstance(reason, str)
    assert reason.strip()                        # non-empty
    assert "\n" not in reason                    # one line, not a multi-reason dump
    assert "off-limits" in reason.lower()

    # No retry loop: branch once, run once, suite never reached (guard fires first),
    # rolled back exactly once — never re-run to retry sneaking the edit through.
    assert calls["branches"] == 1
    assert coded == [1]                          # run_claude invoked exactly once
    assert tested == []                          # suite short-circuited — never ran
    assert calls["discards"] == 1                # rolled back exactly once
    assert calls["discarded"] is True
    assert any(row[2] == "off_limits" for row in store.rows)  # and documented


def test_off_limits_reason_is_one_canonical_message(monkeypatch, tmp_path):
    """One clear reason — the SAME single reason for every off-limits file, not a
    per-file scattering of messages. Collect the reason from rejecting an edit to each
    of the six safety-core files; the set of distinct reasons must be exactly one."""
    reasons: set[str] = set()
    for rel in SAFETY_CORE.values():
        failures.set_store(FakeFailureStore())
        monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
        # Fresh repo per file: a no-op fake discard leaves the tamper on disk, so a
        # shared repo would make the next run's before-image already-tampered.
        repo = _safety_repo(tmp_path / rel.replace("/", "_"))
        calls, bf, df = _vcs()
        r = selfcode.propose(
            f"sneak an edit into {rel}",
            run_claude=lambda _t, _r=rel, _repo=repo: (_repo / _r).write_text("TAMPER\n"),
            run_tests=lambda: (True, "all green"),
            branch_fn=bf, discard_fn=df, repo=str(repo), auto_merge=False,
        )
        assert r["tier"] == "D"
        reasons.add(r["reason"])

    assert len(reasons) == 1                     # exactly one reason for all six files
    (only,) = reasons
    assert "off-limits" in only.lower()
    assert "\n" not in only and only.strip()     # a single, clear line


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
    coded: list[int] = []
    tested: list[int] = []

    r = selfcode.propose(
        f"green change that also touches {rel}",
        run_claude=lambda t: coded.append(1),
        run_tests=lambda: tested.append(1) or (True, ""),
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
    # One clear reason, single line, naming the guard.
    assert isinstance(r["reason"], str) and "\n" not in r["reason"]
    assert "off-limits" in r["reason"].lower()
    # No retry loop on the classify path either: one run, one suite, one rollback.
    assert calls["branches"] == 1 and coded == [1] and tested == [1]
    assert calls["discards"] == 1
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

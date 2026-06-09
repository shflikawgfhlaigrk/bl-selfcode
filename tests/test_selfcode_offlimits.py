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

"Not re-queued" is proven at BOTH levels:

* WITHIN a run — the guard short-circuits BEFORE the suite, runs the coding step exactly
  once, and rolls back exactly once; there is no edit-again-and-recheck retry loop.
* ACROSS the discovery queue — a PENDING off-limits task that the guard rejects is still
  marked used by :func:`sica_autonomy.run_cycle`, so :func:`sica_discover.next_pending_task`
  never re-serves it: a rejected safety edit is dropped, not retried forever next cycle.
"""
from __future__ import annotations

import json

import pytest

from utah import failures, selfcode, sica_autonomy, sica_discover
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


def _propose_off_limits(repo, rel, holder):
    """A proposer for :func:`sica_autonomy.run_cycle` that runs the REAL off-limits guard:
    the coding step tampers *rel* (a Tier-D file) in *repo*, so the production byte-check
    (``safety_snapshot`` → ``safety_intact``) fires and :func:`selfcode.propose` rolls the
    change back as Tier-D. Stashes the raw result in *holder* so the test can assert it."""
    def propose_fn(task):
        r = selfcode.propose(
            task,
            run_claude=lambda _t: (repo / rel).write_text("# TAMPERED\nSENTINEL = 666\n"),
            run_tests=lambda: (True, "all green"),       # even green must not save it
            branch_fn=holder["bf"], discard_fn=holder["df"],
            repo=str(repo), auto_merge=False,
        )
        holder["result"] = r
        return r
    return propose_fn


@pytest.mark.parametrize("rel", list(SAFETY_CORE.values()), ids=list(SAFETY_CORE))
def test_off_limits_rejected_task_is_not_requeued(monkeypatch, tmp_path, rel):
    """End-to-end queue invariant: a PENDING self-code task that the guard rejects as
    off-limits is CONSUMED (marked used) and never re-served by the discovery queue — a
    rejected safety edit is dropped, not retried forever on the next cycle.

    Drives the real :func:`sica_autonomy.run_cycle` over an isolated discovery queue
    holding exactly one off-limits task; the injected proposer runs the production
    byte-check (tamper → Tier-D rollback). Afterwards the queue must be empty."""
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")

    # Isolated discovery queue holding exactly one pending off-limits task.
    log = tmp_path / "discoveries.jsonl"
    used = tmp_path / "used.json"
    task = f"edit the safety file {rel} to weaken the guard"
    rec = {"ts": 1.0, "domain": "research", "suggested_task": task, "brief_path": ""}
    log.write_text(json.dumps(rec) + "\n")
    monkeypatch.setattr(sica_discover, "DISCOVERIES_LOG", log)
    monkeypatch.setattr(sica_discover, "USED_PATH", used)
    # The off-limits task is a 'research' finding. With the revenue-weighted rotation a routine
    # browser (frontend/research) finding is consumed only on its OWN rotation turn — so pin the
    # rotation to 'research' here, exercising the off-limits guard deterministically (the cycle
    # counter is otherwise persistent/stateful, which made this order-dependent).
    from utah import sica_goals
    monkeypatch.setattr(sica_goals, "next_cycle_index", lambda: sica_goals.DOMAINS.index("research"))
    # Precondition: the queue WOULD serve this task.
    assert sica_discover.next_pending_task(log_path=log, used_path=used) is not None

    safety_repo = _safety_repo(tmp_path)
    calls, bf, df = _vcs()
    holder = {"bf": bf, "df": df, "result": None}

    out = sica_autonomy.run_cycle(
        repo=tmp_path, sync_fn=lambda repo: True,
        discover_fn=lambda: {"ran": False},          # skip the browser discovery pass
        foundation_gate=lambda name: None,           # no cron skip
        load_fn=lambda: 0.0,                         # calm machine — don't defer
        propose_fn=_propose_off_limits(safety_repo, rel, holder),
    )

    # The cycle consumed exactly the off-limits task...
    assert out["task"] == task
    # ...the proposer REALLY rejected it off-limits (Tier-D, rolled back, never merged)...
    r = holder["result"]
    assert r["applied"] is False
    assert r["tier"] == "D"
    assert r.get("merged") in (False, None)
    assert "off-limits" in r["reason"].lower()
    # ...documented as a selfcode off_limits failure (source=selfcode, kind=off_limits)...
    assert any(row[1] == "selfcode" and row[2] == "off_limits" for row in store.rows)
    # ...rolled back exactly once, no retry...
    assert calls["discards"] == 1
    # ...and NOT re-queued: the consumed finding is marked used, the queue is now empty.
    assert sica_discover.next_pending_task(log_path=log, used_path=used) is None


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

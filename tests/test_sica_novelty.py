"""Anti-degeneracy for the selfcode loop (audit 2026-06-12: 18/60 commits on main
were the SAME trivial `_probe_marker.py` test, merged over and over).

Three reinforcing defects, each pinned here:
  1. No task memory → the brain re-derived the same "safest smallest" task every
     cycle (sica_goals: recent-work block + is_degenerate gate).
  2. The probe target became the work target → `_probe_marker` is banned as a
     task SUBJECT (the probe machinery itself still uses the file).
  3. utility = pass-rate + cheap + fast → a trivial repeat was argmax-best and the
     meta-prompt showcased it as "best so far" (sica: repeat-discounted effective
     utility; sica_loop: best computed over effective utility).

All boundaries injected — no DB, no brain, no git.
"""
from __future__ import annotations

import pytest

from utah import selfcode, sica, sica_autonomy, sica_discover, sica_goals

_SKIP_DISCOVER = lambda: {"ran": True, "findings": [], "count": 0}

PROBE_TASK = ("Add a unit test asserting utah/_probe_marker.py exposes a "
              "module-level __version__ constant")


# ── task memory ──────────────────────────────────────────────────────────────

def test_recent_tasks_reads_cycle_telemetry_newest_first():
    cycles = [{"task": "newest", "domain": "baseline"},
              {"task": "middle", "domain": "leads"},
              {"task": "newest"},                       # duplicate → collapsed
              {"no_task": True},                        # ran:false, no task → skipped
              {"task": "oldest"}]
    got = sica_goals.recent_tasks(cycles_fn=lambda: cycles)
    assert got == ["newest", "middle", "oldest"]


def test_recent_tasks_caps_and_is_defensive():
    cycles = [{"task": f"t{i}"} for i in range(50)]
    assert len(sica_goals.recent_tasks(k=10, cycles_fn=lambda: cycles)) == 10
    def boom():
        raise RuntimeError("dead telemetry")
    assert sica_goals.recent_tasks(cycles_fn=boom) == []   # never raises


# ── the degeneracy gate ──────────────────────────────────────────────────────

def test_banned_probe_marker_target_is_degenerate():
    reason = sica_goals.is_degenerate(PROBE_TASK, recent=())
    assert reason and "_probe_marker" in reason


def test_near_duplicate_of_recent_task_is_degenerate():
    recent = ["Add a unit test asserting utah/alerts.py dedups repeated alert keys"]
    dup = "Add a unit test asserting that utah/alerts.py dedups repeated alert keys."
    assert sica_goals.is_degenerate(dup, recent=recent)


def test_fresh_task_is_not_degenerate():
    recent = [PROBE_TASK, "Improve one docstring in utah/product/leads.py"]
    fresh = "Add connect_timeout to the DuckDB attach path in utah/store/olap.py"
    assert sica_goals.is_degenerate(fresh, recent=recent) is None


def test_prompt_carries_recent_work_do_not_repeat_block():
    p = sica_goals.build_prompt("baseline", "signals here",
                                recent=["fix alerts dedup", "harden olap timeout"])
    assert "do NOT repeat" in p
    assert "fix alerts dedup" in p and "harden olap timeout" in p
    assert "_probe_marker" in p          # the ban is stated to the brain too


def test_next_task_feeds_recent_work_and_rejection_feedback_to_brain():
    seen = {}
    def brain(prompt):
        seen["prompt"] = prompt
        return "a brand new task"
    sica_goals.next_task("baseline", brain_fn=brain,
                         cycles_fn=lambda: [{"task": "old work"}],
                         rejected=PROBE_TASK, reject_reason="banned target")
    assert "old work" in seen["prompt"]
    assert "REJECTED" in seen["prompt"] and "banned target" in seen["prompt"]


# ── run_cycle wiring ─────────────────────────────────────────────────────────

def _pin_baseline(monkeypatch):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", selfcode.KILL_SWITCH.parent / "nope-novelty")
    monkeypatch.setattr(sica_discover, "next_pending_task", lambda: None)
    monkeypatch.setattr(sica_goals, "select_domain", lambda n, **kw: "baseline")
    monkeypatch.setattr(sica_goals, "next_cycle_index", lambda: 0)


def test_run_cycle_regenerates_once_on_degenerate_task(monkeypatch, tmp_path):
    _pin_baseline(monkeypatch)
    calls = []
    def brain(prompt):
        calls.append(prompt)
        return PROBE_TASK if len(calls) == 1 else \
            "Add a failure-path test for utah/product/outreach.py send gating"
    proposed = {}
    r = sica_autonomy.run_cycle(
        repo=tmp_path, sync_fn=lambda repo: True, discover_fn=_SKIP_DISCOVER,
        brain_fn=brain, recent_fn=lambda: [],
        propose_fn=lambda t: proposed.setdefault("task", t) and {} or
            {"utility": 0.8, "tests_passed": True, "merged": False, "cost_usd": 0})
    assert r["ran"] is True
    assert "outreach" in r["task"] and "_probe_marker" not in r["task"]
    assert proposed["task"] == r["task"]
    assert len(calls) == 2 and "REJECTED" in calls[1]


def test_run_cycle_skips_honestly_when_regeneration_still_degenerate(monkeypatch, tmp_path):
    _pin_baseline(monkeypatch)
    ran = []
    r = sica_autonomy.run_cycle(
        repo=tmp_path, sync_fn=lambda repo: True, discover_fn=_SKIP_DISCOVER,
        brain_fn=lambda p: PROBE_TASK,          # incorrigible
        recent_fn=lambda: [],
        propose_fn=lambda t: ran.append(t) or {})
    assert r["ran"] is False
    assert "degenerate" in r["reason"] and ran == []   # nothing proposed/merged


def test_run_cycle_injected_task_bypasses_gate(monkeypatch, tmp_path):
    """task_fn is the operator/test override — never vetoed."""
    _pin_baseline(monkeypatch)
    r = sica_autonomy.run_cycle(
        repo=tmp_path, sync_fn=lambda repo: True, discover_fn=_SKIP_DISCOVER,
        task_fn=lambda: PROBE_TASK,
        propose_fn=lambda t: {"utility": 0.5, "tests_passed": True,
                              "merged": False, "cost_usd": 0})
    assert r["ran"] is True and r["task"] == PROBE_TASK


# ── repeat-discounted effective utility ──────────────────────────────────────

def _entry(task, u):
    return {"task": task, "utility": u, "passed": True}


def test_effective_utility_decays_spammed_tasks():
    entries = [_entry(PROBE_TASK, 0.95)] * 3 + [_entry("real probate ARV work", 0.7)]
    eff = sica.effective_entries(entries)
    probe = [e for e in eff if e["task"] == PROBE_TASK]
    real = [e for e in eff if "probate" in e["task"]]
    assert all(e["utility"] < 0.1 for e in probe)      # 0.95 · 0.2² → noise
    assert real[0]["utility"] == pytest.approx(0.7)    # done once → untouched


def test_best_effective_ignores_spam():
    entries = [_entry(PROBE_TASK, 0.95)] * 3 + [_entry("real probate ARV work", 0.7)]
    best = sica.best_effective(entries)
    assert best["task"] == "real probate ARV work"
    assert sica.best_effective([]) is None


def test_best_effective_never_showcases_a_banned_subject():
    """Live finding 2026-06-12: the ORIGINAL 'PROBE diff-capture: add
    utah/_probe_marker.py' entry was done once (no repeat discount) at u=1.0 — the
    meta-prompt showcased it as best-so-far, which is what seeded the spam family.
    A banned-subject task can never be the bar to beat."""
    entries = [_entry("PROBE diff-capture: add utah/_probe_marker.py", 1.0),
               _entry("real probate ARV work", 0.7)]
    best = sica.best_effective(entries)
    assert best["task"] == "real probate ARV work"
    only_banned = [_entry("PROBE diff-capture: add utah/_probe_marker.py", 1.0)]
    assert sica.best_effective(only_banned) is None
    # The observed family re-wordings are banned too (live archive 2026-06-12).
    family = [_entry("add a one line docstring to utah probe", 1.0),
              _entry("Create a new file at utah/_meta.py with a docstring", 0.99),
              _entry("real probate ARV work", 0.7)]
    assert sica.best_effective(family)["task"] == "real probate ARV work"


def test_best_effective_breaks_utility_ties_by_recency():
    """utility saturates at 1.0 for any small green change, so all-time argmax with
    insertion-order ties showcased the OLDEST trivial entry forever. Newest among
    equals — recent real work is the base to evolve from."""
    entries = [{"task": "ancient trivial doc tweak", "utility": 1.0, "ts": 100.0},
               {"task": "recent real outreach failure-path tests", "utility": 1.0, "ts": 900.0}]
    assert sica.best_effective(entries)["task"] == "recent real outreach failure-path tests"


def test_meta_prompt_shows_discounted_best_and_forbids_repeats(tmp_path):
    from utah.sica_loop import MetaLoop
    arch = sica.Archive(tmp_path / "arch.jsonl")
    for _ in range(3):
        arch.record(_entry(PROBE_TASK, 0.95))
    arch.record(_entry("real probate ARV work", 0.7))
    seen = {}
    MetaLoop(archive=arch).next_task_from_archive(
        lambda p: seen.setdefault("prompt", p) and "next")
    assert "real probate ARV work" in seen["prompt"].split("Recent attempts")[0]
    assert "repeat" in seen["prompt"].lower()


# ── grounded baseline signal ─────────────────────────────────────────────────

def test_baseline_signal_names_untested_modules(tmp_path):
    (tmp_path / "utah" / "product").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "utah" / "alerts.py").write_text("x = 1")
    (tmp_path / "utah" / "naked.py").write_text("x = 1")          # no test anywhere
    (tmp_path / "utah" / "product" / "bare.py").write_text("x = 1")
    (tmp_path / "tests" / "test_alerts.py").write_text("def test_a(): pass")
    sig = sica_goals.gather_signals(
        "baseline", read_text=lambda p: "", repo_root=tmp_path, grade_queue=[])
    assert "naked.py" in sig and "bare.py" in sig
    assert "alerts.py" not in sig                                  # covered → not a target
    assert "_probe_marker" not in sig


def test_baseline_scan_never_targets_safety_core_or_conflict_copies(tmp_path):
    """The scan must not steer the loop at Tier-D files (it can't merge them anyway —
    wasted cycles) nor at iCloud conflict-copy junk (`* 2.py`)."""
    (tmp_path / "utah" / "voice").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "utah" / "config.py").write_text("x = 1")          # Tier-D safety core
    (tmp_path / "utah" / "voice" / "vad 2.py").write_text("x = 1")  # iCloud junk
    (tmp_path / "utah" / "real_gap.py").write_text("x = 1")
    got = sica_goals._untested_modules(repo_root=tmp_path)
    assert got == ["utah/real_gap.py"]


def test_baseline_signal_consumes_grade_queue():
    queue = [{"file": "utah/store/olap.py",
              "weakness": "DuckDB attach has no timeout"}]
    sig = sica_goals.gather_signals("baseline", read_text=lambda p: "",
                                    repo_root=None, grade_queue=queue)
    assert "olap.py" in sig and "timeout" in sig


def test_baseline_signal_defensive_when_scan_and_queue_fail():
    sig = sica_goals.gather_signals("baseline", read_text=lambda p: "")
    assert "coverage" in sig.lower() or "test" in sig.lower()      # still usable

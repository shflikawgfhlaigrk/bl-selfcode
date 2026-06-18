"""Live proof-of-execution panel: in-progress receipts (begin/beat/end) + the
activity() reader that splits the ledger into {active, recent} for the deck panel.

WHY (Michael, 2026-06-18): the ledger already proves COMPLETED actions, but he wants to
SEE Ace working *right now* — like a deploy subagent view: each action with ticking
elapsed-seconds and a timeout. That requires a row written when work STARTS (not only when
it ends) and a reader that computes live elapsed for anything still running.
"""
from __future__ import annotations

import pytest

from utah import control


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    """Point the activity ledger at a per-test tmp file so we never touch ~/.utah."""
    d = tmp_path / "activity"
    monkeypatch.setattr(control, "ACTIVITY_DIR", d)
    monkeypatch.setattr(control, "LEDGER", d / "actions.jsonl")
    return d


def test_begin_writes_a_running_row_and_returns_id():
    rid = control.begin("improve_apps", "building leads", timeout=120)
    assert isinstance(rid, str) and rid
    rows = control.feed(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == rid
    assert row["status"] == "running"
    assert row["action"] == "improve_apps"
    assert row["timeout"] == 120
    assert "started" in row  # the clock the panel ticks from


def test_activity_reports_a_running_action_with_live_elapsed():
    t0 = 1_000_000.0
    monkey_now(t0)
    rid = control.begin("heal", "healing", timeout=60)
    # 8 seconds later the panel asks for state
    out = control.activity(now=t0 + 8.0)
    assert len(out["active"]) == 1
    a = out["active"][0]
    assert a["id"] == rid
    assert a["elapsed"] == pytest.approx(8.0, abs=0.1)
    assert a["timeout"] == 60
    assert a["stalled"] is False
    assert out["recent"] == []


def test_end_moves_the_action_from_active_to_recent_with_duration():
    t0 = 2_000_000.0
    rid = control.begin("diagnose", "checking health", timeout=30)
    # patch started so duration is deterministic regardless of wall clock
    control.beat(rid)  # heartbeat keeps it running
    row = control.end(rid, ok=True, summary="healthy", evidence={"problems": []})
    assert row["id"] == rid
    assert row["status"] == "done"
    assert row["ok"] is True
    out = control.activity()
    assert out["active"] == []  # finished — no longer live
    assert len(out["recent"]) == 1
    r = out["recent"][0]
    assert r["id"] == rid
    assert r["status"] == "done"
    assert "duration" in r and r["duration"] >= 0.0


def test_failed_end_marks_failed_not_done():
    rid = control.begin("check_stripe", "reading sales")
    control.end(rid, ok=False, summary="no key wired")
    r = control.activity()["recent"][0]
    assert r["status"] == "failed"
    assert r["ok"] is False


def test_a_running_row_past_its_timeout_is_marked_stalled():
    t0 = 3_000_000.0
    monkey_now(t0)
    control.begin("improve_apps", "stuck build", timeout=10)
    out = control.activity(now=t0 + 100.0)  # 10x past the 10s timeout
    a = out["active"][0]
    assert a["stalled"] is True  # a crashed begin() can't masquerade as live forever


def test_beat_updates_summary_without_finishing():
    rid = control.begin("improve_apps", "starting")
    control.beat(rid, "compiling leads/RealEstate.swift")
    out = control.activity()
    assert len(out["active"]) == 1
    assert out["active"][0]["summary"] == "compiling leads/RealEstate.swift"
    assert out["active"][0]["status"] == "running"


def test_latest_row_per_id_wins_so_ledger_stays_append_only():
    # begin + several beats + end all share ONE id; the reader must collapse to the last.
    rid = control.begin("heal", "x")
    control.beat(rid, "step 1")
    control.beat(rid, "step 2")
    control.end(rid, ok=True, summary="done")
    # raw ledger has 4 physical rows (append-only) ...
    assert len(control.feed(50)) == 4
    # ... but activity() collapses them to a single finished action
    out = control.activity()
    assert out["active"] == []
    assert len(out["recent"]) == 1
    assert out["recent"][0]["summary"] == "done"


def test_recent_is_newest_first():
    a = control.begin("heal", "a"); control.end(a, ok=True, summary="a-done")
    b = control.begin("diagnose", "b"); control.end(b, ok=True, summary="b-done")
    recent = control.activity()["recent"]
    assert [r["summary"] for r in recent] == ["b-done", "a-done"]


# --- tiny helper: freeze control._now without a global monkeypatch object on hand ----
_NOW = {"v": None}


def monkey_now(v: float) -> None:
    _NOW["v"] = v


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    """If a test called monkey_now(), make control._now() return it; else real time."""
    import time as _t
    real = _t.time
    monkeypatch.setattr(control, "_now", lambda: (_NOW["v"] if _NOW["v"] is not None else real()))
    yield
    _NOW["v"] = None

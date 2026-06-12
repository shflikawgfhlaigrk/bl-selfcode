"""Fire grader edge cases — non-finite inputs, parameter abuse, horizon clipping,
breakout-bar exclusion, store-down honesty, and the think-on-fire rider.

These lock the HONESTY contract at its boundaries: a fire that cannot be graded from
REAL persisted bars is 'ungradable' with pnl NULL — never a guessed price, never a
crash, never a NaN written to the ledger.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from utah import failures
from utah.product import fire_grader
from tests.fakes import FakeFailureStore

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


# --- grade(): hostile/degenerate numeric inputs --------------------------------------

def test_nan_entry_is_ungradable_never_a_nan_pnl():
    """A NaN entry (a corrupt fires row) must NOT propagate into pnl arithmetic —
    NaN compares false everywhere, so the walk would 'time out' and write a NaN pnl."""
    g = fire_grader.grade("long", float("nan"), [100.0] * 20, [102.0])
    assert g["outcome"] == "ungradable" and g["pnl"] is None


def test_infinite_entry_is_ungradable():
    g = fire_grader.grade("long", float("inf"), [100.0] * 20, [102.0])
    assert g["outcome"] == "ungradable" and g["pnl"] is None


def test_non_positive_target_r_is_ungradable_not_nonsense():
    """target_r <= 0 inverts the target through the entry — grading with it would
    produce a 'target' outcome on a LOSING trade. Refuse instead of mis-grading."""
    g = fire_grader.grade("long", 101.0, [100.0] * 20, [102.0], target_r=0.0)
    assert g["outcome"] == "ungradable" and g["pnl"] is None
    g2 = fire_grader.grade("long", 101.0, [100.0] * 20, [102.0], target_r=-2.0)
    assert g2["outcome"] == "ungradable" and g2["pnl"] is None


def test_pnl_is_rounded_to_4_decimals():
    g = fire_grader.grade("long", 101.0, [100.0] * 20, [101.123456789, 101.123456789])
    assert g["outcome"] == "timeout"
    assert g["pnl"] == round(101.123456789 - 101.0, 4)


def test_short_timeout_mark_to_market_sign():
    # short from 99 (stop 100); drifts UP to 99.6 -> negative pnl (entry - exit)
    g = fire_grader.grade("short", 99.0, [100.0] * 20, [99.4, 99.6])
    assert g["outcome"] == "timeout"
    assert g["pnl"] == pytest.approx(-0.6)


def test_direction_is_exact_not_case_insensitive():
    """The fires table stores lowercase 'long'/'short'; anything else is a data bug
    and must surface as ungradable, not silently coerced."""
    g = fire_grader.grade("LONG", 101.0, [100.0] * 20, [103.5])
    assert g["outcome"] == "ungradable"


# --- run_scheduled: breakout-bar exclusion + horizon clipping ------------------------

class FakeGradeLedger:
    def __init__(self, fires=(), bars=None):
        self.fires = list(fires)
        self.bars = bars or {}
        self.graded: list[tuple] = []

    def init_schema(self):
        pass

    def ungraded_fires(self, older_than_minutes=30):
        return list(self.fires)

    def bars_before(self, symbol, ts, limit):
        closes = [c for t, c in self.bars.get(symbol, ()) if t <= ts]
        return closes[-limit:]

    def bars_between(self, symbol, start, end):
        return [c for t, c in self.bars.get(symbol, ()) if start < t <= end]

    def bar_symbols_between(self, start, end):
        return sorted(s for s, rows in self.bars.items()
                      if any(start < t <= end for t, _ in rows))

    def grade_fire(self, fire_id, outcome, pnl=None):
        self.graded.append((fire_id, outcome, pnl))
        return True


def _bars(t0, closes, step=15):
    return [(t0 + timedelta(seconds=step * i), c) for i, c in enumerate(closes)]


def test_breakout_bar_is_excluded_from_the_stop_window():
    """Ascending closes 100..119, breakout bar closes 120. With the breakout bar
    correctly DROPPED the window is [100..119] -> stop 100, risk 20, target 160 —
    a post close of 159 is a TIMEOUT. If the bug crept back (window includes the
    breakout bar) the stop would be 101 -> target 158 and 159 would 'hit target'."""
    t0 = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    closes = [100.0 + i for i in range(20)] + [120.0] + [159.0]
    fire_ts = t0 + timedelta(seconds=15 * 20 + 1)
    lg = FakeGradeLedger(
        fires=[{"id": 9, "engine": "breakout", "direction": "long", "entry": 120.0,
                "ts": fire_ts, "symbol": "S"}],
        bars={"S": _bars(t0, closes)})
    fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(9, "timeout", 39.0)]


def test_bars_after_the_horizon_never_grade_the_fire():
    """A target-hitting bar 31 minutes out is OUTSIDE the 30-min horizon: the fire
    times out at the last in-horizon close — late prices never rewrite the verdict."""
    t0 = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    fire_ts = t0 + timedelta(seconds=15 * 20 + 1)
    prior = [(t0 + timedelta(seconds=15 * i), 100.0) for i in range(20)]
    breakout = [(t0 + timedelta(seconds=15 * 20), 101.0)]
    inside = [(fire_ts + timedelta(minutes=5), 101.5)]
    outside = [(fire_ts + timedelta(minutes=31), 103.5)]   # would be 'target' if leaked in
    lg = FakeGradeLedger(
        fires=[{"id": 11, "engine": "breakout", "direction": "long", "entry": 101.0,
                "ts": fire_ts, "symbol": "S"}],
        bars={"S": prior + breakout + inside + outside})
    fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(11, "timeout", 0.5)]


def test_zero_candidate_symbols_reason_is_explicit():
    lg = FakeGradeLedger(
        fires=[{"id": 5, "engine": "breakout", "direction": "long", "entry": 1.0,
                "ts": datetime(2026, 6, 9, 12, 0, tzinfo=UTC), "symbol": None}])
    fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(5, "ungradable", None)]


# --- run_scheduled: store-down honesty + the think-on-fire rider ---------------------

def test_store_down_returns_error_and_records_failure(_store):
    class DeadLedger:
        def init_schema(self):
            raise RuntimeError("pg refused")

    out = fire_grader.run_scheduled(ledger=DeadLedger())
    assert out["checked"] == 0 and out["error"] == "pg refused"
    assert any(row[2] == "grader_store" for row in _store.rows)


def test_assessments_ride_the_cron_when_trade_lore_works(monkeypatch):
    from utah.product import trade_lore
    monkeypatch.setattr(trade_lore, "run_assessments", lambda ledger: {"assessed": 2})
    out = fire_grader.run_scheduled(ledger=FakeGradeLedger())
    assert out["assessments"] == {"assessed": 2}


def test_assessment_failure_never_blocks_grading(monkeypatch):
    from utah.product import trade_lore

    def boom(ledger):
        raise RuntimeError("brain offline")

    monkeypatch.setattr(trade_lore, "run_assessments", boom)
    t0 = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    fire_ts = t0 + timedelta(seconds=15 * 20 + 1)
    closes = [100.0] * 20 + [101.0] + [103.5]
    lg = FakeGradeLedger(
        fires=[{"id": 13, "engine": "breakout", "direction": "long", "entry": 101.0,
                "ts": fire_ts, "symbol": "S"}],
        bars={"S": _bars(t0, closes)})
    out = fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(13, "target", 2.0)]              # grading landed
    assert "assessments" not in out                        # rider skipped, not faked


def test_graded_pnl_is_always_finite_or_none():
    """Sweep the grader over hostile inputs: every pnl it emits is finite or None."""
    cases = [
        ("long", 101.0, [100.0] * 20, [103.5]),
        ("short", 99.0, [100.0] * 20, [96.5]),
        ("long", float("nan"), [100.0] * 20, [103.5]),
        ("long", 101.0, [101.0] * 20, [103.5]),
        ("long", 101.0, [], [103.5]),
        ("long", 101.0, [100.0] * 20, []),
    ]
    for direction, entry, prior, post in cases:
        g = fire_grader.grade(direction, entry, prior, post)
        assert g["pnl"] is None or math.isfinite(g["pnl"])

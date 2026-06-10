"""Fire grader — fills outcome/pnl on recorded fires so the signal lane is MEASURABLE.

Convention under test (mirrors backtest.py): structural stop = opposite extreme of the
prior-20 closed-bar closes, target = 2R, per-bar close exits, 30-min timeout marked to
market. HONESTY: a fire with no persisted post-fire bars (everything before bar
persistence shipped) is outcome='ungradable' with pnl NULL — never an invented price.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from utah.product import fire_grader

UTC = timezone.utc


# --- pure grading math (synthetic bars; same mechanics as backtest._simulate_trade) ---

def test_long_target_hit_is_plus_2r_points():
    # prior window low 100 -> stop 100; entry 101 -> risk 1, target 103
    g = fire_grader.grade("long", 101.0, [100.0] * 20, [102.0, 103.5])
    assert g["outcome"] == "target" and g["pnl"] == 2.0


def test_long_stop_hit_is_minus_risk_points():
    g = fire_grader.grade("long", 101.0, [100.0] * 20, [100.4, 99.0])
    assert g["outcome"] == "stop" and g["pnl"] == -1.0


def test_short_target_and_stop_signs():
    # prior window high 100 -> stop 100; entry 99 -> risk 1, target 97
    g = fire_grader.grade("short", 99.0, [100.0] * 20, [98.0, 96.5])
    assert g["outcome"] == "target" and g["pnl"] == 2.0      # (entry - exit) for SHORT
    g = fire_grader.grade("short", 99.0, [100.0] * 20, [100.5])
    assert g["outcome"] == "stop" and g["pnl"] == -1.0


def test_timeout_marks_to_market_at_last_close():
    g = fire_grader.grade("long", 101.0, [100.0] * 20, [101.2, 101.4])
    assert g["outcome"] == "timeout" and abs(g["pnl"] - 0.4) < 1e-9


def test_ungradable_without_post_fire_bars_never_invents_a_price():
    g = fire_grader.grade("long", 101.0, [100.0] * 20, [])
    assert g["outcome"] == "ungradable" and g["pnl"] is None
    assert "post-fire" in g["reason"]


def test_ungradable_with_insufficient_prior_bars():
    g = fire_grader.grade("long", 101.0, [100.0] * 10, [102.0, 103.5], lookback=20)
    assert g["outcome"] == "ungradable" and g["pnl"] is None and "prior" in g["reason"]


def test_ungradable_on_zero_risk_window():
    g = fire_grader.grade("long", 101.0, [101.0] * 20, [102.0])
    assert g["outcome"] == "ungradable" and g["pnl"] is None


def test_ungradable_on_unknown_direction():
    g = fire_grader.grade("sideways", 101.0, [100.0] * 20, [102.0])
    assert g["outcome"] == "ungradable" and g["pnl"] is None


# --- run_scheduled orchestration (injectable ledger; UPDATE exactly once per fire) ---

class FakeGradeLedger:
    """Implements only the Ledger surface the grader uses, in memory."""

    def __init__(self, fires=(), bars=None):
        self.fires = list(fires)                  # dicts: id/engine/direction/entry/ts/symbol
        self.bars = bars or {}                    # {symbol: [(ts, close), ...]} chronological
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


def test_run_scheduled_grades_a_fire_with_real_bars():
    t0 = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    # the fire is recorded ~1s after the breakout bar (index 20, ts t0+300s) closes
    fire_ts = t0 + timedelta(seconds=15 * 20 + 1)
    # 20 prior bars at 100, breakout bar closes 101 (== entry), then runs to target 103
    closes = [100.0] * 20 + [101.0] + [102.0, 103.5]
    lg = FakeGradeLedger(
        fires=[{"id": 7, "engine": "breakout", "direction": "long", "entry": 101.0,
                "ts": fire_ts, "symbol": "CM.MNQM6"}],
        bars={"CM.MNQM6": _bars(t0, closes)})
    out = fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(7, "target", 2.0)]
    assert out["checked"] == 1 and out["target"] == 1 and out["errors"] == 0


def test_run_scheduled_marks_historical_fires_ungradable_when_no_bars_exist():
    """The 400+ pre-persistence fires: no bars table rows -> 'ungradable', pnl NULL."""
    lg = FakeGradeLedger(
        fires=[{"id": 1, "engine": "breakout", "direction": "long", "entry": 29000.0,
                "ts": datetime(2026, 6, 8, 18, 0, tzinfo=UTC), "symbol": None}])
    out = fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(1, "ungradable", None)]
    assert out["ungradable"] == 1


def test_run_scheduled_infers_symbol_only_when_unambiguous():
    t0 = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    fire_ts = t0 + timedelta(seconds=15 * 20 + 1)
    closes = [100.0] * 20 + [101.0] + [99.0]      # post bar through the stop -> 'stop'
    fire = {"id": 2, "engine": "breakout", "direction": "long", "entry": 101.0,
            "ts": fire_ts, "symbol": None}
    # exactly ONE symbol has bars in the window -> inferred, graded
    lg = FakeGradeLedger(fires=[dict(fire)], bars={"CM.MNQM6": _bars(t0, closes)})
    fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(2, "stop", -1.0)]
    # TWO candidate symbols -> ambiguous: never guess which prices to grade against
    lg2 = FakeGradeLedger(fires=[dict(fire)],
                          bars={"A": _bars(t0, closes), "B": _bars(t0, closes)})
    fire_grader.run_scheduled(ledger=lg2)
    assert lg2.graded == [(2, "ungradable", None)]


def test_run_scheduled_handles_missing_entry_and_store_errors_without_raising():
    lg = FakeGradeLedger(
        fires=[{"id": 3, "engine": "breakout", "direction": "long", "entry": None,
                "ts": datetime(2026, 6, 9, 12, 0, tzinfo=UTC), "symbol": "X"}])
    out = fire_grader.run_scheduled(ledger=lg)
    assert lg.graded == [(3, "ungradable", None)]

    class Broken(FakeGradeLedger):
        def bars_before(self, *a):  # a store hiccup must not kill the cron
            raise RuntimeError("pg down")

    lg2 = Broken(fires=[{"id": 4, "engine": "breakout", "direction": "long",
                         "entry": 1.0, "ts": datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
                         "symbol": "X"}], bars={"X": []})
    out2 = fire_grader.run_scheduled(ledger=lg2)
    assert out2["errors"] == 1 and lg2.graded == []

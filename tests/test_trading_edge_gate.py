"""Edge-gated firing + per-archetype fire geometry.

Why this exists (data, 2026-06-12): the live engine fired the naked 20-bar
breakout BLIND — 1,461 fires, 21.5% hit on a 2R target (needs ~33% just to break
even), net −4,384 pts. Meanwhile the mean-reversion engine, which the OOS sweep
shows has real edge on index futures (NQ/MNQ: 0.83–0.96 win, +200..+576 pts OOS),
fired with BREAKOUT geometry (structural stop + 2R target) because `_fire_context`
applied one shape to every engine — so its live fires could never capture the
edge its own backtest proves.

Two fixes, both pinned here:
  1. `edge_ok(engine, symbol)` — a fire records only when THAT engine currently
     proves positive held-out OOS edge on THAT symbol's real bars. No proof → no
     fire. This stops the breakout bleed and only fires what the data earns.
  2. `_fire_context` is archetype-aware — mean-reversion fires get a tight target
     toward the rolling mean and a wide z-based stop (the geometry mr_backtest
     proves), not the breakout structural stop / 2R target.
All boundaries injected — no DB, no feed, no real backtest in unit tests.
"""
from __future__ import annotations

from utah import failures
from utah.product import trading
from tests.fakes import FakeFailureStore


# ── per-archetype fire geometry ──────────────────────────────────────────────

def test_fire_context_breakout_geometry_unchanged():
    closes = [10.0] * 20 + [12.0]
    sig = {"engine": "breakout", "direction": "long", "entry": 12.0}
    ctx = trading._fire_context(closes, sig, lookback=20)
    assert ctx["stop"] == 10.0                          # structural: window low
    assert ctx["target"] == 12.0 + 2.0 * (12.0 - 10.0)  # entry + 2R risk
    assert "breakout" in ctx["rationale"]


def test_fire_context_meanrev_uses_tight_target_wide_stop():
    # prior window: mean 10, sd 0.5 (needs real variance); last bar an extreme low → fade LONG
    closes = [9.5, 10.5] * 10 + [8.0]
    sig = {"engine": "meanrev", "direction": "long", "entry": 8.0}
    ctx = trading._fire_context(closes, sig, lookback=20)
    # target is BETWEEN entry and the mean (tight, toward reversion), not 2R away
    assert 8.0 < ctx["target"] < 10.0
    # stop is BELOW entry (long) and FARTHER than the target distance (wide)
    assert ctx["stop"] < 8.0
    assert (8.0 - ctx["stop"]) > (ctx["target"] - 8.0)
    assert "mean-revert" in ctx["rationale"] or "z=" in ctx["rationale"]


def test_fire_context_meanrev_short_mirrors():
    closes = [9.5, 10.5] * 10 + [12.0]
    sig = {"engine": "meanrev", "direction": "short", "entry": 12.0}
    ctx = trading._fire_context(closes, sig, lookback=20)
    assert 10.0 < ctx["target"] < 12.0      # tight, toward the mean (below entry)
    assert ctx["stop"] > 12.0               # wide, above entry
    assert (ctx["stop"] - 12.0) > (12.0 - ctx["target"])


# ── the edge gate ────────────────────────────────────────────────────────────

def _proven(engine, bars):
    return {"engine": engine, "edge_proven": True, "win_rate": 0.9,
            "net_pts": 400.0, "trades": 30, "reason": "edge proven (test)"}


def _unproven(engine, bars):
    return {"engine": engine, "edge_proven": False, "win_rate": 0.21,
            "net_pts": -50.0, "trades": 30, "reason": "no edge (test)"}


def test_edge_ok_true_when_backtest_proves_edge():
    v = trading.edge_ok("meanrev", "CM.NQM6",
                        ohlc_fn=lambda s: [(1, 1, 1, 1)] * 50, score_fn=_proven)
    assert v["ok"] is True and v["scorecard"]["edge_proven"] is True


def test_edge_ok_false_when_unproven():
    v = trading.edge_ok("breakout", "US.SPY",
                        ohlc_fn=lambda s: [(1, 1, 1, 1)] * 50, score_fn=_unproven)
    assert v["ok"] is False and "no edge" in v["reason"].lower()


def test_edge_ok_false_on_empty_bars_never_fires_blind():
    v = trading.edge_ok("breakout", "US.SPY", ohlc_fn=lambda s: [], score_fn=_proven)
    assert v["ok"] is False                              # no bars → no proven edge → no fire


def test_edge_ok_caches_within_ttl():
    calls = []
    def counting(engine, bars):
        calls.append(engine)
        return _proven(engine, bars)
    clock = [1000.0]
    trading._EDGE_CACHE.clear()
    a = trading.edge_ok("meanrev", "CM.NQM6", ohlc_fn=lambda s: [(1, 1, 1, 1)] * 50,
                        score_fn=counting, ttl_s=900, now=lambda: clock[0])
    b = trading.edge_ok("meanrev", "CM.NQM6", ohlc_fn=lambda s: [(1, 1, 1, 1)] * 50,
                        score_fn=counting, ttl_s=900, now=lambda: clock[0] + 100)
    assert a["ok"] is b["ok"] is True
    assert len(calls) == 1                               # second call served from cache
    clock[0] += 2000                                     # past TTL → recompute
    trading.edge_ok("meanrev", "CM.NQM6", ohlc_fn=lambda s: [(1, 1, 1, 1)] * 50,
                    score_fn=counting, ttl_s=900, now=lambda: clock[0])
    assert len(calls) == 2


# ── the gate wired into run() ────────────────────────────────────────────────

class _RecLedger:
    def __init__(self):
        self.fires = []
    def fire_state(self, engine):
        return {"open": False, "last_fire_age_s": None}
    def record_fire(self, engine, direction, entry=None, synthetic=False, **kw):
        self.fires.append((engine, direction, entry, kw))
        return len(self.fires)


def test_run_suppresses_fire_without_proven_edge():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    closes = [10.0] * 20 + [12.5]                        # a real breakout signal
    r = trading.run(lg, feed_fn=lambda: closes, symbol="US.SPY",
                    edge_fn=lambda eng, sym: {"ok": False, "reason": "no edge (test)"})
    assert r["fires"] == 0 and r["suppressed"] == "no_edge"
    assert lg.fires == []                                # signal present, but unproven → no bet


def test_run_fires_when_edge_proven():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    closes = [10.0] * 20 + [12.5]
    r = trading.run(lg, feed_fn=lambda: closes, symbol="US.SPY",
                    edge_fn=lambda eng, sym: {"ok": True, "reason": "proven (test)"})
    assert r["fires"] == 1 and lg.fires[0][0] == "breakout"


def test_run_edge_gate_off_preserves_legacy_unit_contract():
    # edge_fn=None AND no symbol → gate cannot evaluate a stream → legacy behavior
    # (fire on signal). Keeps the pipeline-mechanics tests meaningful.
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    closes = [10.0] * 20 + [12.5]
    r = trading.run(lg, feed_fn=lambda: closes)          # no symbol, no edge_fn
    assert r["fires"] == 1

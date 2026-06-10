"""Backtest harness — proves the breakout engine's edge with per-bar stop/target exits,
the gate that keeps trading frozen at one paper engine until the data earns more."""
from __future__ import annotations

from utah.product import backtest


# --- single-trade mechanics (per-bar stop/target exit) ----------------------

def test_simulate_long_hits_target_is_plus_target_r():
    # entry 101, stop 100 (risk 1), 2R target = 103; price runs up through it
    closes = [101.0, 102.0, 103.0, 104.0]
    t = backtest._simulate_trade(closes, 0, "long", 101.0, 100.0, target_r=2.0)
    assert t.outcome == "target" and t.r == 2.0


def test_simulate_long_hits_stop_is_minus_one_r():
    closes = [101.0, 100.5, 99.0]
    t = backtest._simulate_trade(closes, 0, "long", 101.0, 100.0, target_r=2.0)
    assert t.outcome == "stop" and t.r == -1.0


def test_simulate_short_mechanics():
    closes = [99.0, 98.0, 97.0]   # short entry 99, stop 100, 2R target = 97
    t = backtest._simulate_trade(closes, 0, "short", 99.0, 100.0, target_r=2.0)
    assert t.outcome == "target" and t.r == 2.0


def test_simulate_runs_to_eod_when_neither_hit():
    closes = [101.0, 101.2, 101.4]   # drifts up but never reaches 103 or 100
    t = backtest._simulate_trade(closes, 0, "long", 101.0, 100.0, target_r=2.0)
    assert t.outcome == "eod" and -1.0 < t.r < 2.0


# --- aggregate backtest -----------------------------------------------------

def test_no_trades_on_a_flat_series():
    r = backtest.backtest([100.0] * 50, lookback=20)
    assert r.trades == 0 and r.edge_proven is False


def test_clean_uptrend_after_breakout_shows_positive_expectancy():
    closes = [100.0] * 20 + [101.0, 102.0, 103.0, 104.0, 105.0]
    r = backtest.backtest(closes, lookback=20, target_r=2.0, min_trades=1)
    assert r.wins >= 1 and r.expectancy_r > 0 and r.edge_proven is True


def test_edge_not_proven_without_a_real_sample():
    closes = [100.0] * 20 + [101.0, 102.0, 103.0, 104.0, 105.0]
    r = backtest.backtest(closes, lookback=20, target_r=2.0, min_trades=30)
    assert r.edge_proven is False and "insufficient" in r.reason


def test_metrics_are_coherent():
    closes = list(range(100, 80, -1)) + [120, 121, 122, 123, 124, 125]
    r = backtest.backtest(closes, lookback=10, target_r=2.0, min_trades=1)
    assert 0.0 <= r.win_rate <= 1.0
    assert r.max_drawdown_r <= 0.0 and r.profit_factor >= 0.0


def test_prove_edge_returns_a_dict_verdict():
    v = backtest.prove_edge([100.0] * 20 + [101, 102, 103, 104, 105], lookback=20, min_trades=1)
    assert set(v) >= {"trades", "expectancy_r", "edge_proven", "reason", "max_drawdown_r"}

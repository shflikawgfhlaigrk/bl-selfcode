"""Backtest input guards + degenerate-series honesty.

The harness is pure and deterministic, so a nonsense parameter must fail LOUDLY with a
named ValueError (not a ZeroDivisionError three frames deep, not a silently-empty
result), and a degenerate series must produce the honest no-trades verdict — never a
fabricated edge."""
from __future__ import annotations

import pytest

from utah.product import backtest


# --- parameter guards: loud, named, immediate ---------------------------------

def test_backtest_rejects_nonpositive_lookback():
    with pytest.raises(ValueError, match="lookback"):
        backtest.backtest([100.0] * 30, lookback=0)
    with pytest.raises(ValueError, match="lookback"):
        backtest.backtest([100.0] * 30, lookback=-5)


def test_backtest_rejects_nonpositive_target_r_and_min_trades():
    with pytest.raises(ValueError, match="target_r"):
        backtest.backtest([100.0] * 30, target_r=0.0)
    with pytest.raises(ValueError, match="min_trades"):
        backtest.backtest([100.0] * 30, min_trades=0)


def test_mr_backtest_rejects_each_bad_parameter():
    bars = [(100.0, 100.1, 99.9, 100.0)] * 30
    for kw, msg in (({"lookback": 0}, "lookback"),
                    ({"z_enter": 0.0}, "z_enter"),
                    ({"tgt_frac": -0.1}, "tgt_frac"),
                    ({"stop_mult": 0.0}, "stop_mult"),
                    ({"max_hold": -1}, "max_hold"),
                    ({"min_trades": 0}, "min_trades")):
        with pytest.raises(ValueError, match=msg):
            backtest.mr_backtest(bars, **kw)


def test_prove_meanrev_rejects_out_of_range_oos_frac():
    bars = [(100.0, 100.1, 99.9, 100.0)] * 30
    for frac in (0.0, 1.0, 1.5, -0.2):
        with pytest.raises(ValueError, match="oos_frac"):
            backtest.prove_meanrev(bars, oos_frac=frac)


# --- degenerate series: honest empties, never a fabricated edge -----------------

def test_empty_and_short_series_yield_honest_no_trades():
    for closes in ([], [100.0] * 5):           # shorter than the lookback window
        r = backtest.backtest(closes, lookback=20)
        assert r.trades == 0 and r.edge_proven is False
        assert "no trades" in r.reason


def test_prove_meanrev_on_empty_bars_is_not_proven():
    v = backtest.prove_meanrev([], lookback=20)
    assert v["edge_proven"] is False
    assert v["out_of_sample"]["trades"] == 0
    assert "not proven" in v["reason"]


# --- metric coherence on the win-only edge ---------------------------------------

def test_profit_factor_with_no_losses_is_the_gross_win():
    # one clean breakout straight to target: gross loss 0 -> PF = gross win (2R), not inf
    closes = [100.0] * 20 + [101.0, 103.0]
    r = backtest.backtest(closes, lookback=20, target_r=2.0, min_trades=1)
    assert r.losses == 0 and r.profit_factor == pytest.approx(2.0)
    assert r.max_drawdown_r == 0.0

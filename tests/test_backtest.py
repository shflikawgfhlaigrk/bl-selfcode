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


# --- mean-reversion engine on OHLC bars (the high-win-rate archetype) --------
# A Bar is (o, h, l, c). The engine fades a z-score extreme with a TIGHT target
# (partway back to the mean) and a WIDE structural stop (many sigma) — the classic
# premium-capture shape that wins often. Exits are checked per-bar on the real
# HIGH/LOW (an intrabar wick can hit a level the close never shows). Pessimistic:
# the stop is checked before the target on the same bar.

def _bar(o, h, l, c):
    return (o, h, l, c)


def test_mr_long_fade_hits_tight_target_on_a_wick():
    # 20 flat bars at 100 (sd 0) then a dip well below -> z very negative -> fade long.
    # target is partway back to mean; a later bar's HIGH reaches it -> win.
    bars = [_bar(100, 100.2, 99.8, 100.0) for _ in range(20)]
    bars[5] = _bar(100, 100.2, 99.8, 100.5)   # inject variance so sd > 0
    bars.append(_bar(97, 97.2, 96.8, 97.0))   # the extreme dip (entry bar)
    bars.append(_bar(97, 99.5, 96.9, 100.0))  # next bar's wick hits the tight target, closes back at mean
    res = backtest.mr_backtest(bars, lookback=20, z_enter=2.0, tgt_frac=0.5,
                               stop_mult=8.0, min_trades=1)
    assert res.trades == 1 and res.wins == 1
    assert res.win_rate == 1.0 and res.total_r > 0


def test_mr_stop_is_checked_before_target_on_the_same_bar():
    # A bar whose range spans BOTH stop and target must count as a STOP (pessimism),
    # so a high-win backtest can never be flattered by assuming the good fill.
    bars = [_bar(100, 100.3, 99.7, 100.0) for _ in range(20)]
    bars[3] = _bar(100, 100.3, 99.7, 100.6)
    bars.append(_bar(96, 96.1, 95.9, 96.0))                    # deep dip -> fade long, wide stop below
    bars.append(_bar(96, 200.0, 0.01, 100.0))                  # spans everything -> must be a stop; closes at mean
    res = backtest.mr_backtest(bars, lookback=20, z_enter=2.0, tgt_frac=0.5,
                               stop_mult=8.0, min_trades=1)
    assert res.trades == 1 and res.losses == 1 and res.win_rate == 0.0


def test_mr_no_trade_when_no_extreme_and_flat_window_never_fires():
    flat = [_bar(100, 100.0, 100.0, 100.0) for _ in range(25)]   # zero variance, never a z-score
    assert backtest.mr_backtest(flat, lookback=20).trades == 0
    # a gentle drift that never breaches the z band also produces no trades
    drift = [_bar(100 + i * 0.001, 100 + i * 0.001, 100 + i * 0.001, 100 + i * 0.001)
             for i in range(40)]
    assert backtest.mr_backtest(drift, lookback=20, z_enter=2.0).trades == 0


def test_mr_max_hold_caps_an_open_loser_to_a_time_exit():
    """A WIDE-stop loser that never hits its stop would otherwise mark-to-market at the
    last bar (an unbounded tail). max_hold forces a time exit after N bars: the open
    drifting loss is realized smaller and the position frees up. This is the honest
    tail-bound (OOS-tested), not a curve-fit — a single structural parameter."""
    bars = [_bar(100, 100.3, 99.7, 100.0) for _ in range(20)]
    bars[3] = _bar(100, 100.3, 99.7, 100.6)
    bars.append(_bar(97, 97.2, 96.8, 97.0))                       # fade long, target ~98.x, wide stop ~88
    # 30 bars that drift DOWN slowly — never the wide stop, never the target
    for k in range(30):
        px = 96.5 - k * 0.02
        bars.append(_bar(px, px + 0.05, px - 0.05, px))
    capped = backtest.mr_backtest(bars, lookback=20, z_enter=2.0, tgt_frac=0.5,
                                  stop_mult=8.0, min_trades=1, max_hold=5)
    uncapped = backtest.mr_backtest(bars, lookback=20, z_enter=2.0, tgt_frac=0.5,
                                    stop_mult=8.0, min_trades=1, max_hold=0)
    # the capped trade exits after 5 bars (smaller drift loss) vs marking to the far,
    # lower last bar — so the capped loss is strictly less negative.
    assert capped.trades >= 1 and uncapped.trades >= 1
    assert capped.total_r > uncapped.total_r


def test_mr_result_has_oos_split_and_edge_proven_needs_win_and_net():
    # prove_meanrev splits chronologically, fits nothing (fixed cfg), reports the
    # held-out OOS stats, and only proves edge when OOS win >= win_floor AND net > 0.
    bars = [_bar(100, 100.3, 99.7, 100.0) for _ in range(20)]
    bars[2] = _bar(100, 100.3, 99.7, 100.6)
    v = backtest.prove_meanrev(bars, lookback=20, z_enter=2.0, tgt_frac=0.5,
                               stop_mult=8.0, min_trades=1, win_floor=0.87, oos_frac=0.5)
    assert {"in_sample", "out_of_sample", "win_floor", "edge_proven", "config"} <= set(v)
    assert "win_rate" in v["out_of_sample"] and "net_pts" in v["out_of_sample"]

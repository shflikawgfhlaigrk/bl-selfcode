"""Backtest harness — prove the breakout engine has EDGE before a 2nd engine exists.

The audit's standing rule (and the post-mortem's): the live engine is a naked 20-bar
breakout with no stop/target/sizing and no proof of edge — so the next dollar of effort is
a backtest that PROVES edge, not a tenth engine. This is that harness: pure, deterministic,
no I/O. It walks a closed-bar series, enters on the same :func:`trading.evaluate` breakout
with a STRUCTURAL stop (the opposite side of the breakout window) and an R-multiple target,
checks the stop/target on EVERY subsequent bar (the per-bar exit the live engine lacks), and
reports edge metrics in R (risk units): win rate, expectancy, profit factor, max drawdown.

``edge_proven`` is True only with a real sample AND positive expectancy — the gate that
keeps trading frozen at one paper engine until the data earns more.
"""
from __future__ import annotations

import msgspec


class Trade(msgspec.Struct, frozen=True):
    """One simulated trade, outcome measured in R (multiples of the risked stop distance)."""
    direction: str
    entry: float
    stop: float
    target: float
    exit: float
    bars_held: int
    r: float          # realized return in R (+target_r on a win, -1.0 on a stop, partial on EOD)
    outcome: str      # "target" | "stop" | "eod"


class BacktestResult(msgspec.Struct, frozen=True):
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_r: float
    expectancy_r: float       # mean R per trade — the headline edge number
    profit_factor: float      # gross win R / gross loss R
    total_r: float
    max_drawdown_r: float
    edge_proven: bool
    reason: str


def _simulate_trade(closes, i, direction, entry, stop, target_r):
    """Walk forward from bar i, exiting on the FIRST bar that hits stop or target (per-bar
    exit). Returns (Trade) or None if the series ends first (closed at the last bar = EOD)."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + target_r * risk if direction == "long" else entry - target_r * risk
    for j in range(i + 1, len(closes)):
        px = closes[j]
        if direction == "long":
            if px <= stop:
                return Trade(direction, entry, stop, target, stop, j - i, -1.0, "stop")
            if px >= target:
                return Trade(direction, entry, stop, target, target, j - i, target_r, "target")
        else:
            if px >= stop:
                return Trade(direction, entry, stop, target, stop, j - i, -1.0, "stop")
            if px <= target:
                return Trade(direction, entry, stop, target, target, j - i, target_r, "target")
    # ran out of bars → mark-to-market at the last close (partial R, never fabricated)
    last = closes[-1]
    r = (last - entry) / risk if direction == "long" else (entry - last) / risk
    return Trade(direction, entry, stop, target, last, len(closes) - 1 - i, round(r, 4), "eod")


def backtest(closes, *, lookback: int = 20, target_r: float = 2.0,
             min_trades: int = 30) -> BacktestResult:
    """Backtest the breakout engine over *closes*. STRUCTURAL stop = the opposite side of the
    breakout window (a long stops at the window low); target = ``target_r`` × the risk. One
    position at a time (no pyramiding). Returns edge metrics in R; ``edge_proven`` needs a
    real sample AND positive expectancy."""
    trades: list[Trade] = []
    i = lookback
    n = len(closes)
    while i < n:
        prior = closes[i - lookback:i]
        last = closes[i]
        direction = stop = None
        if last > max(prior):
            direction, stop = "long", min(prior)
        elif last < min(prior):
            direction, stop = "short", max(prior)
        if direction is None:
            i += 1
            continue
        t = _simulate_trade(closes, i, direction, last, stop, target_r)
        if t is None:
            i += 1
            continue
        trades.append(t)
        i += max(1, t.bars_held)   # one position at a time — resume after the exit
    return _summarize(trades, min_trades)


def _summarize(trades: "list[Trade]", min_trades: int) -> BacktestResult:
    n = len(trades)
    if n == 0:
        return BacktestResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False,
                              "no trades triggered on this series")
    wins = sum(1 for t in trades if t.r > 0)
    losses = sum(1 for t in trades if t.r < 0)
    rs = [t.r for t in trades]
    total_r = sum(rs)
    expectancy = total_r / n
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (gross_win or 0.0)
    # max drawdown on the cumulative-R equity curve
    peak = cum = mdd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    edge = n >= min_trades and expectancy > 0
    reason = ("edge proven: positive expectancy on a real sample" if edge else
              f"insufficient: {n} trades (need ≥{min_trades}), expectancy {expectancy:.3f}R"
              if n < min_trades else f"no edge: expectancy {expectancy:.3f}R ≤ 0")
    return BacktestResult(
        trades=n, wins=wins, losses=losses, win_rate=round(wins / n, 4),
        avg_r=round(expectancy, 4), expectancy_r=round(expectancy, 4),
        profit_factor=round(profit_factor, 4), total_r=round(total_r, 4),
        max_drawdown_r=round(mdd, 4), edge_proven=edge, reason=reason)


def prove_edge(closes, **kw) -> dict:
    """Run the backtest and return a plain dict verdict for the deck / a gate. Trading stays
    frozen at one paper engine until ``edge_proven`` is True on real data."""
    r = backtest(closes, **kw)
    return msgspec.structs.asdict(r)


__all__ = ["Trade", "BacktestResult", "backtest", "prove_edge"]

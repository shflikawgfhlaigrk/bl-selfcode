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


def _require(cond: bool, msg: str) -> None:
    """Loud, named parameter guard. The harness is pure, so a nonsense parameter is a
    programming error — fail immediately with the offending name, never a
    ZeroDivisionError (lookback=0) or a silently-empty result three frames deep."""
    if not cond:
        raise ValueError(msg)


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
    _require(lookback >= 1, f"lookback must be >= 1, got {lookback}")
    _require(target_r > 0, f"target_r must be > 0, got {target_r}")
    _require(min_trades >= 1, f"min_trades must be >= 1, got {min_trades}")
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


# --- mean-reversion on OHLC bars (the high-win-rate archetype) ---------------
# Breakout (above) is a low-win / high-R-tail trend engine. THIS is its opposite:
# fade a z-score extreme back toward the rolling mean with a TIGHT target and a
# WIDE structural stop — the premium-capture shape that wins OFTEN (each trade
# risks a lot to make a little, so it must clear a high win bar to be net-positive).
# It runs on the live 15s bars (utah `bars` table) with a held-out OOS split; the
# real win%/net are whatever this function MEASURES at call time and are NOT written
# here as literals — the live feed keeps appending bars, so any hardcoded figure would
# drift and become a lie. The honest tail: R-expectancy hovers near 0 because a rare
# wide-stop loser costs ~8R, so the WIN RATE (not the R) is the edge — and a thin or
# negative net is reported honestly via edge_proven=False, never painted over.


def _bar_closes(bars):
    return [b[3] for b in bars]


def _mr_simulate(bars, i, direction, entry, stop, target, max_hold: int = 0):
    """Walk forward from entry bar *i*, exiting on the FIRST bar whose real HIGH/LOW
    touches a level. PESSIMISTIC: a bar that spans both levels counts as the STOP, so
    a high-win backtest is never flattered by assuming the good intrabar fill.

    ``max_hold`` (>0) bounds the holding period: a trade still open after *max_hold*
    bars takes a TIME exit (mark-to-market at that bar), realizing a drifting loss
    smaller than an unbounded mark-to-the-last-bar and freeing the position. This is
    the honest tail-bound — a structural cap, OOS-tested, not a curve-fit.

    Returns a :class:`Trade` (r in risk units, pnl recoverable as r*risk)."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target_r = abs(target - entry) / risk
    end = len(bars) if max_hold <= 0 else min(len(bars), i + 1 + max_hold)
    for j in range(i + 1, end):
        hi, lo = bars[j][1], bars[j][2]
        if direction == "long":
            if lo <= stop:
                return Trade(direction, entry, stop, target, stop, j - i, -1.0, "stop")
            if hi >= target:
                return Trade(direction, entry, stop, target, target, j - i, target_r, "target")
        else:
            if hi >= stop:
                return Trade(direction, entry, stop, target, stop, j - i, -1.0, "stop")
            if lo <= target:
                return Trade(direction, entry, stop, target, target, j - i, target_r, "target")
    # time exit (max_hold reached) or end-of-series: mark-to-market at the exit bar
    k = end - 1
    last = bars[k][3]
    held = k - i
    outcome = "timecap" if (max_hold > 0 and k == i + max_hold) else "eod"
    r = (last - entry) / risk if direction == "long" else (entry - last) / risk
    return Trade(direction, entry, stop, target, last, held, round(r, 4), outcome)


def mr_backtest(bars, *, lookback: int = 20, z_enter: float = 2.0, tgt_frac: float = 0.6,
                stop_mult: float = 8.0, min_trades: int = 30,
                max_hold: int = 0) -> BacktestResult:
    """Backtest the mean-reversion engine over OHLC *bars* (tuples ``(o,h,l,c)``).

    On each bar: rolling mean/sd over the prior *lookback* closes; if the close is
    ``>= z_enter`` sigma BELOW the mean fade LONG (``<=`` ABOVE -> SHORT). Target =
    ``tgt_frac`` of the distance back to the mean (tight). Stop = ``stop_mult`` sigma
    beyond entry (wide, structural). ``max_hold`` (>0) time-exits a trade still open
    after that many bars (bounds the wide-stop tail). One position at a time.
    Zero-variance windows can never fire (no fabricated z-score)."""
    _validate_mr(lookback=lookback, z_enter=z_enter, tgt_frac=tgt_frac,
                 stop_mult=stop_mult, min_trades=min_trades, max_hold=max_hold)
    return _summarize(_mr_trades(bars, lookback=lookback, z_enter=z_enter,
                                 tgt_frac=tgt_frac, stop_mult=stop_mult,
                                 min_trades=min_trades, max_hold=max_hold), min_trades)


def prove_meanrev(bars, *, lookback: int = 20, z_enter: float = 2.0, tgt_frac: float = 0.6,
                  stop_mult: float = 8.0, min_trades: int = 30, win_floor: float = 0.87,
                  oos_frac: float = 0.4, max_hold: int = 0) -> dict:
    """Fit NOTHING — run the FIXED mean-reversion cfg on a chronological in-sample /
    out-of-sample split and report the held-out stats. ``edge_proven`` is True only when
    the OUT-OF-SAMPLE win rate clears *win_floor* AND OOS net points are positive on a
    real sample. The deck and any gate read THIS, never an in-sample number. ``max_hold``
    bounds the holding period (the wide-stop tail-cap)."""
    _validate_mr(lookback=lookback, z_enter=z_enter, tgt_frac=tgt_frac,
                 stop_mult=stop_mult, min_trades=min_trades, max_hold=max_hold)
    _require(0.0 < oos_frac < 1.0, f"oos_frac must be in (0, 1), got {oos_frac}")
    _require(0.0 < win_floor <= 1.0, f"win_floor must be in (0, 1], got {win_floor}")
    bars = list(bars)
    split = int(len(bars) * (1.0 - oos_frac))
    cfg = dict(lookback=lookback, z_enter=z_enter, tgt_frac=tgt_frac,
               stop_mult=stop_mult, min_trades=min_trades, max_hold=max_hold)

    def _stats(seg) -> dict:
        r = mr_backtest(seg, **cfg)
        net_pts = round(sum((t.exit - t.entry) if t.direction == "long"
                            else (t.entry - t.exit) for t in _mr_trades(seg, **cfg)), 4)
        return {"trades": r.trades, "wins": r.wins, "losses": r.losses,
                "win_rate": r.win_rate, "total_r": r.total_r,
                "expectancy_r": r.expectancy_r, "net_pts": net_pts,
                "max_drawdown_r": r.max_drawdown_r}

    in_s = _stats(bars[:split])
    oos = _stats(bars[split:])
    edge = (oos["trades"] >= min_trades and oos["win_rate"] >= win_floor
            and oos["net_pts"] > 0)
    return {"config": cfg, "win_floor": win_floor, "oos_frac": oos_frac,
            "in_sample": in_s, "out_of_sample": oos, "edge_proven": edge,
            "reason": (f"edge proven: OOS win {oos['win_rate']:.1%} >= {win_floor:.0%} "
                       f"and net +{oos['net_pts']:.2f} pts on {oos['trades']} trades"
                       if edge else
                       f"not proven: OOS win {oos['win_rate']:.1%} / net {oos['net_pts']:.2f} pts "
                       f"on {oos['trades']} trades (need win>={win_floor:.0%}, net>0, "
                       f">={min_trades} trades)")}


def _validate_mr(*, lookback, z_enter, tgt_frac, stop_mult, min_trades, max_hold) -> None:
    """One guard for both mean-reversion entrypoints (mr_backtest / prove_meanrev)."""
    _require(lookback >= 1, f"lookback must be >= 1, got {lookback}")
    _require(z_enter > 0, f"z_enter must be > 0, got {z_enter}")
    _require(tgt_frac > 0, f"tgt_frac must be > 0, got {tgt_frac}")
    _require(stop_mult > 0, f"stop_mult must be > 0, got {stop_mult}")
    _require(min_trades >= 1, f"min_trades must be >= 1, got {min_trades}")
    _require(max_hold >= 0, f"max_hold must be >= 0, got {max_hold}")


def _mr_trades(bars, *, lookback, z_enter, tgt_frac, stop_mult, min_trades=0, max_hold=0):
    """The shared mean-reversion walk → the list of :class:`Trade` objects. Used both by
    :func:`mr_backtest` (summary in R) and :func:`prove_meanrev` (to also report signed
    points). ``min_trades`` is accepted-and-ignored here (it gates the summary, not the
    walk) so the same kwargs flow through unchanged."""
    closes = _bar_closes(bars)
    out: list[Trade] = []
    i = lookback
    n = len(bars)
    while i < n:
        prior = closes[i - lookback:i]
        mean = sum(prior) / lookback
        var = sum((c - mean) ** 2 for c in prior) / lookback
        if var <= 0.0:
            i += 1
            continue
        sd = var ** 0.5
        entry = closes[i]
        z = (entry - mean) / sd
        if z <= -z_enter:
            direction, target, stop = "long", entry + tgt_frac * (mean - entry), entry - stop_mult * sd
        elif z >= z_enter:
            direction, target, stop = "short", entry - tgt_frac * (entry - mean), entry + stop_mult * sd
        else:
            i += 1
            continue
        t = _mr_simulate(bars, i, direction, entry, stop, target, max_hold=max_hold)
        if t is None:
            i += 1
            continue
        out.append(t)
        i += max(1, t.bars_held)
    return out


__all__ = ["Trade", "BacktestResult", "backtest", "prove_edge",
           "mr_backtest", "prove_meanrev"]

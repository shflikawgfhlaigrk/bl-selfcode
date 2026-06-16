"""Research composite scorer — the Perplexity/Antigravity "Sniper" logic, ported to OHLC.

The ~/debt research bundles distill to ONE strategy: a weighted composite of trend /
level / volume / intelligence / momentum / session / smt components, gated and graded,
with a 5-voter direction ensemble (see ``Research_Backed_Engine/engine/modules/
sniper_engine.py``). Utah's ``bars`` table is OHLC only — no tick stream — so four of
those seven components (volume/CVD, order-flow absorption, SMT divergence, the intraday
session DNA) CANNOT be computed without fabricating their inputs. This module implements
the components OHLC genuinely supports and renormalizes the weights over them:

    trend     0.35  — EMA ribbon (fast>slow) + slow-EMA slope (the StepGMA proxy)
    level     0.25  — proximity to the rolling VWAP proxy on the correct side
    momentum  0.15  — rate-of-change aligned with the signal
    regime    0.25  — variance-ratio (Hurst proxy) TREND/REVERT + a return-entropy gate

The gated components are documented, not faked — when Utah captures a tick/volume feed
they get added back and the weights re-normalized. Pure and deterministic: same closes
in → same verdict out, so the live signal and the OOS backtest agree (the edge gate
depends on that). No I/O, never raises on thin input (returns an honest FLAT).
"""
from __future__ import annotations

import math

#: Component weights, renormalized over the OHLC-computable subset (sum == 1.0). The
#: original 7-component weights (trend .25/level .20/volume .20/intel .15/momentum .10/
#: session .05/smt .05) drop volume+intel-absorption+session+smt — gated on tick data
#: Utah's bars table lacks — and the remaining mass is renormalized here.
WEIGHTS = {"trend": 0.35, "level": 0.25, "momentum": 0.15, "regime": 0.25}

FAST, SLOW = 8, 21               #: EMA ribbon spans
LEVEL_LOOKBACK = 20              #: window for the VWAP proxy / volatility ref
ROC_K = 5                        #: momentum rate-of-change horizon (bars)
VR_K = 5                         #: variance-ratio horizon for the Hurst-proxy regime
ENTRY_THRESHOLD = 0.55           #: composite ≥ this (×100) grades B and fires
STRONG_THRESHOLD = 0.80          #: composite ≥ this (×100) grades A
MIN_CONSENSUS = 3                #: voters that must agree on a side (of 5)
ENTROPY_BLOCK = 0.92             #: normalized return-entropy above this = pure noise → gate
VR_REVERT_GATE = 0.9             #: variance-ratio below this = a mean-reverting regime; a
                                 #: trend-CONTINUATION engine stands down (that's meanrev's
                                 #: regime, and the research engine's Hurst gate does the same)

_WARMUP = SLOW + 2               #: fewest closes the scorer needs before it will speak
#: trailing closes the scorer is fed per decision. 200 ≫ 8×the slow EMA span, so the
#: EMA ribbon is fully warmed and a bounded-window read matches a full-history read to
#: float precision — the live rule and the OOS backtest pass the SAME slice, so they
#: produce the SAME signal (the edge gate depends on that). Keeps the walk O(n·RWIN).
RESEARCH_RWIN = 200


def _ema_series(values: list[float], span: int) -> list[float]:
    k = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _stdev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    return (sum((v - m) ** 2 for v in values) / n) ** 0.5


def _variance_ratio(closes: list[float], k: int) -> float:
    """VR = var(k-bar returns) / (k · var(1-bar returns)). >1 trends, <1 mean-reverts.
    1.0 (neutral) when there isn't enough data. A zero-variance return series is the
    degenerate perfect trend (all equal nonzero drift) → strong trend (2.0); a flat
    series (all-zero returns) → neutral (1.0)."""
    if len(closes) < k + 2:
        return 1.0
    r1 = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    rk = [closes[i] - closes[i - k] for i in range(k, len(closes))]
    v1 = _stdev(r1) ** 2
    vk = _stdev(rk) ** 2
    if v1 <= 0.0:
        nonzero = [r for r in r1 if r != 0.0]
        if nonzero and all(r > 0 for r in nonzero) or nonzero and all(r < 0 for r in nonzero):
            return 2.0   # perfect monotonic drift = the strongest possible trend
        return 1.0
    return vk / (k * v1)


def _return_entropy(closes: list[float], bins: int = 8) -> float:
    """Normalized Shannon entropy (0..1) of the recent 1-bar returns. ~0 = orderly
    (one direction), ~1 = uniform noise. A noise gate, not a signal."""
    rets = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    rets = [r for r in rets if r == r]  # drop NaN defensively
    if len(rets) < 2:
        return 0.0
    lo, hi = min(rets), max(rets)
    if hi <= lo:
        return 0.0
    width = (hi - lo) / bins
    counts = [0] * bins
    for r in rets:
        idx = min(bins - 1, int((r - lo) / width))
        counts[idx] += 1
    n = len(rets)
    h = -sum((c / n) * math.log(c / n) for c in counts if c)
    return h / math.log(bins)


def _votes(closes: list[float], ema_f: float, ema_s: float, slope: float,
           roc: float, vr: float, vwap: float) -> tuple[int, int]:
    """5-voter ensemble adapted to OHLC, mirroring the research engine's _vote()."""
    long_v = short_v = 0
    # V1 StepGMA (slow-EMA slope)
    if slope > 0:
        long_v += 1
    elif slope < 0:
        short_v += 1
    # V2 EMA ribbon
    if ema_f > ema_s:
        long_v += 1
    elif ema_f < ema_s:
        short_v += 1
    # V3 momentum
    if roc > 0:
        long_v += 1
    elif roc < 0:
        short_v += 1
    # V4 regime-aware: TREND votes with the slope, REVERT votes against the last move
    if vr > 1.0:
        long_v += 1 if slope > 0 else 0
        short_v += 1 if slope < 0 else 0
    elif vr < 1.0:
        long_v += 1 if roc < 0 else 0
        short_v += 1 if roc > 0 else 0
    # V5 level vs VWAP proxy
    if closes[-1] > vwap:
        long_v += 1
    elif closes[-1] < vwap:
        short_v += 1
    return long_v, short_v


def _score_trend(direction: str, slope: float, ema_f: float, ema_s: float, ref: float) -> float:
    ribbon_bull = ema_f > ema_s
    aligned = (direction == "long" and ribbon_bull) or (direction == "short" and not ribbon_bull)
    if not aligned:
        return 0.1
    norm = abs(slope) / ref if ref > 0 else 0.0
    return min(1.0, 0.7 + min(0.3, norm * 0.3))


def _score_level(direction: str, last: float, vwap: float, ref: float) -> float:
    if ref <= 0:
        return 0.0
    near = abs(last - vwap) < ref
    right_side = (direction == "long" and last >= vwap) or (direction == "short" and last <= vwap)
    if right_side and near:
        return 1.0
    if right_side:
        return 0.5
    return 0.0


def _score_momentum(direction: str, roc: float, ref: float) -> float:
    if ref <= 0:
        return 0.4
    norm = min(1.0, abs(roc) / ref)
    if (direction == "long" and roc > 0) or (direction == "short" and roc < 0):
        return min(1.0, 0.5 + norm * 0.5)
    return 0.2


def _score_regime(direction: str, vr: float, slope: float) -> float:
    trending = vr > 1.0
    aligned = (direction == "long" and slope > 0) or (direction == "short" and slope < 0)
    if trending and aligned:
        return min(1.0, 0.6 + min(0.4, (vr - 1.0)))
    if trending:
        return 0.3
    return 0.2  # mean-revert regime is hostile to this trend-continuation engine


def _flat(reason: str, last: float | None = None) -> dict:
    return {"direction": "flat", "score": 0.0, "grade": "C", "gate": reason,
            "entry": last, "components": {}, "votes": {"long": 0, "short": 0}}


def score(closes: list[float]) -> dict:
    """Composite score for the trailing close series. Returns a verdict dict:
    ``direction`` (long|short|flat), ``score`` (0–100), ``grade`` (A|B|C), per-component
    ``components``, the voter tally, the ATR-proxy ``vol``, and a ``gate`` reason when
    flat. Pure; never raises — thin/flat/noisy input yields an honest FLAT."""
    if not closes or len(closes) < _WARMUP:
        return _flat(f"insufficient bars ({len(closes) if closes else 0} < {_WARMUP})",
                     closes[-1] if closes else None)

    last = closes[-1]
    win = closes[-LEVEL_LOOKBACK:] if len(closes) >= LEVEL_LOOKBACK else closes
    vol = _stdev(win)
    ref = vol if vol > 0 else 0.0
    vwap = sum(win) / len(win)

    ema_f_series = _ema_series(closes, FAST)
    ema_s_series = _ema_series(closes, SLOW)
    ema_f, ema_s = ema_f_series[-1], ema_s_series[-1]
    slope = ema_s_series[-1] - ema_s_series[-min(len(ema_s_series), VR_K + 1)]
    roc = (last - closes[-1 - ROC_K]) / abs(closes[-1 - ROC_K]) if abs(closes[-1 - ROC_K]) > 0 else 0.0
    vr = _variance_ratio(closes, VR_K)
    entropy = _return_entropy(win)

    if vol <= 0.0:
        return _flat("flat window (zero volatility) — no honest geometry", last)
    if entropy > ENTROPY_BLOCK:
        return _flat(f"entropy noise ({entropy:.0%}) — gated", last)
    if vr < VR_REVERT_GATE:
        return _flat(f"mean-reverting regime (VR={vr:.2f}) — trend engine stands down", last)

    long_v, short_v = _votes(closes, ema_f, ema_s, slope, roc, vr, vwap)
    if long_v >= MIN_CONSENSUS and long_v > short_v:
        direction = "long"
    elif short_v >= MIN_CONSENSUS and short_v > long_v:
        direction = "short"
    else:
        return _flat(f"no consensus ({long_v}L/{short_v}S of {MIN_CONSENSUS})", last)

    # momentum reference = ~2× the random-walk k-bar return magnitude (vol/price·√k);
    # a trend's directional drift should clear it, noise should not.
    ref_roc = (vol / abs(last)) * (ROC_K ** 0.5) if last else 0.0
    comp = {
        "trend": _score_trend(direction, slope, ema_f, ema_s, ref),
        "level": _score_level(direction, last, vwap, ref),
        "momentum": _score_momentum(direction, roc, 2.0 * ref_roc),
        "regime": _score_regime(direction, vr, slope),
    }
    raw = sum(comp[k] * WEIGHTS[k] for k in WEIGHTS)
    final = raw * 100.0
    if final < ENTRY_THRESHOLD * 100:
        return _flat(f"score low ({final:.0f} < {ENTRY_THRESHOLD * 100:.0f})", last)
    grade = "A" if final >= STRONG_THRESHOLD * 100 else "B"
    return {"direction": direction, "score": round(final, 2), "grade": grade,
            "gate": None, "entry": last, "vol": round(vol, 6),
            "components": {k: round(v, 4) for k, v in comp.items()},
            "votes": {"long": long_v, "short": short_v}}

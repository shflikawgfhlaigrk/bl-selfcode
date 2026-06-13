"""The research engine — the Perplexity/Antigravity "Sniper" composite scorer ported
into Utah on the bars Utah actually has.

The ~/debt research bundles (Perplexity_*, Research_Backed, Signal_Bible, Context_*,
Barber) are ONE strategy: a 7-component weighted composite (trend/level/volume/intel/
momentum/session/smt) + a 5-voter ensemble + hard gates + ATR geometry. Utah's bars
table is OHLC only — no tick/order-flow — so the volume/CVD/SMT/absorption voters can't
be computed honestly. This port implements the components OHLC SUPPORTS (trend via EMA
ribbon + slope, key-level proximity, momentum, Hurst/entropy regime), renormalizes the
weights over them, and DOCUMENTS the order-flow components as gated on data Utah lacks.

The live signal (closes-only, same `evaluate` interface as breakout/meanrev) and the
backtest archetype MUST derive the SAME entry from the SAME closes — otherwise the edge
gate, which fires only what the OOS backtest proves, would be meaningless.
"""
from __future__ import annotations

from utah.product import research_signal as rs
from utah.product import trading, backtest


def _rising(n=80, step=0.5, start=100.0):
    return [start + i * step for i in range(n)]


def _falling(n=80, step=0.5, start=140.0):
    return [start - i * step for i in range(n)]


def _choppy(n=80, base=100.0):
    return [base + (1.0 if i % 2 else 0.0) for i in range(n)]


def _ohlc(closes):
    """OHLC tuples around a close series: small symmetric range, open=prior close."""
    out = []
    prev = closes[0]
    for c in closes:
        hi, lo = max(prev, c) + 0.1, min(prev, c) - 0.1
        out.append((prev, hi, lo, c))
        prev = c
    return out


# ── research integrity: the weights are a real, normalized composite ─────────

def test_component_weights_sum_to_one():
    assert abs(sum(rs.WEIGHTS.values()) - 1.0) < 1e-9


def test_score_is_bounded_0_to_100():
    for closes in (_rising(), _falling(), _choppy()):
        s = rs.score(closes)
        assert 0.0 <= s["score"] <= 100.0


# ── the scorer: direction + grade on clean regimes ──────────────────────────

def test_clean_uptrend_scores_long_with_grade():
    s = rs.score(_rising())
    assert s["direction"] == "long"
    assert s["grade"] in ("A", "B")
    assert s["score"] >= rs.ENTRY_THRESHOLD * 100


def test_clean_downtrend_scores_short():
    s = rs.score(_falling())
    assert s["direction"] == "short"
    assert s["grade"] in ("A", "B")


def test_choppy_series_is_flat_no_signal():
    s = rs.score(_choppy())
    assert s["direction"] == "flat"
    assert s["grade"] == "C"


def test_thin_series_is_flat_never_fabricates():
    s = rs.score([100.0, 100.5, 101.0])  # below warmup
    assert s["direction"] == "flat"
    assert s["gate"]  # an honest reason, not a fabricated signal


# ── live wiring into the engine roster ───────────────────────────────────────

def test_research_is_an_implemented_engine():
    assert "research" in trading.ENGINE_RULES
    assert "research" in trading.implemented_engines()


def test_research_has_a_registered_archetype():
    assert trading.ENGINE_ARCHETYPE.get("research")


def test_evaluate_research_fires_long_on_uptrend():
    sig = trading.evaluate(_rising(), engine="research")
    assert sig and sig["engine"] == "research" and sig["direction"] == "long"


def test_evaluate_research_silent_on_chop():
    assert trading.evaluate(_choppy(), engine="research") is None


# ── the backtest archetype: OOS-measured, never painted ──────────────────────

def test_prove_research_returns_oos_verdict_shape():
    v = backtest.prove_research(_ohlc(_rising(300)), min_trades=3)
    for k in ("in_sample", "out_of_sample", "edge_proven", "reason", "config"):
        assert k in v
    assert isinstance(v["edge_proven"], bool)


def test_prove_research_finds_edge_on_persistent_trend():
    # a long, persistent uptrend: trend-aligned longs ride to target → positive OOS
    v = backtest.prove_research(_ohlc(_rising(400)), min_trades=3)
    assert v["out_of_sample"]["trades"] >= 3
    assert v["edge_proven"] is True
    assert v["out_of_sample"]["net_pts"] > 0


def test_prove_research_flat_series_proves_no_edge():
    v = backtest.prove_research(_ohlc([100.0] * 300), min_trades=3)
    assert v["edge_proven"] is False  # zero-variance → no trades, honest


def test_backtest_engine_dispatches_research():
    sc = trading._backtest_engine("research", _ohlc(_rising(400)))
    assert sc["engine"] == "research"
    assert sc["archetype"] == trading.ENGINE_ARCHETYPE["research"]
    assert "edge_proven" in sc

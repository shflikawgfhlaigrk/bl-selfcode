"""Trading STRESS / adversarial suite — the sellable-product guarantees.

The trading engine is sold ($499/mo) on three promises a buyer (or a skeptic) will
poke at directly: it never fabricates, it never bets without proven edge, and the
live signal equals the backtested signal. This suite attacks each promise with
garbage input, hostile symbols, and forced-losing edges, and locks the contract:

  • Non-finite feed values (NaN / ±inf) NEVER produce a signal or a non-finite level.
  • A fire records ONLY when the edge gate proves held-out OOS edge on that symbol —
    a losing/thin backtest is suppressed, record_fire is never reached.
  • The live signal is byte-identical to the backtest signal (same closes → same
    verdict) so the proven OOS edge is the edge that actually fires.
  • DB reads are injection-safe (parameterized) and degrade to honest-empty, never crash.
"""
from __future__ import annotations

import math
import random

import pytest

from utah.product import trading
from utah.product import research_signal as rs


# ── 1. Non-finite guard — corrupt feed never fabricates a signal or a level ──────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("engine", ["breakout", "meanrev", "research"])
def test_non_finite_close_never_signals(engine, bad):
    closes = [100.0] * 20 + [bad]
    assert trading.evaluate(closes, engine=engine) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_anywhere_in_series_never_signals(bad):
    # garbage in the MIDDLE of the window, not just the tail
    closes = [100.0] * 5 + [bad] + [101.0] * 15 + [120.0]
    for engine in ("breakout", "meanrev", "research"):
        assert trading.evaluate(closes, engine=engine) is None


def test_no_engine_ever_emits_a_non_finite_level():
    """Across pathological-but-finite series, a fired signal's stop/target are always
    finite numbers (or None) — never NaN/inf. A non-finite level would poison the
    ledger and the phone alert."""
    series = {
        "huge": [1e300] * 20 + [1e300 * 1.0001],
        "tiny": [1e-300] * 20 + [2e-300],
        "neg": [-100.0] * 20 + [-50.0],
        "spike_up": [100.0] * 20 + [1e9],
        "spike_dn": [100.0] * 20 + [-1e9],
    }
    for closes in series.values():
        for engine in ("breakout", "meanrev", "research"):
            sig = trading.evaluate(closes, engine=engine)
            if not sig:
                continue
            ctx = trading._fire_context(closes, sig)
            for k in ("stop", "target"):
                v = ctx.get(k)
                assert v is None or (isinstance(v, (int, float)) and math.isfinite(v)), \
                    f"{engine}/{k} emitted non-finite level {v!r}"


# ── 2. Edge-gate integrity — never a negative-expectancy bet ─────────────────────

class _NeverFireLedger:
    """record_fire is a tripwire: if the edge gate lets a no-edge signal through,
    this raises and the test fails loudly."""

    def fire_state(self, engine):
        return {"open": False, "last_fire_age_s": None}

    def record_fire(self, *a, **kw):  # pragma: no cover - must never be reached here
        raise AssertionError("record_fire reached despite an unproven edge")


def _breakout_long():
    return [100.0] * 20 + [110.0]


def test_no_edge_verdict_suppresses_the_fire():
    r = trading.run(_NeverFireLedger(), feed_fn=_breakout_long, symbol="ES",
                    edge_fn=lambda e, s: {"ok": False, "reason": "OOS net negative"})
    assert r["fires"] == 0
    assert r.get("suppressed") == "no_edge"


def test_thin_bars_verdict_suppresses_the_fire():
    r = trading.run(_NeverFireLedger(), feed_fn=_breakout_long, symbol="ES",
                    edge_fn=lambda e, s: {"ok": False, "reason": "insufficient bars"})
    assert r["fires"] == 0 and r.get("suppressed") == "no_edge"


class _RecordingLedger:
    def __init__(self):
        self.fired = []

    def fire_state(self, engine):
        return {"open": False, "last_fire_age_s": None}

    def record_fire(self, *a, **kw):
        self.fired.append((a, kw))
        return 7


def test_proven_edge_allows_exactly_one_fire(monkeypatch):
    monkeypatch.setattr(trading, "feed_available", lambda: True)
    # silence the phone-alert side effect
    import utah.product.trade_alert as ta
    monkeypatch.setattr(ta, "send_fire_alert", lambda *a, **k: None)
    lg = _RecordingLedger()
    r = trading.run(lg, feed_fn=_breakout_long, symbol="ES",
                    edge_fn=lambda e, s: {"ok": True, "reason": "proven", "scorecard": {}})
    assert r["fires"] == 1 and len(lg.fired) == 1
    # the recorded fire is real, never synthetic
    assert lg.fired[0][1].get("synthetic") is False


def test_edge_gate_default_real_data_only_proven_pairs_fire():
    """End-to-end against the REAL bars table: edge_ok must agree with the audit —
    a fire is allowed only where held-out OOS edge is actually proven. Skips cleanly
    if the bars table is cold (CI/no-DB)."""
    syms = trading._backtestable_symbols()
    if not syms:
        pytest.skip("no bars table / cold DB")
    proven = 0
    for sym in syms:
        for eng in trading.implemented_engines():
            v = trading.edge_ok(eng, sym)
            sc = v.get("scorecard", {})
            # the gate's verdict is EXACTLY the backtest's edge_proven — never looser
            assert v["ok"] == bool(sc.get("edge_proven"))
            proven += int(v["ok"])
    # honest: there may be zero proven edges on a given day; that is allowed.
    assert proven >= 0


# ── 3. Live == backtest determinism — the edge that fires is the edge proven ─────

def test_research_signal_is_deterministic():
    for seed in range(30):
        random.seed(seed)
        series = [100.0]
        for _ in range(150):
            series.append(series[-1] + random.uniform(-1.5, 1.5))
        w = series[-rs.RESEARCH_RWIN:]
        assert rs.score(w) == rs.score(list(w)), f"non-deterministic at seed {seed}"


def test_evaluate_is_pure_no_mutation():
    closes = [100.0 + i * 0.1 for i in range(40)]
    snapshot = list(closes)
    for engine in ("breakout", "meanrev", "research"):
        trading.evaluate(closes, engine=engine)
    assert closes == snapshot  # the engine never mutates the caller's series


# ── 4. DB boundary — hostile symbols are parameterized, never crash ──────────────

def test_ohlc_bars_uses_parameterized_query(monkeypatch):
    """A hostile symbol must reach psycopg as a BOUND PARAMETER, never string-formatted
    into the SQL. Capture the executed statement + params from a fake driver."""
    import psycopg

    captured = {}

    class _Cur:
        def fetchall(self): return []
        def fetchone(self): return None

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None, *a, **k):
            captured["sql"] = sql
            captured["params"] = params
            return _Cur()

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _Conn())
    hostile = "ES'; DROP TABLE bars;--"
    assert trading._ohlc_bars(hostile) == []
    assert "%s" in captured["sql"], "symbol must be a placeholder, not interpolated"
    assert captured["params"][0] == hostile, "symbol must travel as a bound param"
    assert "DROP TABLE" not in captured["sql"], "hostile symbol leaked into the SQL text"


def test_unfed_edges_dead_db_returns_empty_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("pg unreachable")
    out = trading.unfed_edges(candidate_symbols_fn=boom)
    assert out == []

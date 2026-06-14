"""The nightly engine audit — a full per-(engine,symbol) edge review of the trading fleet.

Michael's standing order (2026-06-13): every night, audit the engines, give a full review,
and converge over 6 months. This is that review. It runs each implemented engine's OOS
backtest against every backtestable symbol, reports which (engine,symbol) pairs PROVE
held-out edge (and which don't, and why), and renders a human report + a one-line brief.
Pure core with injected boundaries — no DB, no real backtest in these unit tests.
"""
from __future__ import annotations

from utah.product import engine_audit


def _score(verdicts):
    """A score_fn(engine, symbol) backed by a {(engine,symbol): scorecard} table."""
    def fn(engine, symbol):
        return verdicts[(engine, symbol)]
    return fn


def _sc(edge, *, trades=30, win=0.5, net=1.0, exp=0.05, reason="r"):
    return {"engine": "", "archetype": "a", "trades": trades, "wins": int(trades * win),
            "win_rate": win, "net_pts": net, "total_r": exp * trades,
            "edge_proven": edge, "reason": reason}


def test_audit_covers_the_full_engine_symbol_matrix():
    v = {("research", "QQQ"): _sc(True), ("research", "SPY"): _sc(False),
         ("meanrev", "QQQ"): _sc(False), ("meanrev", "SPY"): _sc(True)}
    r = engine_audit.audit(engines=("research", "meanrev"),
                           symbols_fn=lambda: ["QQQ", "SPY"], score_fn=_score(v),
                           generated_at="2026-06-13T02:00:00Z")
    assert len(r["fleet"]) == 4
    assert r["summary"]["total_pairs"] == 4
    assert r["summary"]["proven"] == 2


def test_audit_groups_proven_edges_by_engine():
    v = {("research", "QQQ"): _sc(True, net=3.6), ("research", "SPY"): _sc(True, net=1.2),
         ("meanrev", "QQQ"): _sc(False)}
    r = engine_audit.audit(engines=("research", "meanrev"),
                           symbols_fn=lambda: ["QQQ", "SPY"], score_fn=_score(v))
    assert set(r["summary"]["by_engine"]["research"]) == {"QQQ", "SPY"}
    assert r["summary"]["by_engine"].get("meanrev", []) == []


def test_audit_rows_carry_required_fields():
    v = {("research", "QQQ"): _sc(True)}
    r = engine_audit.audit(engines=("research",), symbols_fn=lambda: ["QQQ"],
                           score_fn=_score(v))
    row = r["fleet"][0]
    for k in ("engine", "symbol", "edge_proven", "win_rate", "net_pts", "trades", "reason"):
        assert k in row


def test_audit_empty_universe_is_honest_not_a_crash():
    r = engine_audit.audit(engines=("research",), symbols_fn=lambda: [],
                           score_fn=_score({}))
    assert r["fleet"] == []
    assert r["summary"]["proven"] == 0
    assert r["summary"]["total_pairs"] == 0


def test_audit_is_defensive_on_a_failing_backtest():
    def boom(engine, symbol):
        if symbol == "BAD":
            raise RuntimeError("backtest exploded")
        return _sc(True)
    r = engine_audit.audit(engines=("research",), symbols_fn=lambda: ["QQQ", "BAD"],
                           score_fn=boom)
    assert len(r["fleet"]) == 2                       # both pairs represented
    bad = [x for x in r["fleet"] if x["symbol"] == "BAD"][0]
    assert bad["edge_proven"] is False and "error" in bad
    assert r["summary"]["proven"] == 1               # the good one still counts


def test_render_report_names_engines_and_verdicts():
    v = {("research", "QQQ"): _sc(True, net=3.6, win=0.40),
         ("research", "SPY"): _sc(False, net=-2.0, win=0.30)}
    r = engine_audit.audit(engines=("research",), symbols_fn=lambda: ["QQQ", "SPY"],
                           score_fn=_score(v), generated_at="2026-06-13T02:00:00Z")
    text = engine_audit.render(r)
    assert "research" in text and "QQQ" in text and "SPY" in text
    assert "1" in text  # at least one proven edge reported in the summary
    assert "2026-06-13" in text


def test_summary_line_is_one_line_for_the_brief():
    v = {("research", "QQQ"): _sc(True)}
    r = engine_audit.audit(engines=("research",), symbols_fn=lambda: ["QQQ"],
                           score_fn=_score(v))
    line = engine_audit.summary_line(r)
    assert "\n" not in line
    assert "1" in line  # 1 proven edge


# ── best_edges: per-engine best PROVEN symbol (so the app/lab shows WHERE edge is) ──

def test_best_edges_picks_highest_net_proven_per_engine():
    v = {("research", "QQQ"): _sc(True, net=3.6, win=0.40),
         ("research", "SPY"): _sc(True, net=1.2, win=0.41),
         ("research", "DIA"): _sc(False, net=-1.8),
         ("meanrev", "NQ"): _sc(False)}
    r = engine_audit.audit(engines=("research", "meanrev"),
                           symbols_fn=lambda: ["QQQ", "SPY", "DIA", "NQ"], score_fn=_score(v))
    be = engine_audit.best_edges(r)
    assert be["research"]["symbol"] == "QQQ"          # highest net proven
    assert be["research"]["net_pts"] == 3.6
    assert be["meanrev"] is None                       # nothing proven → honest None


def test_best_edges_engine_with_no_proven_pair_is_none():
    v = {("research", "QQQ"): _sc(False, net=-1.0)}
    r = engine_audit.audit(engines=("research",), symbols_fn=lambda: ["QQQ"], score_fn=_score(v))
    assert engine_audit.best_edges(r)["research"] is None


# ── latest(): the cheap deck/app read of the last persisted audit ──

def test_latest_reads_persisted_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_audit, "AUDIT_DIR", tmp_path)
    assert engine_audit.latest() == {}                 # nothing written yet → honest empty
    payload = {"generated_at": "2026-06-13T02:00:00Z", "summary": {"proven": 1},
               "fleet": [{"engine": "research", "symbol": "QQQ", "edge_proven": True}]}
    import json
    (tmp_path / "latest.json").write_text(json.dumps(payload))
    got = engine_audit.latest()
    assert got["summary"]["proven"] == 1
    assert got["fleet"][0]["symbol"] == "QQQ"

"""Trading capability — ready-skeleton GATED on the WealthCharts market feed (Michael's WC
login). The pipeline is wired: feed -> evaluate() -> ledger.record_fire. A reference breakout
engine is the starter; Ace's 8 engines add rules on the same evaluate() interface. With no
feed it records 0 fires + documents the gate (faking fires is forbidden). evaluate is pure;
the feed is injectable."""
from __future__ import annotations

from utah import failures
from utah.product import trading
from tests.fakes import FakeFailureStore


def test_evaluate_breakout_and_breakdown_and_range():
    rng = [10.0] * 20
    assert trading.evaluate(rng + [12.0], lookback=20)["direction"] == "long"    # breakout
    assert trading.evaluate(rng + [8.0], lookback=20)["direction"] == "short"     # breakdown
    assert trading.evaluate(rng + [10.0], lookback=20) is None                    # in range
    assert trading.evaluate([10.0, 11.0], lookback=20) is None                    # too few bars


class _RecLedger:
    def __init__(self):
        self.fires = []

    def record_fire(self, engine, direction, entry=None, synthetic=False, **kw):
        self.fires.append((engine, direction, entry, synthetic, kw))
        return len(self.fires)


def test_run_gated_without_feed_records_zero_and_documents(monkeypatch):
    monkeypatch.setattr(trading, "feed_available", lambda: False)  # hermetic: ignore live WC
    store = FakeFailureStore(); failures.set_store(store)
    lg = _RecLedger()
    r = trading.run(lg)                              # no feed, no live feed
    assert r["fires"] == 0 and r["gated"] is True
    assert lg.fires == []                            # NEVER a fabricated fire
    assert any("feed_gated" in row[2] for row in store.rows)


def test_run_with_feed_fires_real_signal():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    closes = [10.0] * 20 + [12.5]                    # breakout
    r = trading.run(lg, feed_fn=lambda: closes)
    assert r["fires"] == 1 and r["signal"]["direction"] == "long"
    assert lg.fires and lg.fires[0][3] is False      # real fire, not synthetic
    assert lg.fires[0][4].get("stop") == 10.0        # structural stop from flat window
    assert lg.fires[0][4].get("rationale")           # rich alert context persisted


def test_run_feed_failure_documented():
    store = FakeFailureStore(); failures.set_store(store)
    def boom():
        raise RuntimeError("WC bridge disconnected")
    r = trading.run(_RecLedger(), feed_fn=boom)
    assert r["fires"] == 0
    assert any("feed_failed" in row[2] for row in store.rows)


def test_lab_state_roster_is_gated_and_dormant(monkeypatch):
    # The merged engine lab lists the full roster; with no WC feed every engine is
    # DORMANT (implemented) or AWAITING PORT (nameplates) — never a fabricated live engine.
    monkeypatch.setattr(trading, "feed_available", lambda: False)  # hermetic: ignore live WC
    st = trading.lab_state(fires=0)
    assert st["feed"] == "gated"
    names = {e["name"] for e in st["engines"]}
    assert {"shadow", "ctx_alpha", "ctx_bravo", "barber", "perp", "research", "bible", "antigrav"} <= names
    assert all(e["state"] in ("dormant", "awaiting port") for e in st["engines"])
    assert not any(e["state"] == "live" for e in st["engines"])
    assert st["fires"] == 0


def test_lab_state_never_shows_an_unimplemented_engine_live(monkeypatch):
    """The dash lie (2026-06-10): feed live flipped ALL nine roster engines to LIVE
    while one had logic. Per-engine truth: live only for implemented engines."""
    monkeypatch.setattr(trading, "feed_available", lambda: True)
    st = trading.lab_state(fires=3)
    by = {e["name"]: e["state"] for e in st["engines"]}
    assert by["breakout"] == "live" and by["meanrev"] == "live"
    assert by["antigrav"] == "awaiting port" and by["shadow"] == "awaiting port"
    assert st["live_engines"] == 2 and "2 of" in st["note"]


def test_lab_state_carries_per_engine_fire_counts(monkeypatch):
    monkeypatch.setattr(trading, "feed_available", lambda: True)
    st = trading.lab_state(fires=5, by_engine={"breakout": 4, "meanrev": 1})
    by = {e["name"]: e.get("fires", 0) for e in st["engines"]}
    assert by["breakout"] == 4 and by["meanrev"] == 1 and by["antigrav"] == 0


def test_evaluate_meanrev_fades_extremes_and_sits_out_the_middle():
    """Engine #2 is REAL and DISTINCT: mean-reversion fades a z-score extreme —
    the opposite thesis to breakout, so the two legitimately disagree."""
    base = [100.0, 100.4, 99.8, 100.2, 99.9, 100.1, 100.0, 99.7, 100.3, 100.0] * 2
    dip = base + [97.0]      # deep below the band -> fade LONG
    spike = base + [103.0]   # far above the band  -> fade SHORT
    flat = base + [100.05]
    assert trading.evaluate(dip, lookback=20, engine="meanrev")["direction"] == "long"
    assert trading.evaluate(spike, lookback=20, engine="meanrev")["direction"] == "short"
    assert trading.evaluate(flat, lookback=20, engine="meanrev") is None
    # zero-variance window can never fire (no fabricated z-score)
    assert trading.evaluate([100.0] * 21, lookback=20, engine="meanrev") is None
    # unknown engines never fabricate a signal
    assert trading.evaluate(dip, lookback=20, engine="antigrav") is None


class _StateLedger:
    """Fake with the fire-state machine: scripted state, records record_fire calls."""
    def __init__(self, state):
        self.state, self.fires = state, []

    def fire_state(self, engine):
        return self.state

    def record_fire(self, engine, direction, entry=None, synthetic=False,
                    symbol=None, *, stop=None, target=None, rationale=None):
        self.fires.append((engine, direction, entry))
        return len(self.fires)


def _breakout_closes():
    # 20 flat bars then a clear new high -> breakout long signal
    return [100.0] * 20 + [105.0]


def test_open_position_suppresses_refire():
    """The 2026-06-10 storm fix: an ungraded (open) fire means IN A TRADE — the same
    engine must not fire again until the grader closes it (749 fires/day before)."""
    lg = _StateLedger({"open": True, "last_fire_age_s": 10_000.0})
    r = trading.run(lg, feed_fn=_breakout_closes)
    assert r["fires"] == 0 and r["suppressed"] == "in_position"
    assert lg.fires == []                      # nothing recorded


def test_cooldown_suppresses_refire_then_rearms():
    lg = _StateLedger({"open": False, "last_fire_age_s": 30.0})
    r = trading.run(lg, feed_fn=_breakout_closes)
    assert r["fires"] == 0 and "cooldown" in r["suppressed"]
    assert lg.fires == []
    lg.state = {"open": False, "last_fire_age_s": trading.FIRE_COOLDOWN_S + 1}
    r2 = trading.run(lg, feed_fn=_breakout_closes)
    assert r2["fires"] == 1 and lg.fires      # flat + cooled down -> real fire records


def test_first_fire_ever_is_not_suppressed():
    lg = _StateLedger({"open": False, "last_fire_age_s": None})
    r = trading.run(lg, feed_fn=_breakout_closes)
    assert r["fires"] == 1 and len(lg.fires) == 1


# --- engine backtest auto-population into the apex dash (Part B wiring) -------
# Each implemented engine maps to a backtest archetype run on the REAL bars; the
# result (held-out OOS win/net) auto-populates the dash so the bars live-update
# from measured numbers, never a painted one.

def _mr_extreme_bars():
    """OHLC bars where the OOS half (after the 60/40 split) independently holds a full
    20-bar lookback window plus a clean z-extreme dip, so meanrev fades long and the
    tight target is hit — a real, non-zero held-out backtest the dash can render."""
    def half():
        win = [(100.0, 100.3, 99.7, 100.0) for _ in range(25)]
        win[22] = (100.0, 100.6, 99.7, 100.6)    # variance INSIDE the dip's lookback so sd > 0
        dip = (97.0, 97.2, 96.8, 97.0)           # the extreme (entry)
        recover = (97.0, 100.1, 96.9, 100.0)     # wick hits the tight target, closes at mean
        return win + [dip, recover]
    return half() + half()                        # both in-sample and OOS halves fire


def test_fleet_backtest_runs_each_implemented_engine_on_real_ohlc():
    """fleet_backtest is the apex dash config builder: per implemented engine it runs
    its archetype on injected OHLC bars and returns the held-out OOS scorecard. Never
    touches the live DB in the test — the fetcher is injectable."""
    bars = _mr_extreme_bars()
    ohlc = {"meanrev": bars, "breakout": bars}
    fb = trading.fleet_backtest(ohlc_fn=lambda eng: ohlc.get(eng, []))
    assert set(fb) == set(trading.implemented_engines())          # one entry per real engine
    mr = fb["meanrev"]
    assert {"win_rate", "net_pts", "trades", "edge_proven", "archetype"} <= set(mr)
    assert mr["archetype"] == "mean-reversion"
    assert mr["trades"] >= 1                                       # a real measured sample


def test_fleet_backtest_never_fabricates_when_no_bars():
    """No bars (cold DB / pre-feed) = an honest empty scorecard, not a painted number."""
    fb = trading.fleet_backtest(ohlc_fn=lambda eng: [])
    for eng, sc in fb.items():
        assert sc["trades"] == 0 and sc["win_rate"] is None and sc["edge_proven"] is False


def test_lab_state_carries_backtest_scorecard_onto_each_engine():
    """The wiring: lab_state stamps each engine row with its backtest OOS stats so the
    deck's bar-builders live-update from the config with no panel edits."""
    bt = {"meanrev": {"win_rate": 0.89, "net_pts": 173.2, "trades": 60,
                      "edge_proven": True, "archetype": "mean-reversion"}}
    st = trading.lab_state(fires=1, backtests=bt)
    by = {e["name"]: e for e in st["engines"]}
    assert by["meanrev"]["backtest"]["win_rate"] == 0.89
    assert by["meanrev"]["backtest"]["edge_proven"] is True
    # engines without a backtest entry carry None, never a fabricated stat
    assert by["antigrav"]["backtest"] is None


def test_lab_state_explicit_none_backtests_carries_none(monkeypatch):
    """Explicit backtests=None (the hermetic default) => every engine carries a
    backtest key set to None — never a fabricated stat, never a DB hit in a unit test."""
    monkeypatch.setattr(trading, "feed_available", lambda: False)
    st = trading.lab_state(fires=0, backtests=None)
    assert all(e["backtest"] is None for e in st["engines"])


# --- apex dash config file: fleet_backtest writes it, the deck reads it -------

def test_fleet_backtest_writes_the_apex_dash_config(tmp_path, monkeypatch):
    """fleet_backtest persists the per-engine scorecards to the apex dash config file so
    the deck's bar-builders live-update from measured numbers without re-running a backtest
    on every 2.5s poll. The path is overridable for the test."""
    import json

    cfg_path = tmp_path / "apex_dash.json"
    monkeypatch.setattr(trading, "APEX_DASH_CONFIG", cfg_path)
    bars = _mr_extreme_bars()
    fb = trading.fleet_backtest(ohlc_fn=lambda eng: bars)
    assert cfg_path.exists()
    on_disk = json.loads(cfg_path.read_text())
    assert "engines" in on_disk and "updated" in on_disk
    assert on_disk["engines"]["meanrev"]["win_rate"] == fb["meanrev"]["win_rate"]


def test_dash_config_reads_the_written_config_and_is_safe_when_missing(tmp_path, monkeypatch):
    """dash_config() is the cheap read the deck uses every poll: the persisted scorecards,
    or an empty dict when the config has never been built (honest, never a crash)."""
    cfg_path = tmp_path / "apex_dash.json"
    monkeypatch.setattr(trading, "APEX_DASH_CONFIG", cfg_path)
    assert trading.dash_config() == {}                              # never built => empty
    trading.fleet_backtest(ohlc_fn=lambda eng: _mr_extreme_bars())
    cfg = trading.dash_config()
    assert "meanrev" in cfg and "win_rate" in cfg["meanrev"]


def test_lab_state_default_auto_populates_from_the_dash_config(tmp_path, monkeypatch):
    """The live-update wiring with NO panel edit: lab_state() with no backtests reads the
    apex dash config so the deck shows each engine's measured backtest every poll."""
    cfg_path = tmp_path / "apex_dash.json"
    monkeypatch.setattr(trading, "APEX_DASH_CONFIG", cfg_path)
    monkeypatch.setattr(trading, "feed_available", lambda: False)
    trading.fleet_backtest(ohlc_fn=lambda eng: _mr_extreme_bars())
    st = trading.lab_state(fires=0)                                 # no kwarg => reads the config
    by = {e["name"]: e for e in st["engines"]}
    assert by["meanrev"]["backtest"] is not None
    assert by["meanrev"]["backtest"]["win_rate"] == trading.dash_config()["meanrev"]["win_rate"]
    assert by["antigrav"]["backtest"] is None                      # nameplate stays empty

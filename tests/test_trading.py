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

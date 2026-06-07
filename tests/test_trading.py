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

    def record_fire(self, engine, direction, entry=None, synthetic=False):
        self.fires.append((engine, direction, entry, synthetic))
        return len(self.fires)


def test_run_gated_without_feed_records_zero_and_documents():
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


def test_run_feed_failure_documented():
    store = FakeFailureStore(); failures.set_store(store)
    def boom():
        raise RuntimeError("WC bridge disconnected")
    r = trading.run(_RecLedger(), feed_fn=boom)
    assert r["fires"] == 0
    assert any("feed_failed" in row[2] for row in store.rows)

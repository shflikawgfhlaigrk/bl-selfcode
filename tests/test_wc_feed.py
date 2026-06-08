"""WealthCharts feed bridge — pure frame parsing + the gated, real-only run loop."""
from __future__ import annotations

from utah.integrations import wc_feed

# a REAL frame captured live from app.wealthcharts.com (MNQ June micro-futures)
REAL = ('{"cmd":"feed","data":{"type":"candle","c":"CM.MNQM6","candle":'
        '{"cnu":1,"co":29374.00,"cm":29370.00,"cM":29380.00,"cc":29376.50,'
        '"cts":224622,"cq":"1","cepoch":1781045182,"type":"rt"}}}')


def test_parse_candle_extracts_symbol_and_ohlc():
    c = wc_feed.parse_candle(REAL)
    assert c["symbol"] == "CM.MNQM6"
    assert c["close"] == 29376.5 and c["open"] == 29374.0
    assert c["high"] == 29380.0 and c["low"] == 29370.0
    assert c["epoch"] == 1781045182


def test_parse_candle_ignores_keepalive_and_junk():
    assert wc_feed.parse_candle('{"cmd":"keepalive","ref":81}') is None
    assert wc_feed.parse_candle("2") is None                       # scalar frame
    assert wc_feed.parse_candle("not json") is None
    assert wc_feed.parse_candle('{"cmd":"feed","data":{"type":"quote"}}') is None  # non-candle
    assert wc_feed.parse_candle('{"cmd":"feed","data":{"type":"candle","c":"X","candle":{}}}') is None


def test_run_gates_when_feed_unavailable(monkeypatch):
    monkeypatch.setattr(wc_feed, "feed_available", lambda: False)
    r = wc_feed.run(ledger=object())
    assert r["available"] is False and r["fires"] == 0


def test_run_records_a_real_fire_on_breakout(monkeypatch):
    """Latest close breaks the prior lookback high → one REAL (synthetic=False) long fire."""
    monkeypatch.setattr(wc_feed, "feed_available", lambda: True)
    fired: list = []

    class FakeLedger:
        def record_fire(self, engine, direction, entry=None, synthetic=False):
            fired.append((engine, direction, entry, synthetic))
            return 1

    closes = [100, 101, 102, 101, 103, 104, 110]   # 7 closes; last breaks prior-5 high
    r = wc_feed.run(ledger=FakeLedger(), lookback=5,
                    collect_fn=lambda seconds: {"CM.MNQM6": closes})
    assert r["available"] is True and r["evaluated"] == 1 and r["fires"] == 1
    assert fired[0][1] == "long" and fired[0][3] is False           # real, never synthetic


def test_run_no_fire_when_range_bound(monkeypatch):
    monkeypatch.setattr(wc_feed, "feed_available", lambda: True)
    closes = [100, 101, 100, 101, 100, 101, 100]   # oscillating, no breakout
    r = wc_feed.run(ledger=object(), lookback=5,
                    collect_fn=lambda seconds: {"X": closes})
    assert r["fires"] == 0 and r["evaluated"] == 1

"""WealthCharts feed bridge — pure frame parsing + closed-bar aggregation + the gated,
real-only, bar-based run loop (no firing on tick jitter)."""
from __future__ import annotations

from utah.integrations import wc_feed

# a REAL frame captured live from app.wealthcharts.com (MNQ June micro-futures)
REAL = ('{"cmd":"feed","data":{"type":"candle","c":"CM.MNQM6","candle":'
        '{"cnu":1,"co":29374.00,"cm":29370.00,"cM":29380.00,"cc":29376.50,'
        '"cts":224622,"cq":"1","cepoch":1781045182,"type":"rt"}}}')


def test_parse_candle_extracts_symbol_ohlc_epoch():
    c = wc_feed.parse_candle(REAL)
    assert c["symbol"] == "CM.MNQM6" and c["epoch"] == 1781045182
    assert c["close"] == 29376.5 and c["open"] == 29374.0
    assert c["high"] == 29380.0 and c["low"] == 29370.0


def test_parse_candle_ignores_keepalive_junk_and_missing_fields():
    assert wc_feed.parse_candle('{"cmd":"keepalive","ref":81}') is None
    assert wc_feed.parse_candle("2") is None
    assert wc_feed.parse_candle("not json") is None
    assert wc_feed.parse_candle('{"cmd":"feed","data":{"type":"quote"}}') is None
    # candle with no close OR no epoch -> rejected (never a fabricated price/time)
    assert wc_feed.parse_candle('{"cmd":"feed","data":{"type":"candle","c":"X","candle":{"cc":1}}}') is None


def test_closed_bars_buckets_by_epoch_and_excludes_forming_bar():
    # bar_seconds=10: epochs 100-109 -> bar 10, 110-119 -> bar 11, 120 -> bar 12 (forming)
    ticks = [(100, 1.0), (105, 2.0), (109, 3.0),   # bar 10 closes at 3.0
             (110, 4.0), (119, 5.0),               # bar 11 closes at 5.0
             (120, 6.0)]                           # bar 12 still forming -> excluded
    bars = wc_feed.closed_bars(ticks, bar_seconds=10)
    assert bars == [(10, 3.0), (11, 5.0)]          # last close per closed bucket, forming dropped


def test_ingest_persists_and_dedups_across_overlapping_windows():
    state = {}
    # window 1: bars 10 (closes 3.0) and 11 (closes 5.0) complete; bar 12 still forming (9.0)
    wc_feed.ingest(state, "X", [(100, 1.0), (109, 3.0), (110, 5.0), (120, 9.0)], bar_seconds=10)
    # window 2 OVERLAPS — re-sends bar 11, completes bar 12 (last tick 7.0), bar 13 forming.
    # bar 11 must NOT double-count; bar 12 lands once.
    closes = wc_feed.ingest(state, "X", [(110, 5.0), (120, 6.0), (129, 7.0), (130, 8.0)], bar_seconds=10)
    assert closes == [3.0, 5.0, 7.0]               # bars 10,11,12 — each once; 13 still forming


def test_run_gates_when_feed_unavailable(monkeypatch):
    monkeypatch.setattr(wc_feed, "feed_available", lambda: False)
    r = wc_feed.run(ledger=object())
    assert r["available"] is False and r["fires"] == 0


def test_run_fires_on_a_real_closed_bar_breakout(monkeypatch):
    """A breakout of the prior closed-bar range fires ONE real (synthetic=False) long — and
    only once enough CLOSED bars exist (no firing on the forming bar / tick jitter)."""
    monkeypatch.setattr(wc_feed, "feed_available", lambda: True)
    fired: list = []

    class FakeLedger:
        def record_fire(self, engine, direction, entry=None, synthetic=False, symbol=None):
            fired.append((direction, synthetic)); return 1

    # 7 ticks across 7 distinct 1s bars; the 7th (forming) is excluded -> 6 closed bars:
    # 100,101,102,101,103,110 ; with lookback 5 the last CLOSED bar 110 breaks prior-5 high
    ticks = {"CM.MNQM6": [(0, 100.0), (1, 101.0), (2, 102.0), (3, 101.0),
                          (4, 103.0), (5, 110.0), (6, 111.0)]}
    r = wc_feed.run(ledger=FakeLedger(), bar_seconds=1, lookback=5,
                    collect_fn=lambda seconds: ticks)
    assert r["available"] is True and r["ready"] == 1 and r["fires"] == 1
    assert fired == [("long", False)]                  # real, never synthetic


def test_ohlc_bars_derives_ohlc_from_tick_closes_and_drops_forming_bar():
    ticks = [(100, 2.0), (105, 1.0), (109, 3.0),   # bar 10: o=2 h=3 l=1 c=3
             (110, 4.0),                           # bar 11: single tick
             (120, 6.0)]                           # bar 12 still forming -> excluded
    bars = wc_feed.ohlc_bars(ticks, bar_seconds=10)
    assert bars == [(10, 2.0, 3.0, 1.0, 3.0), (11, 4.0, 4.0, 4.0, 4.0)]


def test_run_persists_closed_bars_and_fire_symbol(monkeypatch):
    """Closed bars now land in the ledger (ts = bar CLOSE time) so fires are gradable
    after the fact, and the fire row carries its symbol. Overlapping windows never
    re-persist a bar already stored."""
    monkeypatch.setattr(wc_feed, "feed_available", lambda: True)
    persisted, fired = [], []

    class FakeLedger:
        def record_bars(self, symbol, rows, bar_seconds=15):
            persisted.append((symbol, list(rows), bar_seconds)); return len(rows)

        def record_fire(self, engine, direction, entry=None, synthetic=False, symbol=None):
            fired.append((direction, synthetic, symbol)); return 1

    lg, state = FakeLedger(), {}
    ticks = {"CM.MNQM6": [(0, 100.0), (1, 101.0), (2, 102.0), (3, 101.0),
                          (4, 103.0), (5, 110.0), (6, 111.0)]}
    r = wc_feed.run(ledger=lg, bar_seconds=1, lookback=5,
                    collect_fn=lambda s: ticks, state=state)
    assert r["fires"] == 1 and fired == [("long", False, "CM.MNQM6")]
    sym, rows, bsec = persisted[0]
    assert sym == "CM.MNQM6" and bsec == 1
    assert [c for (_, o, h, l, c) in rows] == [100.0, 101.0, 102.0, 101.0, 103.0, 110.0]
    assert rows[0][0] == 1 and rows[5][0] == 6     # ts = bar CLOSE epoch ((key+1)*bar_s)
    # window 2 overlaps: re-sends bar 5, completes bar 6 -> ONLY bar 6 persisted anew
    ticks2 = {"CM.MNQM6": [(5, 110.0), (6, 111.0), (7, 112.0)]}
    wc_feed.run(ledger=lg, bar_seconds=1, lookback=5,
                collect_fn=lambda s: ticks2, state=state)
    assert persisted[-1] == ("CM.MNQM6", [(7, 111.0, 111.0, 111.0, 111.0)], 1)


def test_run_survives_a_ledger_without_record_bars(monkeypatch):
    """Old/minimal ledgers (and a bars-store hiccup) never break the feed loop."""
    monkeypatch.setattr(wc_feed, "feed_available", lambda: True)
    ticks = {"X": [(0, 100.0), (1, 101.0), (2, 99.0)]}
    r = wc_feed.run(ledger=object(), bar_seconds=1, lookback=5, collect_fn=lambda s: ticks)
    assert r["available"] is True and r["fires"] == 0


def test_run_no_fire_before_enough_bars(monkeypatch):
    monkeypatch.setattr(wc_feed, "feed_available", lambda: True)
    ticks = {"X": [(0, 100.0), (1, 101.0), (2, 99.0)]}   # only 2 closed bars, lookback 5
    r = wc_feed.run(ledger=object(), bar_seconds=1, lookback=5, collect_fn=lambda s: ticks)
    assert r["ready"] == 0 and r["fires"] == 0           # warms up, never fakes a signal

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


# ---- chrome-wc lifecycle: Utah owns its OWN CDP port and NEVER piles up tabs ----------------
# Root cause of the 2026-06-10 outage: old Ace's leftover chrome held 127.0.0.1:9222, Utah's
# chrome could only bind [::1]:9222, the IPv4-only probe saw the wrong (empty) chrome forever,
# and every cycle's flag-less relaunch just opened ANOTHER WC tab (21 piled up, zero data).


def test_cdp_port_is_utahs_own_not_old_aces_9222():
    assert wc_feed.CDP_PORT == 9223        # old Ace owns :9222; sharing it = probe hits its chrome


def test_wc_page_falls_back_to_ipv6_loopback(monkeypatch):
    """Chrome binds [::1] when something squats the IPv4 loopback — the probe must try both."""
    import io
    import json as _json
    page = {"type": "page", "url": f"https://{wc_feed.WC_HOST}/",
            "webSocketDebuggerUrl": "ws://[::1]:9223/devtools/page/X"}

    def fake_urlopen(url, timeout=4):
        if "127.0.0.1" in str(url):
            raise OSError("connection refused")
        return io.StringIO(_json.dumps([page]))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert wc_feed._wc_page() == page


def _ensure_harness(monkeypatch, *, pids, reachable, wc_tab_open, feed_after=True):
    """Run ensure_chrome_wc in a fully-stubbed world; return the call record."""
    calls = {"spawn": 0, "kill": [], "open_tab": 0, "feed_polls": 0}
    monkeypatch.setattr(wc_feed.os.path, "isdir", lambda p: True)
    monkeypatch.setattr(wc_feed, "_migrate_wc_profile_once", lambda: None)
    monkeypatch.setattr(wc_feed, "_wc_chrome_pids", lambda: list(pids))
    monkeypatch.setattr(wc_feed, "_cdp_reachable", lambda: reachable)
    monkeypatch.setattr(wc_feed, "_wc_any_page",
                        lambda: {"url": f"https://{wc_feed.WC_HOST}/login"} if wc_tab_open else None)

    def fake_feed():
        calls["feed_polls"] += 1
        return feed_after and calls["feed_polls"] > 1      # False on the first check, then live
    monkeypatch.setattr(wc_feed, "feed_available", fake_feed)

    def fake_open_tab():
        calls["open_tab"] += 1
        return True
    monkeypatch.setattr(wc_feed, "_open_wc_tab", fake_open_tab)
    monkeypatch.setattr(wc_feed, "_kill_wc_chrome", lambda p: calls["kill"].append(list(p)))

    def fake_spawn():
        calls["spawn"] += 1
        return True
    monkeypatch.setattr(wc_feed, "_spawn_chrome", fake_spawn)
    monkeypatch.setattr(wc_feed, "_await_feed", lambda seconds=30.0: wc_feed.feed_available())
    calls["result"] = wc_feed.ensure_chrome_wc()
    return calls


def test_ensure_never_spawns_a_second_chrome_for_a_missing_tab(monkeypatch):
    """Profile chrome alive + CDP healthy + WC tab closed → reopen the tab VIA CDP.
    A second Popen against a live profile ignores the debug flags and just piles tabs."""
    c = _ensure_harness(monkeypatch, pids=[123], reachable=True, wc_tab_open=False)
    assert c["result"] is True and c["open_tab"] == 1
    assert c["spawn"] == 0 and c["kill"] == []


def test_ensure_restarts_wedged_chrome_instead_of_tab_spam(monkeypatch):
    """Profile chrome alive but NOT reachable on our CDP port (lost the bind/wedged) →
    restart OUR chrome with the flag; never a flag-less relaunch."""
    c = _ensure_harness(monkeypatch, pids=[123, 456], reachable=False, wc_tab_open=False)
    assert c["result"] is True and c["kill"] == [[123, 456]] and c["spawn"] == 1
    assert c["open_tab"] == 0


def test_ensure_gates_on_login_without_piling_tabs_or_killing(monkeypatch):
    """WC tab exists but is logged out → Michael's one-time login; the loop must NOT open
    more tabs, spawn chromes, or churn-restart while waiting."""
    c = _ensure_harness(monkeypatch, pids=[123], reachable=True, wc_tab_open=True,
                        feed_after=False)
    assert c["result"] is False
    assert c["spawn"] == 0 and c["open_tab"] == 0 and c["kill"] == []


def test_ensure_spawns_fresh_chrome_when_none_running(monkeypatch):
    c = _ensure_harness(monkeypatch, pids=[], reachable=False, wc_tab_open=False)
    assert c["result"] is True and c["spawn"] == 1 and c["kill"] == []


class _FireLedger:
    def __init__(self):
        self.fired, self.persisted = [], []

    def record_fire(self, engine, direction, entry=None, synthetic=False, symbol=None):
        self.fired.append((engine, direction, entry, synthetic, symbol))
        return 1

    def record_bars(self, symbol, rows, bar_seconds=15):
        self.persisted.append((symbol, list(rows), bar_seconds))
        return len(rows)


def test_barstream_hooks_once_and_flushes_incrementally():
    """The PERSISTENT attach: ticks stream in continuously, flush() folds closed bars
    into state WITHOUT a detach/re-attach window — and overlapping flushes never
    double-count a bar or re-persist it (Michael 2026-06-10: 'run it the one time
    and get the hook for the data', not a 30s reconnect loop)."""
    lg = _FireLedger()
    bs = wc_feed.BarStream(lg, bar_seconds=1, lookback=5)
    for ep, px in [(0, 100.0), (1, 101.0), (2, 102.0), (3, 101.0), (4, 103.0)]:
        bs.feed("CM.NQM6", ep, px)
    r1 = bs.flush()
    assert r1["fires"] == 0 and lg.fired == []          # warm-up: 4 closed bars < lookback+1
    # ...stream continues on the SAME hook: bar 5 closes at a breakout, bar 6 forming
    bs.feed("CM.NQM6", 5, 110.0)
    bs.feed("CM.NQM6", 6, 111.0)
    r2 = bs.flush()
    # the SAME closed bar judged by every implemented engine under its OWN name:
    # breakout chases the new high, meanrev fades the 2σ extreme — true separation.
    assert r2["fires"] == 2
    assert ("breakout", "long", 110.0, False, "CM.NQM6") in lg.fired
    assert ("meanrev", "short", 110.0, False, "CM.NQM6") in lg.fired
    # every closed bar persisted exactly once across the two flushes
    all_ts = [ts for _, rows, _ in lg.persisted for (ts, *_a) in rows]
    assert sorted(all_ts) == [1, 2, 3, 4, 5, 6] and len(all_ts) == len(set(all_ts))


def test_barstream_no_new_closed_bar_means_no_reevaluation():
    """Flushing while only the forming bar grew must not re-evaluate (no duplicate
    fires from the same closed bar)."""
    lg = _FireLedger()
    bs = wc_feed.BarStream(lg, bar_seconds=1, lookback=5)
    for ep, px in [(0, 100.0), (1, 101.0), (2, 102.0), (3, 101.0),
                   (4, 103.0), (5, 110.0), (6, 111.0)]:
        bs.feed("X", ep, px)
    first = bs.flush()["fires"]
    assert first >= 1
    bs.feed("X", 6, 111.5)                              # forming bar only
    r = bs.flush()
    assert r["evaluated"] == 0 and r["fires"] == 0 and len(lg.fired) == first


def test_barstream_trims_its_buffer_to_the_forming_bar():
    """The hook runs for hours — the per-symbol tick buffer must not grow unboundedly."""
    lg = _FireLedger()
    bs = wc_feed.BarStream(lg, bar_seconds=1, lookback=5)
    for ep in range(50):
        bs.feed("X", ep, 100.0 + ep * 0.01)
    bs.flush()
    assert len(bs.buf["X"]) <= 1                        # only the forming bucket retained


def test_barstream_fires_on_signal_edge_not_every_extended_bar():
    """271 fires/hr (2026-06-10): level-firing recorded a fire on EVERY bar beyond the
    band and paged the phone each time. Edge contract: fire when the signal appears or
    flips; a no-signal bar re-arms; an extended run fires ONCE."""
    lg = _FireLedger()
    bs = wc_feed.BarStream(lg, bar_seconds=1, lookback=5)
    for ep, px in [(0, 100.0), (1, 101.0), (2, 102.0), (3, 101.0), (4, 103.0)]:
        bs.feed("X", ep, px)
    bs.flush()
    # three consecutive new-high bars: breakout signal LEVEL stays on -> ONE fire
    for ep, px in [(5, 110.0), (6, 111.0), (7, 112.0), (8, 113.0)]:
        bs.feed("X", ep, px)
    bs.flush()
    breakout_fires = [f for f in lg.fired if f[0] == "breakout"]
    assert len(breakout_fires) == 1                     # edge, not level


def test_silent_watch_pages_michael_once_not_every_flush():
    """A hooked-but-silent feed (WC login wall renders at the ROOT url, so the /login
    gate can't see it — proven live 2026-06-10) must PAGE Michael once, not loop
    silently and not spam: fires on the Nth consecutive empty flush, re-arms only
    after ticks flow again."""
    w = wc_feed.SilentWatch(threshold=4)
    assert [w.note(0), w.note(0), w.note(0)] == [False, False, False]
    assert w.note(0) is True                  # 4th consecutive empty -> page now
    assert w.note(0) is False                 # already paged -> no spam
    assert w.note(3) is False                 # ticks again -> re-armed
    assert [w.note(0)] * 1 == [False] and w.note(0) is False and w.note(0) is False
    assert w.note(0) is True                  # silent again for threshold -> page again


def test_spawn_disables_chrome_tab_pausing(monkeypatch):
    """Chrome throttles/discards background tabs — which silently pauses the realtime WC
    feed (available:True, symbols:0, chart frozen) whenever the window is occluded. The
    launch must pin the feed tab awake."""
    argv = {}
    monkeypatch.setattr("subprocess.Popen",
                        lambda args, **kw: argv.setdefault("args", list(args)))
    assert wc_feed._spawn_chrome() is True
    for flag in ("--disable-background-timer-throttling",
                 "--disable-backgrounding-occluded-windows",
                 "--disable-renderer-backgrounding"):
        assert flag in argv["args"]

"""WealthCharts live feed bridge — attach to Michael's logged-in WC dashboard over the
Chrome DevTools Protocol and stream its realtime feed into Utah's trading engines.

WC streams ~1-second candle updates (multiple tick updates per second), e.g.::

    {"cmd":"feed","data":{"type":"candle","c":"CM.MNQM6",
     "candle":{"co":29374.0,"cM":29380.0,"cm":29370.0,"cc":29376.5,"cepoch":1781045182,"type":"rt"}}}

Evaluating EVERY tick is noise (the first cut fired ~never on real lookback and only on tick
jitter when forced). So we AGGREGATE ticks into CLOSED BARS per symbol (bucket by
``cepoch // BAR_SECONDS``; the last close of a completed bucket is the bar's close, the
still-forming bucket is excluded), keep a persistent per-symbol bar history, and evaluate the
engines on the bar series → ``ledger.record_fire`` on a real breakout. Every newly-closed bar
is ALSO persisted to the ledger's ``bars`` table (ts = bar close time) so recorded fires are
gradable after the fact — utah/product/fire_grader.py walks those bars to fill outcome/pnl.
Read-only (never sends an order); gates honestly when WC is unreachable; never fabricates a
price.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("utah.integrations.wc_feed")

CDP_PORT = int(os.environ.get("UTAH_WC_CDP_PORT", "9222"))
WC_HOST = "app.wealthcharts.com"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
#: The WC Chrome profile lives under ~/.utah now (binding rule M: the runtime never reads
#: ~/.ace). The legacy ~/.ace/chrome-wc login is forward-migrated ONCE by ensure_chrome_wc.
WC_PROFILE = os.path.expanduser(os.environ.get("UTAH_WC_PROFILE", "~/.utah/chrome-wc"))
_WC_PROFILE_LEGACY = os.path.expanduser("~/.ace/chrome-wc")


def _migrate_wc_profile_once() -> None:
    """Copy the logged-in Ace WC Chrome profile into ~/.utah exactly once (iff the ~/.utah
    profile is absent and the legacy exists), so the WC login is preserved WITHOUT a
    recurring ~/.ace dependency. Best-effort; Chrome must not be running against it."""
    import shutil

    try:
        if not os.path.isdir(WC_PROFILE) and os.path.isdir(_WC_PROFILE_LEGACY):
            shutil.copytree(_WC_PROFILE_LEGACY, WC_PROFILE, dirs_exist_ok=False,
                            ignore=shutil.ignore_patterns("Singleton*", "*.lock"))
            log.info("wc_feed: migrated WC profile %s → %s (one-time)",
                     _WC_PROFILE_LEGACY, WC_PROFILE)
    except Exception as exc:  # noqa: BLE001 — a copy hiccup just means Michael re-logs in
        log.debug("wc_feed: WC profile migrate skipped: %s", exc)
#: Bar size in seconds (the feed is ~1s candles; 15s bars + lookback 20 = a 5-minute
#: breakout — a real intraday timeframe, not tick jitter). Tune with UTAH_WC_BAR_SECONDS.
BAR_SECONDS = int(os.environ.get("UTAH_WC_BAR_SECONDS", "15"))
MAX_BARS = 400


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_candle(payload: str) -> dict | None:
    """Parse one WC WebSocket frame → ``{symbol, close, open, high, low, epoch}`` or None.

    PURE (unit-tested). Returns None for keepalives, non-feed, non-candle, malformed, or
    value-less frames — so junk never crashes the bridge or fabricates a price."""
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("cmd") != "feed":
        return None
    data = obj.get("data")
    if not isinstance(data, dict) or data.get("type") != "candle":
        return None
    candle = data.get("candle")
    symbol = data.get("c")
    if not isinstance(candle, dict) or not symbol:
        return None
    close = _f(candle.get("cc"))
    epoch = candle.get("cepoch")
    if close is None or epoch is None:
        return None
    return {"symbol": symbol, "close": close, "open": _f(candle.get("co")),
            "high": _f(candle.get("cM")), "low": _f(candle.get("cm")), "epoch": int(epoch)}


def closed_bars(ticks, bar_seconds: int = BAR_SECONDS) -> list[tuple[int, float]]:
    """``ticks``: time-ordered ``[(epoch, close), ...]`` → ``[(bar_key, close), ...]`` for the
    CLOSED bars only (the still-forming final bucket is excluded). PURE (unit-tested)."""
    order: list[int] = []
    last: dict[int, float] = {}
    for ep, cl in ticks:
        if ep is None or cl is None:
            continue
        k = int(ep) // bar_seconds
        if k not in last:
            order.append(k)
        last[k] = cl
    closed = order[:-1] if len(order) >= 2 else []
    return [(k, last[k]) for k in closed]


def ohlc_bars(ticks, bar_seconds: int = BAR_SECONDS) -> list[tuple[int, float, float, float, float]]:
    """``[(epoch, close), ...]`` → ``[(bar_key, o, h, l, c), ...]`` for CLOSED buckets only
    (forming bucket excluded, same rule as :func:`closed_bars`). o/h/l/c are derived from
    the ~1s tick closes observed inside each bucket — REAL prices; o/h/l are best-effort
    within the collection window, c (the grading price) is authoritative. PURE."""
    order: list[int] = []
    agg: dict[int, list[float]] = {}
    for ep, cl in ticks:
        if ep is None or cl is None:
            continue
        k = int(ep) // bar_seconds
        if k not in agg:
            order.append(k)
            agg[k] = []
        agg[k].append(cl)
    closed = order[:-1] if len(order) >= 2 else []
    return [(k, agg[k][0], max(agg[k]), min(agg[k]), agg[k][-1]) for k in closed]


def _persist_bars(ledger, symbol: str, ticks, prev_key: int, bar_seconds: int) -> int:
    """Write the window's NEWLY-closed bars (bar_key > *prev_key*) to the ledger's ``bars``
    table with ts = bar CLOSE epoch — the price history that makes fires gradable after
    the fact (utah/product/fire_grader.py). Best-effort: a store hiccup (or a minimal
    ledger without record_bars) never stops the feed loop."""
    if not hasattr(ledger, "record_bars"):
        return 0
    rows = [((k + 1) * bar_seconds, o, h, l, c)
            for k, o, h, l, c in ohlc_bars(ticks, bar_seconds) if k > prev_key]
    if not rows:
        return 0
    try:
        return ledger.record_bars(symbol, rows, bar_seconds=bar_seconds)
    except Exception as exc:  # noqa: BLE001 — persistence must never kill the feed
        from utah import failures
        failures.record("trading", "bars_persist_failed", str(exc))
        return 0


def ingest(state: dict, symbol: str, ticks, *, bar_seconds: int = BAR_SECONDS,
           max_bars: int = MAX_BARS) -> list[float]:
    """Merge a window of ``ticks`` into *symbol*'s persistent closed-bar history in *state*,
    deduped by bar_key so overlapping collection windows never double-count. Returns the
    symbol's rolling list of closed-bar closes. PURE (unit-tested)."""
    s = state.setdefault(symbol, {"last_key": -1, "closes": []})
    for k, close in closed_bars(ticks, bar_seconds):
        if k > s["last_key"]:
            s["closes"].append(close)
            s["last_key"] = k
    if len(s["closes"]) > max_bars:
        del s["closes"][:-max_bars]
    return s["closes"]


def _wc_page() -> dict | None:
    """The logged-in WC dashboard CDP target, or None if WC isn't reachable / on /login."""
    try:
        pages = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{CDP_PORT}/json", timeout=4))
    except Exception as exc:  # noqa: BLE001 — CDP down = feed unavailable, not an error
        log.debug("wc_feed: CDP not reachable on :%d (%s)", CDP_PORT, exc)
        return None
    return next((p for p in pages
                 if WC_HOST in (p.get("url") or "")
                 and p.get("type") == "page"
                 and p.get("webSocketDebuggerUrl")
                 and "/login" not in (p.get("url") or "")), None)


def feed_available() -> bool:
    """True when a logged-in WC dashboard is reachable on the CDP port — the gate trading.py
    checks. False = chrome-wc not launched or bounced to login; engines stay dormant."""
    return _wc_page() is not None


def collect_ticks(seconds: float = 30.0, *, page=None) -> dict[str, list[tuple[int, float]]]:
    """Attach to the live WC feed and collect ``{symbol: [(epoch, close), ...]}`` for
    *seconds*. Read-only (only Network.enable). Never raises."""
    import asyncio
    import time as _time

    page = page or _wc_page()
    if not page:
        return {}
    series: dict[str, list[tuple[int, float]]] = {}

    async def _run():
        import websockets

        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
            t0 = _time.monotonic()
            while _time.monotonic() - t0 < seconds:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                if m.get("method") != "Network.webSocketFrameReceived":
                    continue
                cd = parse_candle(m["params"]["response"]["payloadData"])
                if cd:
                    series.setdefault(cd["symbol"], []).append((cd["epoch"], cd["close"]))

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — one bad stream never aborts the daemon/cron
        log.warning("wc_feed collect failed: %s", exc)
    return series


def run(ledger, *, seconds: float = 30.0, bar_seconds: int = BAR_SECONDS, lookback: int = 20,
        engine: str = "breakout", state: dict | None = None, collect_fn=None) -> dict:
    """One window: collect live ticks, aggregate to CLOSED bars (merged into persistent
    *state* across windows), and record a REAL fire for any symbol whose latest closed bar
    breaks its recent range. GATED: if WC is unreachable, record the gate (never faked).
    Returns ``{available, symbols, evaluated, fires, ready}``. Never raises."""
    from utah import failures
    from utah.product import trading

    state = state if state is not None else {}
    if not feed_available():
        failures.record("trading", "feed_gated",
                        "WC dashboard not reachable on CDP — launch chrome-wc + log in (Michael)")
        return {"available": False, "symbols": 0, "evaluated": 0, "fires": 0, "ready": 0}
    ticks = (collect_fn or collect_ticks)(seconds)
    fires = evaluated = ready = 0
    for symbol, tk in ticks.items():
        prev_key = state.get(symbol, {}).get("last_key", -1)
        closes = ingest(state, symbol, tk, bar_seconds=bar_seconds)
        _persist_bars(ledger, symbol, tk, prev_key, bar_seconds)
        if len(closes) < lookback + 1:
            continue
        ready += 1
        evaluated += 1
        sig = trading.evaluate(closes, lookback=lookback, engine=engine)
        if sig and sig.get("direction"):
            ledger.record_fire(engine, sig["direction"], entry=closes[-1],
                               synthetic=False, symbol=symbol)
            fires += 1
            log.info("wc_feed FIRE: %s %s @ %.2f (%d bars)", symbol, sig["direction"],
                     closes[-1], len(closes))
    return {"available": True, "symbols": len(ticks), "evaluated": evaluated,
            "fires": fires, "ready": ready}


def ensure_chrome_wc() -> bool:
    """Launch the logged-in ``chrome-wc`` profile with the CDP debug port if WC isn't already
    reachable — Ace bringing up his own market access. Headful (WC's realtime feed needs a real
    render). Returns True once a logged-in WC page is reachable. Never raises."""
    if feed_available():
        return True
    _migrate_wc_profile_once()   # one-time ~/.ace/chrome-wc → ~/.utah/chrome-wc (preserve login)
    if not os.path.isdir(WC_PROFILE):
        log.warning("wc_feed: no chrome-wc profile at %s — Michael's WC login required", WC_PROFILE)
        return False
    import subprocess
    import time as _time

    try:
        subprocess.Popen(
            [CHROME, f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
             f"--user-data-dir={WC_PROFILE}", "--no-first-run", "--no-default-browser-check",
             f"https://{WC_HOST}/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        log.warning("wc_feed: chrome-wc launch failed: %s", exc)
        return False
    for _ in range(20):
        _time.sleep(1.5)
        if feed_available():
            log.info("wc_feed: chrome-wc up, WC feed reachable")
            return True
    log.warning("wc_feed: chrome-wc launched but WC not reachable (login/2FA may be needed)")
    return False


def main() -> int:
    """``com.utah.wcfeed`` entry — keep the WC feed flowing into the engines forever. A single
    persistent per-symbol bar history accumulates across windows (so the lookback warms up and
    breakouts are real bars, not tick jitter). Tunables: UTAH_WC_INTERVAL, UTAH_WC_BAR_SECONDS,
    UTAH_WC_LOOKBACK."""
    import time as _time

    from utah import failures
    from utah.product.ledger import Ledger

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    interval = float(os.environ.get("UTAH_WC_INTERVAL", "30"))
    lookback = int(os.environ.get("UTAH_WC_LOOKBACK", "20"))
    ledger = Ledger()
    try:
        ledger.init_schema()   # bars table + fires.symbol exist before the first persist
    except Exception as exc:  # noqa: BLE001 — store down at boot: record, loop will retry
        failures.record("trading", "bars_schema", str(exc))
    state: dict = {}
    while True:
        if not ensure_chrome_wc():
            failures.record("trading", "feed_gated",
                            "chrome-wc not reachable — Michael's WC login/2FA required")
            _time.sleep(interval)
            continue
        try:
            r = run(ledger, seconds=interval, lookback=lookback, state=state)
            bars = {s: len(v["closes"]) for s, v in state.items()}
            log.info("wcfeed cycle: %s | bars=%s", r, bars)
        except Exception as exc:  # noqa: BLE001 — never let one cycle kill the loop
            log.warning("wcfeed cycle failed: %s", exc)
        _time.sleep(1.0)


__all__ = ["parse_candle", "closed_bars", "ohlc_bars", "ingest", "feed_available",
           "collect_ticks", "run", "ensure_chrome_wc", "main", "CDP_PORT", "WC_HOST",
           "BAR_SECONDS"]


if __name__ == "__main__":
    raise SystemExit(main())

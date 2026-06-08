"""WealthCharts live feed bridge — attach to Michael's logged-in WC dashboard over the
Chrome DevTools Protocol and stream its realtime candle feed into Utah's trading engines.

WC runs in a dedicated, already-logged-in Chrome profile (``~/.ace/chrome-wc``) launched with
``--remote-debugging-port``. Its dashboard opens a WebSocket to WC's market server and receives
frames like::

    {"cmd":"feed","data":{"type":"candle","c":"CM.MNQM6",
     "candle":{"co":29374.0,"cM":29374.0,"cm":29374.0,"cc":29374.0,"cepoch":1781045182,"type":"rt"}}}

We read those frames READ-ONLY via CDP (never send an order) and feed the per-symbol close
series to ``trading.evaluate`` → ``ledger.record_fire``. Real data, real fires, never faked —
when WC isn't reachable the gate is recorded and the engines stay dormant.
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
WC_PROFILE = os.path.expanduser(os.environ.get("UTAH_WC_PROFILE", "~/.ace/chrome-wc"))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_candle(payload: str) -> dict | None:
    """Parse one WC WebSocket frame → ``{symbol, close, open, high, low, epoch}`` or None.

    PURE (unit-tested). Returns None for keepalives, non-feed, or non-candle frames, and for
    malformed JSON — so a junk frame never crashes the bridge or fabricates a price."""
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
    if close is None:
        return None
    return {"symbol": symbol, "close": close, "open": _f(candle.get("co")),
            "high": _f(candle.get("cM")), "low": _f(candle.get("cm")),
            "epoch": candle.get("cepoch")}


def _wc_page() -> dict | None:
    """The logged-in WC dashboard CDP target, or None if WC isn't reachable / on the login
    page. Bounced-to-login (no session) reads as unavailable — never a fake feed."""
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


def collect_closes(seconds: float = 12.0, *, page=None) -> dict[str, list[float]]:
    """Attach to the live WC feed and collect per-symbol realtime closes for *seconds*.
    Read-only (only Network.enable is sent — never an order). Returns ``{symbol: [close,...]}``
    in arrival order. Never raises (a feed hiccup yields whatever was collected)."""
    import asyncio
    import time as _time

    page = page or _wc_page()
    if not page:
        return {}
    series: dict[str, list[float]] = {}

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
                    series.setdefault(cd["symbol"], []).append(cd["close"])

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — one bad stream never aborts the daemon/cron
        log.warning("wc_feed collect failed: %s", exc)
    return series


def run(ledger, *, seconds: float = 20.0, lookback: int = 20, engine: str = "breakout",
        collect_fn=None) -> dict:
    """Stream the live WC feed and record a REAL fire for any symbol whose latest close breaks
    its recent range (trading.evaluate). GATED: if WC isn't reachable, record the gate and fire
    nothing (never faked). Returns ``{available, symbols, fires, evaluated}``. Never raises."""
    from utah import failures
    from utah.product import trading

    if not feed_available():
        failures.record("trading", "feed_gated",
                        "WC dashboard not reachable on CDP — launch chrome-wc + log in (Michael)")
        return {"available": False, "symbols": 0, "fires": 0, "evaluated": 0}
    series = (collect_fn or collect_closes)(seconds)
    fires = evaluated = 0
    for symbol, closes in series.items():
        if len(closes) < lookback + 1:
            continue
        evaluated += 1
        sig = trading.evaluate(closes, lookback=lookback, engine=engine)
        if sig and sig.get("direction"):
            ledger.record_fire(engine, sig["direction"], entry=closes[-1], synthetic=False)
            fires += 1
            log.info("wc_feed FIRE: %s %s @ %.2f", symbol, sig["direction"], closes[-1])
    log.info("wc_feed run: symbols=%d evaluated=%d fires=%d", len(series), evaluated, fires)
    return {"available": True, "symbols": len(series), "fires": fires, "evaluated": evaluated}


def ensure_chrome_wc() -> bool:
    """Launch the logged-in ``chrome-wc`` profile with the CDP debug port if WC isn't already
    reachable — Ace bringing up his own market access. Headful (WC's realtime feed needs a real
    render). Returns True once a logged-in WC page is reachable. Never raises."""
    if feed_available():
        return True
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
    """``com.utah.wcfeed`` entry — keep the WC feed flowing into the engines forever. Each
    cycle ensures chrome-wc is up, collects a window of live closes, and records real fires
    (gates honestly when WC is unreachable). Tunables: UTAH_WC_INTERVAL, UTAH_WC_LOOKBACK."""
    import time as _time

    from utah import failures
    from utah.product.ledger import Ledger

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    interval = float(os.environ.get("UTAH_WC_INTERVAL", "60"))
    lookback = int(os.environ.get("UTAH_WC_LOOKBACK", "20"))
    ledger = Ledger()
    while True:
        if not ensure_chrome_wc():
            failures.record("trading", "feed_gated",
                            "chrome-wc not reachable — Michael's WC login/2FA required")
            _time.sleep(interval)
            continue
        try:
            log.info("wcfeed cycle: %s", run(ledger, seconds=interval * 0.8, lookback=lookback))
        except Exception as exc:  # noqa: BLE001 — never let one cycle kill the loop
            log.warning("wcfeed cycle failed: %s", exc)
        _time.sleep(max(2.0, interval * 0.2))


__all__ = ["parse_candle", "feed_available", "collect_closes", "run",
           "ensure_chrome_wc", "main", "CDP_PORT", "WC_HOST"]


if __name__ == "__main__":
    raise SystemExit(main())

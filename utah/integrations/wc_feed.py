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


def _env_int(name: str, default: int) -> int:
    """Integer env tunable — garbage falls back to the default instead of crashing the
    import (a typo'd launchd plist must not take the whole feed daemon down)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("wc_feed: env %s=%r is not an int — using %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    """Float env tunable — same fallback contract as :func:`_env_int`."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("wc_feed: env %s=%r is not a float — using %s", name, raw, default)
        return default


#: Utah's OWN CDP port. NOT 9222: old Ace's bridge (and its leftover chromes) own :9222, and
#: a port shared across profiles means the probe can reach the WRONG chrome — proven live
#: 2026-06-10 (old-Ace chrome held IPv4 :9222 with zero tabs; Utah's logged-in chrome could
#: only bind [::1]; the loop relaunched flag-less Chrome every cycle = 21 piled-up WC tabs).
CDP_PORT = _env_int("UTAH_WC_CDP_PORT", 9223)
#: Chrome binds whichever loopback is free — probe both (IPv4 first, then IPv6).
CDP_HOSTS = ("127.0.0.1", "[::1]")
WC_HOST = "app.wealthcharts.com"
CHROME_APP = "/Applications/Google Chrome.app"  # for `open -g -a` (background launch, no focus steal)
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
BAR_SECONDS = _env_int("UTAH_WC_BAR_SECONDS", 15)
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


def _frame_candle(raw) -> dict | None:
    """One CDP envelope string → candle dict or None. PURE and junk-proof: the old
    inline ``json.loads(raw)`` + ``m["params"]["response"]["payloadData"]`` ran BARE
    inside the stream loop, so a single malformed CDP frame killed the WHOLE
    persistent hook (caught only by the outer hook-dropped handler = full re-attach).
    Every malformed variant must be a quiet None, never an exception."""
    try:
        m = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(m, dict) or m.get("method") != "Network.webSocketFrameReceived":
        return None
    params = m.get("params")
    if not isinstance(params, dict):
        return None
    response = params.get("response")
    if not isinstance(response, dict):
        return None
    payload = response.get("payloadData")
    if not isinstance(payload, str):
        return None
    return parse_candle(payload)


def normalize_epoch(epoch: int, arrival: float, *, step: int = 900) -> int:
    """Strip WC's exchange-wall-clock lie from a frame's epoch. Equity candles arrive
    stamped in EXCHANGE time written as if it were a unix epoch (+3600s vs reality,
    proven in the bars table 2026-06-10: every US.* bar landed 1h in the future while
    CM.* futures were true) — so fire grading and the deck saw "future" bars. Ticks
    reach us within ~2s of their stamp, so any whole multiple of *step* (15 min, the
    smallest real timezone granularity) between stamp and ARRIVAL clock is timezone
    skew, not latency: snap it to zero and keep the sub-step remainder (the real
    intra-bar timing) exactly. PURE (unit-tested)."""
    skew = round((int(epoch) - arrival) / step) * step
    return int(epoch - skew)


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


def _cdp_pages() -> list | None:
    """The CDP target list from whichever loopback our chrome bound, or None when no chrome
    answers on Utah's port at all (= chrome down, portless, or wedged)."""
    for host in CDP_HOSTS:
        try:
            return json.load(urllib.request.urlopen(
                f"http://{host}:{CDP_PORT}/json", timeout=4))
        except Exception as exc:  # noqa: BLE001 — CDP down = feed unavailable, not an error
            log.debug("wc_feed: CDP not reachable on %s:%d (%s)", host, CDP_PORT, exc)
    return None


def _cdp_reachable() -> bool:
    return _cdp_pages() is not None


def _wc_any_page() -> dict | None:
    """ANY WealthCharts page (login wall included) — the don't-pile-tabs check."""
    pages = _cdp_pages() or []
    return next((p for p in pages
                 if WC_HOST in (p.get("url") or "") and p.get("type") == "page"), None)


def _wc_page() -> dict | None:
    """The logged-in WC dashboard CDP target, or None if WC isn't reachable / on /login."""
    pages = _cdp_pages() or []
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
                cd = _frame_candle(raw)
                if cd:
                    ep = normalize_epoch(cd["epoch"], _time.time())
                    series.setdefault(cd["symbol"], []).append((ep, cd["close"]))

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


def _wc_chrome_pids() -> list[int]:
    """PIDs of the MAIN Chrome process(es) running against WC_PROFILE (Helpers excluded).
    Empty list = no chrome owns the profile, so a fresh launch is safe. Never raises."""
    import subprocess

    try:
        # "--" is load-bearing: the pattern starts with "-", and macOS pgrep
        # parses it as an illegal OPTION (exit 2, empty stdout) → this returned
        # [] while a healthy chrome-wc owned the profile → every converge cycle
        # blind-spawned a ProcessSingleton-doomed chrome (85 in a row, 2026-06-11)
        # and the tab-reopen/restart healing paths were dead code.
        out = subprocess.run(["pgrep", "-f", "--", f"--user-data-dir={WC_PROFILE}"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001 — can't enumerate = treat as none running
        return []
    pids = []
    for tok in out.split():
        try:
            pid = int(tok)
            cmd = subprocess.run(["ps", "-p", tok, "-o", "command="],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:  # noqa: BLE001
            continue
        if "MacOS/Google Chrome" in cmd and "Helper" not in cmd:
            pids.append(pid)
    return pids


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:  # noqa: BLE001 — e.g. EPERM = something's there
        return True


def _kill_wc_chrome(pids) -> None:
    """TERM (then KILL) our wedged chrome-wc so a flagged relaunch can own the CDP port.
    Only ever called on PIDs that are chrome-wc mains. Never raises."""
    import signal
    import time as _time

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001 — already gone
            pass
    deadline = _time.monotonic() + 8
    while _time.monotonic() < deadline and any(_pid_alive(p) for p in pids):
        _time.sleep(0.5)
    for pid in pids:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass


def _open_wc_tab() -> bool:
    """Reopen the WC dashboard as a tab in the ALREADY-RUNNING chrome via CDP — never a
    second Popen (a flag-less relaunch is exactly the tab-spam bug). Never raises."""
    for host in CDP_HOSTS:
        try:
            req = urllib.request.Request(
                f"http://{host}:{CDP_PORT}/json/new?https://{WC_HOST}/", method="PUT")
            urllib.request.urlopen(req, timeout=4)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


#: Chrome quietly PAUSES occluded/background tabs (timer throttling, renderer backgrounding,
#: Memory Saver tab discard) — on the WC tab that freezes the realtime feed while the page
#: still looks "open" (the live signature: cycles report available:True but symbols:0, and
#: WC shows a paused chart). The feed tab must never sleep just because the window is behind
#: Michael's other windows.
CHROME_NO_THROTTLE_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=HighEfficiencyModeAvailable,MemorySaverModeAggressiveness",
)
#: Michael's directive (2026-06-10): the WC window never lands in his face again. Spawns
#: start SMALL in the bottom-right corner — present (WC needs a real rendered session;
#: minimizing risks the page unsubscribing its feed) but out of the way.
CHROME_WINDOW_FLAGS = ("--window-position=1100,760", "--window-size=700,480")


def _spawn_chrome() -> bool:
    """Fresh chrome-wc with the CDP flag. Callers must ensure no chrome owns the profile.

    Launched via ``open -g`` (background, NEVER raised to the foreground) so respawning the
    feed window can never steal Michael's keyboard focus mid-typing — directly Popen-ing the
    Chrome binary activates the app every time, which (with the heal-loop respawning on a
    dropped feed) was yanking the caret away repeatedly. ``-n`` forces a separate instance on
    the chrome-wc profile; the CDP/no-throttle/window flags pass through after ``--args``."""
    import subprocess

    try:
        subprocess.Popen(
            ["open", "-g", "-n", "-a", CHROME_APP, "--args",
             f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
             f"--user-data-dir={WC_PROFILE}", "--no-first-run", "--no-default-browser-check",
             *CHROME_NO_THROTTLE_FLAGS, *CHROME_WINDOW_FLAGS, f"https://{WC_HOST}/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("wc_feed: chrome-wc launch failed: %s", exc)
        return False


def _await_feed(seconds: float = 30.0) -> bool:
    import time as _time

    deadline = _time.monotonic() + seconds
    while True:
        if feed_available():
            log.info("wc_feed: chrome-wc up, WC feed reachable on :%d", CDP_PORT)
            return True
        if _time.monotonic() >= deadline:
            return False
        _time.sleep(1.5)


class BarStream:
    """Incremental tick→closed-bar processor for the PERSISTENT attach.

    The original design re-attached to the CDP websocket every 30s window (collect →
    detach → sleep → re-attach): ~120 reconnects/hour, Chrome flashing its debug banner
    and reflowing the WC page on every attach — Michael's "why does it keep looping; run
    it ONE time and get the hook for the data" (2026-06-10). This class is the hook's
    consumer half: ``feed()`` ticks as they arrive on the ONE connection; ``flush()``
    folds newly-closed bars into the persistent per-symbol state, persists them, and
    evaluates the engine — never touching the connection. All bar math reuses the same
    pure helpers (``ingest``/``ohlc_bars``) the windowed path proved out."""

    def __init__(self, ledger, *, bar_seconds: int = BAR_SECONDS, lookback: int = 20,
                 engine: str = "breakout", state: dict | None = None,
                 max_bars: int = MAX_BARS, sig: dict | None = None):
        self.ledger = ledger
        self.bar_seconds = bar_seconds
        self.lookback = lookback
        self.engine = engine
        self.state = state if state is not None else {}
        self.max_bars = max_bars
        self.buf: dict[str, list[tuple[int, float]]] = {}
        #: EDGE-firing state: last signal direction per (engine, symbol). An engine
        #: fires when its signal APPEARS or FLIPS — never again on every extended bar
        #: (level-firing produced 271 fires/hr on 2026-06-10 and paged the phone for
        #: each one). A no-signal bar re-arms the edge. CALLER-OWNED like ``state``
        #: (pass the same dict across re-hooks/restarts): a fresh empty dict made
        #: every signal look newborn — each hook drop or restart re-recorded the
        #: whole active roster as "new" fires (~30 phantoms per restart, 2026-06-10).
        self._sig: dict[tuple[str, str], str | None] = sig if sig is not None else {}
        #: forming-bucket key per symbol — lets feed() report the INSTANT a bar closes
        #: so the consumer flushes on bar-close, not on a wall timer ("none are coming
        #: in millisecond time", 2026-06-10).
        self._forming: dict[str, int] = {}

    def feed(self, symbol: str, epoch: int, close: float) -> bool:
        """Buffer one tick; write it through to the live-tick surface; return True the
        instant this tick ROLLS the symbol's bar bucket (its previous bar just closed)
        so the caller can flush immediately instead of waiting for the timer."""
        self.buf.setdefault(symbol, []).append((int(epoch), float(close)))
        if hasattr(self.ledger, "record_tick"):
            try:
                self.ledger.record_tick(symbol, float(close), int(epoch))
            except Exception:  # noqa: BLE001 — the live surface must never stall the hook
                pass
        key = int(epoch) // self.bar_seconds
        prev = self._forming.get(symbol)
        self._forming[symbol] = key
        return prev is not None and key > prev

    def flush(self) -> dict:
        from utah.product import trading

        fires = evaluated = ready = ticked = 0
        for symbol, ticks in list(self.buf.items()):
            if not ticks:
                continue
            ticked += 1
            prev_key = self.state.get(symbol, {}).get("last_key", -1)
            closes = ingest(self.state, symbol, ticks, bar_seconds=self.bar_seconds,
                            max_bars=self.max_bars)
            _persist_bars(self.ledger, symbol, ticks, prev_key, self.bar_seconds)
            new_key = self.state[symbol]["last_key"]
            # bound the hook's memory: only the still-forming bucket's ticks are kept
            self.buf[symbol] = [(e, c) for (e, c) in ticks
                                if e // self.bar_seconds > new_key]
            if len(closes) < self.lookback + 1:
                continue
            ready += 1
            if new_key == prev_key:
                continue       # only the forming bar grew — nothing new to judge
            evaluated += 1
            # EVERY implemented engine judges the same closed bar under its OWN name —
            # separate fires, separate grading, separate dash rows. EDGE-fired: a fire
            # records only when the signal appears or flips, not on every extended bar.
            for eng in trading.implemented_engines():
                sig = trading.evaluate(closes, lookback=self.lookback, engine=eng)
                direction = (sig or {}).get("direction")
                prev = self._sig.get((eng, symbol))
                self._sig[(eng, symbol)] = direction
                if direction and direction != prev:
                    # Same suppression contract as trading.run — one open position
                    # per engine + flat-time cooldown. The live hook bypassed it
                    # (edge-firing re-armed on every momentary signal drop) and
                    # re-created the fire storm: 1,556 fires/24h, every row
                    # provenance-less. Defensive getattr keeps minimal test
                    # ledgers working (same pattern as trading.run).
                    state = getattr(self.ledger, "fire_state",
                                    lambda e: {"open": False, "last_fire_age_s": None})(eng)
                    age = state.get("last_fire_age_s")
                    if state.get("open") or (age is not None and age < trading.FIRE_COOLDOWN_S):
                        log.info("wc_feed suppressed: %s %s %s (%s)", eng, symbol, direction,
                                 "in_position" if state.get("open") else "cooldown")
                        continue
                    ctx = trading._fire_context(closes, sig, lookback=self.lookback)
                    self.ledger.record_fire(eng, direction, entry=closes[-1],
                                            synthetic=False, symbol=symbol,
                                            stop=ctx.get("stop"), target=ctx.get("target"),
                                            rationale=ctx.get("rationale"))
                    fires += 1
                    log.info("wc_feed FIRE: %s %s %s @ %.2f (%d bars)", eng, symbol,
                             direction, closes[-1], len(closes))
        return {"symbols": len(self.buf), "ticked": ticked, "evaluated": evaluated,
                "fires": fires, "ready": ready,
                "bars": {s: len(v.get("closes", [])) for s, v in self.state.items()}}


class SilentWatch:
    """Fire-once detector for a hooked-but-silent feed. WC renders its LOGIN WALL at the
    root URL (proven live 2026-06-10: page title 'WealthCharts', body = Email/Password
    form, zero frames) so the /login URL gate can't see a dead session — the only honest
    signal is N consecutive empty flushes. ``note(ticks)`` returns True exactly once per
    silence episode; ticks flowing re-arms it."""

    def __init__(self, threshold: int = 4):
        self.threshold = threshold
        self.empty = 0
        self.paged = False

    def note(self, symbols_with_ticks: int) -> bool:
        if symbols_with_ticks > 0:
            self.empty = 0
            self.paged = False
            return False
        self.empty += 1
        if self.empty >= self.threshold and not self.paged:
            self.paged = True
            return True
        return False


def warm_from_ledger(ledger, *, bar_seconds: int = BAR_SECONDS, lookback: int = 20,
                     max_bars: int = MAX_BARS) -> tuple[dict, dict]:
    """Rebuild the feed's working state from the DURABLE bars table after a restart —
    no state file, the ledger IS the state. Returns ``(state, sig)`` for BarStream:

    - ``state``: each symbol's rolling closes + last_key, so the lookback is warm on
      the first bar instead of re-accumulating for 5+ minutes, and already-persisted
      bars are never re-judged.
    - ``sig``: each engine's CURRENT signal direction recomputed from those closes,
      so a signal that was already on before the restart does NOT re-fire (the
      2026-06-10 ~30-phantom-fires-per-restart storm); a genuine flip still does.

    Never raises — a cold store just means a cold start (state={}, sig={})."""
    from utah.product import trading

    state: dict = {}
    sig: dict = {}
    try:
        symbols = ledger.bar_symbols()
    except Exception:  # noqa: BLE001 — store down at boot = cold start, loop retries
        return state, sig
    for symbol in symbols:
        try:
            rows = ledger.recent_closes(symbol, max_bars)
        except Exception:  # noqa: BLE001
            continue
        if not rows:
            continue
        closes = [c for _, c in rows]
        # storage convention (record_bars): ts = (k+1)*bar_seconds → k = ts//bs - 1
        last_key = int(rows[-1][0]) // bar_seconds - 1
        state[symbol] = {"last_key": last_key, "closes": closes}
        for eng in trading.implemented_engines():
            s = trading.evaluate(closes, lookback=lookback, engine=eng)
            sig[(eng, symbol)] = (s or {}).get("direction")
    return state, sig


def stream(ledger, *, interval: float = 30.0, bar_seconds: int = BAR_SECONDS,
           lookback: int = 20, engine: str = "breakout", state: dict | None = None,
           sig: dict | None = None) -> None:
    """ONE persistent hook: attach to the live WC websocket ONCE and consume ticks until
    the connection itself drops — flushing closed bars every *interval* seconds without
    ever detaching. Returns only when the hook is lost (caller re-establishes).
    ``state`` AND ``sig`` are caller-owned so neither bar history nor edge-firing
    state resets across re-hooks (a reset sig re-fired the whole roster)."""
    import asyncio
    import time as _time

    page = _wc_page()
    if not page:
        return
    bs = BarStream(ledger, bar_seconds=bar_seconds, lookback=lookback,
                   engine=engine, state=state, sig=sig)
    watch = SilentWatch()

    async def _run():
        import websockets

        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
            log.info("wc_feed: HOOKED — one persistent attach on :%d (no reconnect loop)",
                     CDP_PORT)
            last = _time.monotonic()
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    cd = _frame_candle(raw)
                    if cd:
                        ep = normalize_epoch(cd["epoch"], _time.time())
                        if bs.feed(cd["symbol"], ep, cd["close"]):
                            # a bar just CLOSED — persist + evaluate NOW (ms after
                            # the roll), not at the next wall-timer flush.
                            bs.flush()
                if _time.monotonic() - last >= interval:
                    r = bs.flush()
                    log.info("wcfeed flush: %s", r)
                    last = _time.monotonic()
                    if watch.note(r.get("ticked", 0)):
                        # hooked but SILENT = almost always the WC login wall (renders at
                        # the root URL, invisible to the /login gate) — page Michael.
                        from utah import alerts, failures
                        failures.record("trading", "feed_silent",
                                        "WC hook live but no ticks — log into the corner "
                                        "WC window (session likely expired)")
                        alerts.critical_async("wcfeed",
                                              "WC feed silent — log into the corner "
                                              "WealthCharts window (session expired)",
                                              key="wcfeed/silent")

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — hook dropped (chrome/page/network)
        log.warning("wc_feed: hook dropped (%s) — re-establishing", exc)


def ensure_chrome_wc() -> bool:
    """Bring up the logged-in ``chrome-wc`` profile on Utah's CDP port — Ace bringing up his
    own market access. Headful (WC's realtime feed needs a real render). Converges instead of
    churning: a live profile chrome is never Popen'd at again (Chrome would ignore the debug
    flags and just open ANOTHER tab — the 2026-06-10 21-tab pileup). Healing paths:
    tab closed → reopen via CDP; chrome wedged/portless → restart it; logged out → gate and
    wait for Michael (his one-time login/2FA). Never raises."""
    if feed_available():
        return True
    _migrate_wc_profile_once()   # one-time ~/.ace/chrome-wc → ~/.utah/chrome-wc (preserve login)
    if not os.path.isdir(WC_PROFILE):
        log.warning("wc_feed: no chrome-wc profile at %s — Michael's WC login required", WC_PROFILE)
        return False
    pids = _wc_chrome_pids()
    if pids:
        if _cdp_reachable():
            if _wc_any_page() is None:
                # chrome healthy on our port, WC tab simply closed → reopen IN-PLACE.
                if _open_wc_tab() and _await_feed(10):
                    return True
            # WC tab exists but no live feed = login wall / 2FA. Piling tabs or restart
            # churn can't fix a logged-out session — gate honestly and wait for Michael.
            log.warning("wc_feed: chrome-wc up but no logged-in WC page (login/2FA may be needed)")
            return False
        # chrome owns the profile but doesn't answer on OUR port (lost the bind / wedged):
        # only a restart WITH the flag can reattach it.
        log.warning("wc_feed: chrome-wc %s unreachable on CDP :%d — restarting it", pids, CDP_PORT)
        _kill_wc_chrome(pids)
    if not _spawn_chrome():
        return False
    if _await_feed(30):
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
    interval = _env_float("UTAH_WC_INTERVAL", 30.0)
    lookback = _env_int("UTAH_WC_LOOKBACK", 20)
    ledger = Ledger()
    try:
        ledger.init_schema()   # bars table + fires.symbol exist before the first persist
    except Exception as exc:  # noqa: BLE001 — store down at boot: record, loop will retry
        failures.record("trading", "bars_schema", str(exc))
    # Rebuild bar history + edge-firing state from the DURABLE bars table so a restart
    # neither re-fires the active roster (~30 phantoms/restart on 2026-06-10) nor
    # spends 5+ minutes re-warming the lookback from zero.
    state, sig = warm_from_ledger(bar_seconds=BAR_SECONDS, lookback=lookback,
                                  ledger=ledger)
    log.info("wcfeed warm boot: %d symbols, %d signal edges restored from the ledger",
             len(state), sum(1 for v in sig.values() if v))
    while True:
        if not ensure_chrome_wc():
            failures.record("trading", "feed_gated",
                            "chrome-wc not reachable — Michael's WC login/2FA required")
            _time.sleep(interval)
            continue
        try:
            # ONE persistent hook — returns only if the connection drops; bar AND
            # edge state survive re-hooks so the lookback never restarts from zero
            # and an unchanged signal never re-fires.
            stream(ledger, interval=interval, lookback=lookback, state=state, sig=sig)
        except Exception as exc:  # noqa: BLE001 — never let one drop kill the loop
            log.warning("wcfeed stream failed: %s", exc)
        _time.sleep(2.0)


__all__ = ["parse_candle", "normalize_epoch", "closed_bars", "ohlc_bars", "ingest", "feed_available", "BarStream", "stream", "SilentWatch", "warm_from_ledger",
           "collect_ticks", "run", "ensure_chrome_wc", "main", "CDP_PORT", "WC_HOST",
           "BAR_SECONDS"]


if __name__ == "__main__":
    raise SystemExit(main())

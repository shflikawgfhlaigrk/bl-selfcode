"""Engine bridge — ingest the restored Apex-Prime fleet's fires into Utah.

The Utah-native port of AceOS's ``engine_listener``. The original engines
(``~/debt/*``: Bible, Apex, Perplexity, Barber, Context Alpha/Bravo) run as their
own services (``com.utah.engine-*``) on the live WealthCharts feed, each exposing
``/api/trade_state``. This bridge polls them, diffs each frame against the prior one,
and on every transition emits a fire event that it:

  * appends to ``~/.utah/cache/engine_fires.jsonl`` (the original's log format), and
  * pages to Michael's phone on an OPEN (via :mod:`utah.product.trade_alert`).

Detection is a pure function (:func:`detect_fires`) so it is tested fully offline; the
HTTP poll, the sink, and the clock are injected. Nothing here ever raises into the
loop — a dead engine or torn frame is skipped, never a crash.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Callable

log = logging.getLogger("utah.integrations.engine_bridge")

#: The restored fleet: (key, display name, dashboard port). Mirrors the
#: com.utah.engine-* launchd services.
FLEET: list[tuple[str, str, int]] = [
    ("bible",     "Apex Signal Bible",   8400),
    ("apex",      "Apex (NoYahoo V2)",   8100),
    ("research",  "Perplexity Research", 8300),
    ("barber",    "Ace Barber",          8200),
    ("ctx_alpha", "Context Alpha",       8600),
    ("ctx_bravo", "Context Bravo",       8700),
]

FIRES_LOG = os.path.expanduser(
    os.environ.get("UTAH_ENGINE_FIRES_LOG", "~/.utah/cache/engine_fires.jsonl"))
POLL_SECONDS = float(os.environ.get("UTAH_ENGINE_BRIDGE_POLL_S", "2.0"))
HTTP_TIMEOUT_S = float(os.environ.get("UTAH_ENGINE_BRIDGE_HTTP_TIMEOUT_S", "4.0"))


def detect_fires(engine: str, prev: dict | None, cur: dict) -> list[dict]:
    """Diff two ``/api/trade_state`` frames → the fire events between them.

    Pure. ``prev is None`` (cold start) records state and emits nothing. A fire is:
      * ``open``  — ``active_trade`` went falsy → truthy (entry)
      * ``close`` — ``active_trade`` went truthy → falsy (exit)
      * ``count_up`` — ``trades_today`` rose with no open/close transition (a scalp
        that opened and closed between two polls)
    """
    if prev is None or not isinstance(cur, dict):
        return []
    fires: list[dict] = []
    prev_at = prev.get("active_trade") or None
    cur_at = cur.get("active_trade") or None

    if cur_at and not prev_at:
        at = cur_at if isinstance(cur_at, dict) else {}
        fires.append({
            "kind": "open", "engine": engine,
            "direction": at.get("direction"), "entry": at.get("entry"),
            "stop": at.get("stop"), "target": at.get("target"),
            "session_pnl": cur.get("session_pnl"),
        })
    elif prev_at and not cur_at:
        at = prev_at if isinstance(prev_at, dict) else {}
        fires.append({
            "kind": "close", "engine": engine,
            "direction": at.get("direction"), "entry": at.get("entry"),
            "session_pnl": cur.get("session_pnl"),
        })

    prev_n = prev.get("trades_today") or 0
    cur_n = cur.get("trades_today") or 0
    if cur_n > prev_n and not (cur_at and not prev_at):
        fires.append({"kind": "count_up", "engine": engine,
                      "trades_today": cur_n, "session_pnl": cur.get("session_pnl")})
    return fires


def _get_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": "utah-engine-bridge/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        obj = json.loads(resp.read().decode("utf-8"))
    return obj if isinstance(obj, dict) else None


def _trades_to_state(trades: dict) -> dict:
    """Map an ``/api/trades`` summary to a trade_state-shaped frame. Older engine
    builds (e.g. apex) don't expose ``/api/trade_state``; their completed-trade
    ``total`` still surfaces as a ``count_up`` so the engine isn't invisible."""
    return {"active_trade": None, "trades_today": trades.get("total") or 0,
            "session_pnl": trades.get("total_pnl") or 0.0}


def _http_trade_state(key: str, port: int) -> dict | None:
    """Fetch one engine's live state. Prefers ``/api/trade_state`` (carries the open
    position); falls back to ``/api/trades`` for engine builds that lack it. ``None``
    on any failure — a down engine is skipped, never fatal."""
    try:
        return _get_json(f"http://127.0.0.1:{port}/api/trade_state")
    except Exception as exc:  # noqa: BLE001 — try the fallback before giving up
        log.debug("engine %s (:%s) trade_state miss (%s); trying /api/trades", key, port, exc)
    try:
        trades = _get_json(f"http://127.0.0.1:{port}/api/trades")
        return _trades_to_state(trades) if trades is not None else None
    except Exception as exc:  # noqa: BLE001 — engine down/unreachable
        log.debug("engine %s (:%s) unavailable: %s", key, port, exc)
        return None


def _append_jsonl(event: dict, path: str, now: Callable[[], float]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        event = {**event, "ts": now()}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
    except OSError as exc:
        log.warning("engine fires log write failed (non-fatal): %s", exc)


def _page_on_open(event: dict) -> None:
    """Page Michael on an OPEN — reuse Utah's existing fire-alert path. Best-effort."""
    if event.get("kind") != "open":
        return
    try:
        from utah.product import trade_alert
        trade_alert.send_fire_alert({
            "engine": event.get("engine"), "direction": event.get("direction"),
            "entry": event.get("entry"), "stop": event.get("stop"),
            "target": event.get("target"), "source": "engine_bridge",
        })
    except Exception as exc:  # noqa: BLE001 — alerting never blocks ingestion
        log.debug("engine fire alert skipped: %s", exc)


class EngineBridge:
    """Polls the fleet, detects fires, and routes them to a sink (default: jsonl + page)."""

    def __init__(self, *, roster=None, fetch=None, sink=None, now=None):
        self.roster = roster if roster is not None else FLEET
        self._fetch = fetch or _http_trade_state
        self._now = now or time.time
        self._sink = sink or self._default_sink
        self._prev: dict[str, dict] = {}

    def _default_sink(self, event: dict) -> None:
        _append_jsonl(event, FIRES_LOG, self._now)
        _page_on_open(event)

    def poll(self) -> int:
        """One poll across the whole fleet. Returns the number of fires emitted."""
        emitted = 0
        for key, _name, port in self.roster:
            cur = self._fetch(key, port)
            if cur is None:
                continue
            prev = self._prev.get(key)
            for event in detect_fires(key, prev, cur):
                try:
                    self._sink(event)
                    emitted += 1
                except Exception as exc:  # noqa: BLE001 — one bad sink never stops the sweep
                    log.warning("engine fire sink failed: %s", exc)
            self._prev[key] = cur
        return emitted

    def run_forever(self, *, poll_seconds: float = POLL_SECONDS) -> None:
        log.info("engine_bridge: watching %d engines, poll=%.1fs → %s",
                 len(self.roster), poll_seconds, FIRES_LOG)
        while True:
            try:
                self.poll()
            except Exception as exc:  # noqa: BLE001 — the loop is immortal
                log.warning("engine_bridge poll error (continuing): %s", exc)
            time.sleep(poll_seconds)


def main() -> None:  # pragma: no cover — the launchd entry point
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    EngineBridge().run_forever()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["detect_fires", "EngineBridge", "FLEET", "main"]

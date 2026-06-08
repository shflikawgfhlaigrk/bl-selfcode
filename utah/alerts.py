"""Alert taxonomy — which live events page Michael's phone, at what priority, with
storm suppression and quiet hours. The four streams Michael chose:

    critical       — real breakage (daemon down, load critical). Emergency (prio 2):
                     re-alerts until acked, BYPASSES quiet hours.
    trade          — an engine fire (entry/dir). High (prio 1), bypasses quiet hours.
                     Dormant until the WealthCharts feed lands (no synthetic fires).
    brief          — the daily morning brief + a tap-through to the Utah deck over the
                     tailnet. Normal (prio 0), RESPECTS quiet hours.
    leads_probate  — the daily pipeline summary (new leads / probate rows). Low
                     (prio -1), silent on the phone, RESPECTS quiet hours.

Every function is a thin, NEVER-raising wrapper over the pushover transport. Dedup is
in-memory (per-process): the long-lived daemon de-dupes critical/trade storms; the
once-a-day cron paths key dedup on the date. The actual push goes through an injectable
sender (:func:`set_sender`) so the whole suite runs with zero network and zero real
pushes — captured at spawn-time for the threaded critical path so a test teardown can
never let a late thread reach the real transport.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import datetime, time as dtime

from utah import config

log = logging.getLogger("utah.alerts")

_SEEN: dict[str, float] = {}   # in-memory dedup: key -> last-sent epoch seconds
_SENDER = None                 # injectable real-push seam (tests pin a fake)
_LOCK = threading.Lock()


def set_sender(fn) -> None:
    """Inject the push sender (tests). ``None`` restores the real Pushover transport."""
    global _SENDER
    _SENDER = fn


def _now() -> datetime:
    return datetime.now()


def in_quiet_hours(now: datetime | None = None) -> bool:
    """Whether *now* falls in the configured quiet-hours window (handles a window
    that wraps past midnight). Malformed config => never quiet (fail loud, not silent)."""
    now = now or _now()
    try:
        sh, sm = (int(x) for x in config.QUIET_HOURS_START.split(":"))
        eh, em = (int(x) for x in config.QUIET_HOURS_END.split(":"))
    except Exception:  # noqa: BLE001
        return False
    start, end, t = dtime(sh, sm), dtime(eh, em), now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # wraps midnight


def _dedup_ok(key: str | None, ttl: int) -> bool:
    """True if *key* has not fired within *ttl* seconds; records the hit. ``None`` key
    always passes (no dedup)."""
    if not key:
        return True
    now = _time.time()
    with _LOCK:
        last = _SEEN.get(key, 0.0)
        if now - last < ttl:
            return False
        _SEEN[key] = now
    return True


def _send(stream: str, message: str, *, title: str, target: str | None = None,
          url: str | None = None, url_title: str | None = None,
          dedup_key: str | None = None, dedup_ttl: int | None = None,
          sender=None) -> dict:
    """Apply the stream gates (enabled? quiet hours? deduped?) then push. Never raises."""
    try:
        if stream not in config.ALERT_STREAMS_ENABLED:
            return {"sent": False, "gated": True, "reason": f"stream:{stream}:disabled"}
        priority = config.ALERT_PRIORITY.get(stream, 0)
        # Only normal/low streams respect quiet hours; high/emergency always wake the phone.
        if priority < 1 and in_quiet_hours():
            return {"sent": False, "gated": True, "reason": "quiet_hours"}
        if not _dedup_ok(dedup_key, dedup_ttl or config.ALERT_DEDUP_SECONDS):
            return {"sent": False, "gated": True, "reason": "deduped"}
        push = sender if sender is not None else _SENDER
        if push is None:
            from utah.integrations import pushover
            push = pushover.send
        return push(message, title=title, priority=priority, target=target,
                    url=url, url_title=url_title)
    except Exception as exc:  # noqa: BLE001 — paging must never crash the caller
        log.debug("alert send swallowed (stream=%s): %s", stream, exc, exc_info=True)
        return {"sent": False, "gated": False, "error": str(exc)}


# --- the four streams ----------------------------------------------------------

def critical(source: str, detail: str = "", *, key: str | None = None, sender=None) -> dict:
    """Page the phone about real breakage (emergency, bypasses quiet hours)."""
    return _send("critical", f"{source}: {detail}".strip(": "),
                 title="⚠️ Utah CRITICAL", dedup_key=f"critical:{key or source}",
                 sender=sender)


def critical_async(source: str, detail: str = "", *, key: str | None = None) -> None:
    """Fire-and-forget critical page on a background thread — so the hot failure-record
    path never blocks on the network. Captures the sender NOW so a test teardown can't
    let the late thread reach the real transport."""
    captured = _SENDER
    def _go() -> None:
        try:
            critical(source, detail, key=key, sender=captured)
        except Exception:  # noqa: BLE001
            pass
    try:
        threading.Thread(target=_go, name="utah-alert-critical", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def trade_fire(engine: str, direction: str, entry, *, fire_id=None,
               target: float | None = None, stop: float | None = None, sender=None) -> dict:
    """Page an engine fire (entry / direction / optional target & stop)."""
    msg = f"{str(engine).upper()} {str(direction).upper()} @ {entry}"
    if target is not None:
        msg += f"  tgt {target}"
    if stop is not None:
        msg += f"  stop {stop}"
    if fire_id is not None:
        msg += f"  (fire #{fire_id})"
    return _send("trade", msg, title="📈 Utah trade fire",
                 dedup_key=(f"trade:{fire_id}" if fire_id is not None else None),
                 sender=sender)


def brief(text: str, *, sender=None) -> dict:
    """Push the morning brief with a tap-through to the deck over the tailnet."""
    return _send("brief", text, title="☀️ Utah morning brief",
                 url=config.DECK_TAILNET_URL, url_title="Open the Utah deck",
                 dedup_key=f"brief:{_now():%Y-%m-%d}", dedup_ttl=12 * 3600, sender=sender)


def leads_probate(result: dict, *, kind: str = "leads", sender=None) -> dict:
    """Push the daily pipeline summary from a ``run_scheduled`` result dict."""
    r = result or {}
    if kind == "probate":
        msg = f"{r.get('new', 0)} new probate rows ({r.get('found', 0)} seen)"
    elif kind == "outreach":
        msg = (f"{r.get('sent', 0)} outreach emails sent "
               f"({r.get('suppressed', 0)} already-contacted, {r.get('blocked', 0)} blocked)")
    else:
        msg = (f"{r.get('new', 0)} new leads "
               f"({r.get('found', 0)} seen, {r.get('tiles_scanned', 0)} tiles)")
    return _send("leads_probate", msg, title="📋 Utah pipeline",
                 dedup_key=f"{kind}:{_now():%Y-%m-%d}", dedup_ttl=12 * 3600, sender=sender)


__all__ = [
    "set_sender", "in_quiet_hours", "critical", "critical_async",
    "trade_fire", "brief", "leads_probate",
]

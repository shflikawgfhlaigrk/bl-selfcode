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
FILE-BACKED (~/.utah/run/alerts_seen.json): process-memory-only dedup re-paged every
active signal on every wcfeed restart (the 2026-06-10 "hundreds of trade notifications").
The trade stream additionally carries its own longer TTL and a hard per-hour page budget.
The actual push goes through an injectable sender (:func:`set_sender`) so the whole
suite runs with zero network and zero real pushes — captured at spawn-time for the
threaded critical path so a test teardown can never let a late thread reach the real
transport.
"""
from __future__ import annotations

import json
import logging
import threading
import time as _time
from datetime import datetime, time as dtime

from utah import config

log = logging.getLogger("utah.alerts")

_SEEN: dict[str, float] = {}   # dedup: key -> last-sent epoch seconds (file-backed)
_SEEN_LOADED = False           # lazy one-time load of the persisted dedup state
_SENDER = None                 # injectable real-push seam (tests pin a fake)
_LOCK = threading.Lock()


_SEEN_PATH = None              # injectable override (tests isolate to a tmp file)


def set_seen_path(p) -> None:
    """Point the durable dedup file somewhere else (tests). ``None`` = the live path."""
    global _SEEN_PATH
    _SEEN_PATH = p


def _seen_path():
    return _SEEN_PATH or (config.UTAH_HOME / "run" / "alerts_seen.json")


def _load_seen_locked() -> None:
    """One-time lazy load of the persisted dedup map. Held under _LOCK."""
    global _SEEN_LOADED
    if _SEEN_LOADED:
        return
    _SEEN_LOADED = True
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            disk = json.load(f)
        now = _time.time()
        # only carry entries young enough to matter for any TTL in use (24h cap)
        _SEEN.update({k: float(v) for k, v in disk.items()
                      if isinstance(v, (int, float)) and now - float(v) < 86400})
    except Exception:  # noqa: BLE001 — no file / corrupt file = fresh start
        pass


def _save_seen_locked() -> None:
    """Write-behind persistence of the dedup map (atomic rename). Held under _LOCK."""
    try:
        p = _seen_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_SEEN, f)
        tmp.replace(p)
    except Exception:  # noqa: BLE001 — persistence is best-effort, never blocks a page
        pass


def _reset_seen_for_tests() -> None:
    """Drop in-memory + loaded state so each test starts from its own tmp file."""
    global _SEEN_LOADED
    with _LOCK:
        _SEEN.clear()
        _SENT_LOG.clear()
        _SEEN_LOADED = False


_SENT_LOG: list[float] = []    # trade-stream budget window: epoch seconds of sends


def _trade_budget_ok() -> bool:
    """Hard cap on trade pages per rolling hour. Fires are ALWAYS ledgered and on the
    deck regardless — the budget only protects the phone. Held under _LOCK."""
    now = _time.time()
    with _LOCK:
        cutoff = now - 3600
        _SENT_LOG[:] = [t for t in _SENT_LOG if t > cutoff]
        if len(_SENT_LOG) >= config.TRADE_ALERTS_PER_HOUR:
            return False
        _SENT_LOG.append(now)
    return True


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
    """True if *key* has not fired within *ttl* seconds; records the hit durably so a
    process restart can never re-page a live storm. ``None`` key always passes."""
    if not key:
        return True
    now = _time.time()
    with _LOCK:
        _load_seen_locked()
        last = _SEEN.get(key, 0.0)
        if now - last < ttl:
            return False
        _SEEN[key] = now
        _save_seen_locked()
    return True


def _mirror_desktop(title: str, message: str, *, priority: int) -> None:
    """Readable Mac fallback when iOS Pushover's notification extension fails to decrypt.

    iPhone lock-screen alerts can show "error decrypting" even with E2E disabled — that's
    Apple's per-device payload encryption + a broken Pushover NSE, not Utah's send path.
    High-priority streams also ping the Mac so breakage still surfaces in plain text.
    The non-push channel rides the courier (the one delivery router); the perms
    pre-check stays so a mirror on an ungranted Mac never records a gated failure."""
    if priority < 1:
        return
    try:
        from utah import courier
        from utah.integrations import notify
        if notify.perms_available():
            courier.deliver(message, via="notify",
                            subject=title.replace("⚠️ ", "").replace("📈 ", ""))
    except Exception as exc:  # noqa: BLE001 — mirror must never block the phone push
        log.debug("desktop mirror swallowed: %s", exc, exc_info=True)


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
        # trade pages additionally burn a per-hour budget — AFTER dedup, so only
        # would-be-real pages consume it; capped fires stay ledgered + on the deck.
        if stream == "trade" and not _trade_budget_ok():
            return {"sent": False, "gated": True, "reason": "trade_budget"}
        push = sender if sender is not None else _SENDER
        if push is None:
            # The live default transport (daemon + crons inject nothing) rides the
            # courier — the one delivery router — instead of hand-picking pushover
            # here. Courier inherits the channel's honest gate (no creds => gated,
            # never fakes). An injected sender still bypasses it: the _SENDER seam
            # is how the whole suite runs with zero network and zero real pushes.
            from utah import courier
            result = courier.deliver(message, via="push", subject=title,
                                     priority=priority, target=target,
                                     url=url, url_title=url_title)
        else:
            result = push(message, title=title, priority=priority, target=target,
                          url=url, url_title=url_title)
        if result.get("sent"):
            _mirror_desktop(title, message, priority=priority)
        return result
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


def prospect_reply(sender_email: str, subject: str = "", *, sender=None) -> dict:
    """Page the phone the moment a PITCHED PROSPECT writes back — the conversion
    moment the entire outreach funnel exists for. Rides the critical stream
    (bypasses quiet hours; a willing buyer should never wait until morning)."""
    msg = f"{sender_email} replied"
    if subject:
        msg += f": {subject[:140]}"
    return _send("critical", msg, title="💰 PROSPECT REPLIED",
                 dedup_key=f"reply:{sender_email}", sender=sender)


def trade_fire(engine: str, direction: str, entry, *, fire_id=None,
               symbol: str | None = None,
               target: float | None = None, stop: float | None = None,
               rationale: str | None = None, sender=None) -> dict:
    """Page an engine fire. DEDUP is engine+symbol+direction with the TRADE TTL (2h
    default, not the 30-min stream TTL) — NEVER per fire_id (unique every time): 271
    fires/hr paged Michael's phone 271 times on 2026-06-10. On top of dedup, a hard
    per-hour budget (TRADE_ALERTS_PER_HOUR) protects the phone from many DISTINCT
    keys storming at once (2 engines x 9 symbols x 2 directions = 36 keys); capped
    fires still land in the ledger and on the deck."""
    msg = f"{str(engine).upper()} {str(direction).upper()} @ {entry}"
    if stop is not None:
        msg += f"  stop {stop}"
    if target is not None:
        msg += f"  tgt {target}"
    if rationale:
        msg += f" — {rationale}"
    if fire_id is not None:
        msg += f"  (fire #{fire_id})"
    return _send("trade", msg, title="📈 Utah trade fire",
                 dedup_key=f"trade:{engine}:{symbol or '?'}:{direction}",
                 dedup_ttl=config.TRADE_ALERT_DEDUP_SECONDS,
                 sender=sender)


def unfed_edge(engine: str, symbol: str, *, win_rate=None, net_pts=None,
               sender=None) -> dict:
    """Page that an engine PROVES edge on a symbol that isn't in the live feed — the
    actionable gap (open that chart). Rides the 'brief' stream (informational, respects
    quiet hours; this is a 'do this when you can', not an emergency). Dedup is
    engine+symbol on the TRADE TTL so a standing gap re-pages at most every couple hours,
    not every 15-min grader tick."""
    wr = f"{win_rate:.0%}" if isinstance(win_rate, (int, float)) else "?"
    net = f"{net_pts:+.0f}pt" if isinstance(net_pts, (int, float)) else "?"
    msg = (f"Edge PROVEN: {symbol} {engine} ({wr} / {net}) — not in the live feed. "
           f"Open the {symbol} chart in WealthCharts to trade it.")
    return _send("brief", msg, title="🎯 Utah unfed edge",
                 dedup_key=f"unfed:{engine}:{symbol}",
                 dedup_ttl=config.TRADE_ALERT_DEDUP_SECONDS, sender=sender)


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
        ch = r.get("channel", "email")
        msg = (f"{r.get('sent', 0)} outreach {ch} sent "
               f"({r.get('queued', 0)} queued, {r.get('suppressed', 0)} suppressed)")
    else:
        msg = (f"{r.get('new', 0)} new leads "
               f"({r.get('found', 0)} seen, {r.get('tiles_scanned', 0)} tiles)")
    return _send("leads_probate", msg, title="📋 Utah pipeline",
                 dedup_key=f"{kind}:{_now():%Y-%m-%d}", dedup_ttl=12 * 3600, sender=sender)


__all__ = [
    "set_sender", "in_quiet_hours", "critical", "critical_async",
    "trade_fire", "brief", "leads_probate",
]

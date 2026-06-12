"""iMessage capability — send a text through the Mac's own Messages.app.

Michael's directive (2026-06-09): "you already have control over my texts — if you
can't find an email, just send a text with the same stuff." This is the local,
no-vendor send path the daemon uses for phone leads when Twilio is not wired:
``osascript`` drives the signed, signed-in Messages.app to send an iMessage (and
fall back to SMS via the Messages relay where the recipient isn't on iMessage).

Honest gate, like every other adapter: if Messages automation isn't permitted
(no TCC Automation grant under launchd) or Messages isn't running/signed-in, the
send records a DOCUMENTED gate and returns ``sent=False`` — never faked. Grant is
a one-line unlock (System Settings → Privacy → Automation → allow controlling
Messages), after which this is fully live. The runner is injectable for tests.

Real sends are capped per hour (:data:`_MAX_PER_HOUR`, file-backed across restarts)
— these texts leave from Michael's PERSONAL number, so a runaway loop is a carrier
spam flag, not a log line.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.imessage")

#: Optional opt-out flag mirroring the other macOS-automation adapters. iMessage
#: needs no JSON secret (Messages is signed in at the OS level), but a present
#: ``imessage.disabled`` flag hard-gates the channel (kill switch for texting).
DISABLED_FLAG = runtime.UTAH_HOME / "secrets" / "imessage.disabled"

_SEND_TIMEOUT_S = 25

#: Hourly send cap — these texts leave from Michael's PERSONAL number, so a runaway
#: loop is a carrier spam flag / contact burn, not a log line. File-backed (one JSON
#: list of send epochs) so the cap survives daemon restarts; env-tunable. The cap
#: applies ONLY to real sends — injected ``send_fn`` (tests, probes) never reads or
#: writes the live machine's rate state.
_RATE_FILE = runtime.RUN_DIR / "imessage_sends.json"
_MAX_PER_HOUR = int(os.environ.get("UTAH_IMESSAGE_MAX_PER_HOUR", "30"))
_RATE_WINDOW_S = 3600.0


def _recent_sends(now: float | None = None) -> list[float]:
    """Send epochs inside the rate window. Missing/garbled state reads as empty —
    bad bookkeeping must never block a prospect's one shot."""
    try:
        rows = json.loads(_RATE_FILE.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    now = now if now is not None else time.time()
    return [float(t) for t in rows
            if isinstance(t, (int, float)) and 0 <= now - float(t) < _RATE_WINDOW_S]


def _record_send(recent: list[float], now: float | None = None) -> None:
    """Append this send to the pruned window. Best-effort: a failed write is logged,
    never raised (the text already went out)."""
    try:
        _RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RATE_FILE.write_text(json.dumps(recent + [now if now is not None else time.time()]))
    except OSError as exc:
        log.warning("imessage: rate-state write failed (%s) — cap may under-count", exc)


def enabled() -> bool:
    """True unless the kill-switch flag is present. (Messages auth is at the OS level.)"""
    return not DISABLED_FLAG.exists()


#: The script takes recipient + body as ``argv`` — message text NEVER appears in
#: AppleScript source. (Live incident 2026-06-09: the old f-string used Python
#: ``!r`` repr — single-quoted strings, which AppleScript does not accept — so
#: every real send died with "syntax error: Expected expression" before reaching
#: Messages. argv-passing makes quoting structurally impossible to get wrong.)
#: SMS-first for cold outreach to phone numbers. The old iMessage-first script returned
#: ``sent:imessage`` even when the recipient isn't on iMessage — AppleScript doesn't
#: error, the message never delivers, but Utah logged ``sent=30`` (live 2026-06-10).
#: Business phones need the SMS service; iMessage is the fallback only.
_SEND_SCRIPT = '''
on run argv
    set theTo to item 1 of argv
    set theBody to item 2 of argv
    tell application "Messages"
        try
            set smsSvc to 1st service whose service type = SMS
            set smsBuddy to buddy theTo of smsSvc
            send theBody to smsBuddy
            return "sent:sms"
        on error smsErr
            try
                set svc to 1st service whose service type = iMessage
                set imBuddy to buddy theTo of svc
                send theBody to imBuddy
                return "sent:imessage"
            on error
                error smsErr
            end try
        end try
    end tell
end run
'''


def _osascript_send(to: str, body: str) -> str:
    """Send via Messages.app (SMS first). Returns channel token ``sms`` or ``imessage``."""
    proc = subprocess.run(
        ["osascript", "-", to, body],
        input=_SEND_SCRIPT,
        capture_output=True, text=True, timeout=_SEND_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "osascript failed").strip())
    tag = (proc.stdout or "").strip()
    if not tag.startswith("sent:"):
        raise RuntimeError(f"unexpected osascript response: {tag!r}")
    return tag.split(":", 1)[1]


def send(to: str, body: str, *, send_fn=None) -> dict:
    """Send a text to *to* via Messages.app. Honest-gated; never raises.

    Returns ``{"sent": bool, "gated": bool, ...}``. With an injected ``send_fn``
    (tests), uses it; otherwise drives the real Messages.app via ``osascript``.
    A missing recipient/body, a disabled channel or a blown hourly cap records a
    documented gate (the cap and rate state apply to REAL sends only)."""
    to = (to or "").strip()
    if not to:
        return {"sent": False, "gated": True, "reason": "no recipient"}
    if not (body or "").strip():
        return {"sent": False, "gated": True, "reason": "empty body"}
    if send_fn is None and not enabled():
        failures.record("imessage", "gated", f"iMessage disabled by flag {DISABLED_FLAG}")
        return {"sent": False, "gated": True, "reason": "imessage disabled"}
    recent: list[float] = []
    if send_fn is None:
        recent = _recent_sends()
        if len(recent) >= _MAX_PER_HOUR:
            failures.record("imessage", "rate_limited",
                            f"{to}: hourly text cap reached ({_MAX_PER_HOUR}/h)")
            return {"sent": False, "gated": True,
                    "reason": f"hourly rate cap reached ({_MAX_PER_HOUR}/h)"}
    try:
        if send_fn is not None:
            send_fn(to, body)
            channel = "imessage"
        else:
            channel = _osascript_send(to, body)
            _record_send(recent)
        log.info("imessage: sent to %s via %s", to, channel)
        return {"sent": True, "gated": False, "channel": channel}
    except Exception as exc:  # noqa: BLE001 — a failed send must never crash outreach
        # Automation-permission / not-signed-in failures land here: document as a GATE
        # (a real one-line unlock), not a hard failure, so the prospect keeps their shot.
        failures.record("imessage", "gated", f"{to}: {exc}")
        return {"sent": False, "gated": True, "error": str(exc)}


__all__ = ["send", "enabled", "DISABLED_FLAG"]

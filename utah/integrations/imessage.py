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
"""
from __future__ import annotations

import logging
import subprocess

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.imessage")

#: Optional opt-out flag mirroring the other macOS-automation adapters. iMessage
#: needs no JSON secret (Messages is signed in at the OS level), but a present
#: ``imessage.disabled`` flag hard-gates the channel (kill switch for texting).
DISABLED_FLAG = runtime.UTAH_HOME / "secrets" / "imessage.disabled"

_SEND_TIMEOUT_S = 25


def enabled() -> bool:
    """True unless the kill-switch flag is present. (Messages auth is at the OS level.)"""
    return not DISABLED_FLAG.exists()


def _osascript_send(to: str, body: str) -> None:  # pragma: no cover — drives Messages.app
    """Send *body* to *to* via Messages.app over iMessage, with an SMS relay fallback.

    Tries the iMessage service first; if the buddy isn't reachable on iMessage,
    falls back to the SMS service (requires Text Message Forwarding from the iPhone).
    Raises on any AppleScript error so the caller can document the gate."""
    script = f'''
    on run
        set theBody to {body!r}
        set theTo to {to!r}
        tell application "Messages"
            try
                set svc to 1st service whose service type = iMessage
                set buddy to participant theTo of svc
                send theBody to buddy
                return "sent:imessage"
            on error
                set smsSvc to 1st service whose service type = SMS
                set smsBuddy to participant theTo of smsSvc
                send theBody to smsBuddy
                return "sent:sms"
            end try
        end tell
    end run
    '''
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=_SEND_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "osascript failed").strip())


def send(to: str, body: str, *, send_fn=None) -> dict:
    """Send a text to *to* via Messages.app. Honest-gated; never raises.

    Returns ``{"sent": bool, "gated": bool, ...}``. With an injected ``send_fn``
    (tests), uses it; otherwise drives the real Messages.app via ``osascript``.
    A missing recipient or a disabled channel records a documented gate."""
    to = (to or "").strip()
    if not to:
        return {"sent": False, "gated": True, "reason": "no recipient"}
    if send_fn is None and not enabled():
        failures.record("imessage", "gated", f"iMessage disabled by flag {DISABLED_FLAG}")
        return {"sent": False, "gated": True, "reason": "imessage disabled"}
    runner = send_fn or _osascript_send
    try:
        runner(to, body)
        return {"sent": True, "gated": False, "channel": "imessage"}
    except Exception as exc:  # noqa: BLE001 — a failed send must never crash outreach
        # Automation-permission / not-signed-in failures land here: document as a GATE
        # (a real one-line unlock), not a hard failure, so the prospect keeps their shot.
        failures.record("imessage", "gated", f"{to}: {exc}")
        return {"sent": False, "gated": True, "error": str(exc)}


__all__ = ["send", "enabled", "DISABLED_FLAG"]

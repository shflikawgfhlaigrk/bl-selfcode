"""SMS capability — Twilio when wired, else the Mac's own iMessage relay.

Twilio creds live at ``~/.utah/secrets/twilio.json``
(``{"account_sid": "...", "auth_token": "...", "from_number": "+1..."}``). When
those are ABSENT, ``send`` falls back to :mod:`utah.integrations.imessage` —
Michael's own texts via Messages.app (his 2026-06-09 directive: "if you can't
find an email, just send a text with the same stuff"). Only when BOTH are
unavailable does ``send`` document a gate and return ``sent=False`` — never faked.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.sms")

TWILIO_CREDS = runtime.UTAH_HOME / "secrets" / "twilio.json"
_TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def creds_available() -> bool:
    return TWILIO_CREDS.exists()


def _twilio_send(to: str, body: str) -> None:
    creds = json.loads(TWILIO_CREDS.read_text())
    sid = creds["account_sid"]
    data = urllib.parse.urlencode({
        "To": to,
        "From": creds["from_number"],
        "Body": body,
    }).encode()
    req = urllib.request.Request(
        _TWILIO_URL.format(sid=sid), data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    import base64
    auth = base64.b64encode(f"{sid}:{creds['auth_token']}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=30) as r:
        json.loads(r.read().decode("utf-8", "replace"))


def send(to: str, body: str, *, send_fn=None) -> dict:
    """Send a text. Priority: injected ``send_fn`` → Twilio (if creds) → iMessage relay.

    With no Twilio creds the message goes out via Messages.app (Michael's texts).
    Only if BOTH Twilio and iMessage are unavailable is the gate documented and
    ``sent=False`` returned. Never faked, never raises."""
    if send_fn is not None:
        try:
            send_fn(to, body)
            return {"sent": True, "gated": False}
        except Exception as exc:  # noqa: BLE001
            failures.record("sms", "send_failed", f"{to}: {exc}")
            return {"sent": False, "gated": False, "error": str(exc)}
    if creds_available():
        try:
            _twilio_send(to, body)
            return {"sent": True, "gated": False, "channel": "twilio"}
        except Exception as exc:  # noqa: BLE001
            failures.record("sms", "send_failed", f"{to}: {exc}")
            return {"sent": False, "gated": False, "error": str(exc)}
    # No Twilio → fall back to the Mac's own iMessage relay (Michael's directive).
    from utah.integrations import imessage

    res = imessage.send(to, body)
    if res.get("sent"):
        return {"sent": True, "gated": False, "channel": res.get("channel", "imessage")}
    # iMessage also unavailable (no Automation grant / not signed in): honest gate.
    return {"sent": False, "gated": True,
            "reason": res.get("error") or res.get("reason") or "no twilio + imessage gated"}


__all__ = ["send", "creds_available", "TWILIO_CREDS"]

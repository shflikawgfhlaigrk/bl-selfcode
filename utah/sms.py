"""SMS capability — GATED on Twilio creds at ``~/.utah/secrets/twilio.json``.

Shape: ``{"account_sid": "...", "auth_token": "...", "from_number": "+1..."}``.
Until creds land, ``send`` documents the gate and returns ``sent=False`` — never faked.
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
    """Send an SMS. With creds or injected ``send_fn``, sends; else gates honestly."""
    if send_fn is None and not creds_available():
        failures.record("sms", "gated", f"SMS gated: no Twilio creds at {TWILIO_CREDS}")
        return {"sent": False, "gated": True, "reason": "no twilio secret"}
    try:
        if send_fn is not None:
            send_fn(to, body)
        else:
            _twilio_send(to, body)
        return {"sent": True, "gated": False}
    except Exception as exc:  # noqa: BLE001
        failures.record("sms", "send_failed", f"{to}: {exc}")
        return {"sent": False, "gated": False, "error": str(exc)}


__all__ = ["send", "creds_available", "TWILIO_CREDS"]

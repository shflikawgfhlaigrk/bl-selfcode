"""SMS capability — Twilio when wired, else the Mac's own iMessage relay.

Twilio creds live at ``~/.utah/secrets/twilio.json``
(``{"account_sid": "...", "auth_token": "...", "from_number": "+1..."}``). When
those are ABSENT, ``send`` falls back to :mod:`utah.integrations.imessage` —
Michael's own texts via Messages.app (his 2026-06-09 directive: "if you can't
find an email, just send a text with the same stuff"). Only when BOTH are
unavailable does ``send`` document a gate and return ``sent=False`` — never faked.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import logging
import os
import urllib.parse
import urllib.request

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.sms")

TWILIO_CREDS = runtime.UTAH_HOME / "secrets" / "twilio.json"
_TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

#: Safe cold-text volume per day. iMessage rides Michael's PERSONAL Apple ID —
#: uncapped cron volume (~46 candidates × 10 runs/day) is the pattern that gets
#: an Apple ID's iMessage deactivated. Mirrors mail.py PER_ACCOUNT_DAILY=30.
PER_DAY = int(os.environ.get("UTAH_SMS_PER_DAY", "30"))
#: Disk-persisted day counter shared across cron processes (cf. mail_rotation.json).
DAILY_COUNTER = runtime.UTAH_HOME / "run" / "sms_daily.json"
#: Bound on the Twilio REST call — a hung API must never stall the outreach cron.
HTTP_TIMEOUT_S = int(os.environ.get("UTAH_SMS_HTTP_TIMEOUT", "30"))


def _today() -> str:
    return _dt.date.today().isoformat()


def _sends_today() -> int:
    try:
        data = json.loads(DAILY_COUNTER.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    return int(data.get("count", 0)) if data.get("date") == _today() else 0


def _record_send() -> None:
    """Burn one day-counter slot. Best-effort by design: the counter is
    BOOKKEEPING for a message that already went out — a write failure (disk
    full, bad perms) must never flip a delivered text into a reported failure,
    so it is logged and swallowed rather than raised into ``send``.

    The read-modify-write is not atomic across processes; two crons racing can
    undercount by one. Acceptable: the cap is a reputation guard with margin,
    not a billing invariant — same trade mail.py makes."""
    try:
        DAILY_COUNTER.parent.mkdir(parents=True, exist_ok=True)
        DAILY_COUNTER.write_text(
            json.dumps({"date": _today(), "count": _sends_today() + 1}))
    except OSError as exc:
        log.warning("sms day-counter write failed (%s): %s", DAILY_COUNTER, exc)


def creds_available() -> bool:
    """True only for USABLE Twilio creds: file present AND sid/token/from all non-empty.

    Bare ``.exists()`` caused the 2026-06-09 incident: a gutted twilio.json
    (empty sid/from) routed every text into a doomed Twilio call — 387
    send_failed/day — and made the iMessage fallback unreachable code."""
    if not TWILIO_CREDS.exists():
        return False
    try:
        creds = json.loads(TWILIO_CREDS.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return all(
        str(creds.get(k) or "").strip()
        for k in ("account_sid", "auth_token", "from_number")
    )


def _twilio_send(to: str, body: str) -> None:
    """One bounded Twilio REST call; raises on any HTTP/API-level failure so the
    caller's honest-result handling owns the outcome."""
    creds = json.loads(TWILIO_CREDS.read_text())
    sid = creds["account_sid"]
    data = urllib.parse.urlencode({
        "To": to,
        "From": creds["from_number"],
        "Body": body,
    }).encode()
    req = urllib.request.Request(
        # quote(): a malformed/poisoned creds file with '/'-bearing sid must not
        # be able to rewrite the request path — escaped into the URL, not spliced.
        _TWILIO_URL.format(sid=urllib.parse.quote(sid, safe="")), data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    auth = base64.b64encode(f"{sid}:{creds['auth_token']}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    # Twilio can 2xx with an embedded error (queued-then-rejected numbers etc.).
    if payload.get("error_code"):
        raise RuntimeError(
            f"twilio error {payload['error_code']}: {payload.get('error_message', '')}")


def send(to: str, body: str, *, send_fn=None) -> dict:
    """Send a text. Priority: injected ``send_fn`` → Twilio (if creds) → iMessage relay.

    With no Twilio creds the message goes out via Messages.app (Michael's texts).
    Only if BOTH Twilio and iMessage are unavailable is the gate documented and
    ``sent=False`` returned. Never faked, never raises."""
    to = (to or "").strip()
    if not to:
        # Caller bug — reject before burning a network call or a relay attempt.
        return {"sent": False, "gated": False, "error": "empty recipient"}
    body = (body or "").strip()
    if not body:
        # Also a caller bug: a blank text would burn a cap slot (and the
        # prospect's one shot) on a message that says nothing.
        return {"sent": False, "gated": False, "error": "empty body"}
    if send_fn is not None:
        try:
            send_fn(to, body)
            return {"sent": True, "gated": False}
        except Exception as exc:  # noqa: BLE001
            failures.record("sms", "send_failed", f"{to}: {exc}")
            return {"sent": False, "gated": False, "error": str(exc)}
    if _sends_today() >= PER_DAY:
        # Reputation guard, not a failure: prospect keeps their one shot.
        return {"sent": False, "gated": True,
                "reason": f"daily text cap reached ({PER_DAY}/day)"}
    if creds_available():
        try:
            _twilio_send(to, body)
        except Exception as exc:  # noqa: BLE001 — never-raises boundary; the cron must survive any API surprise
            failures.record("sms", "send_failed", f"{to}: {exc}")
            return {"sent": False, "gated": False, "error": str(exc)}
        _record_send()
        return {"sent": True, "gated": False, "channel": "twilio"}
    # No Twilio → fall back to the Mac's own iMessage relay (Michael's directive).
    from utah.integrations import imessage

    res = imessage.send(to, body)
    if res.get("sent"):
        _record_send()
        return {"sent": True, "gated": False, "channel": res.get("channel", "imessage")}
    # iMessage also unavailable (no Automation grant / not signed in): honest gate.
    return {"sent": False, "gated": True,
            "reason": res.get("error") or res.get("reason") or "no twilio + imessage gated"}


__all__ = ["send", "creds_available", "TWILIO_CREDS"]

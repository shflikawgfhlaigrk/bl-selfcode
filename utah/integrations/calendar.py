"""Calendar capability — a REAL Google Calendar v3 client behind an honest gate.

Ace's calendar agent transitions HERE as a capability behind the brain, not an agent.
The old skeleton's "real client" could only raise — with creds present it failed every
single create (documented-as-live). This is the genuine path: creds at
``~/.utah/secrets/google.json`` as either ``{"access_token": ...}`` (direct) or the
refresh trio ``{"client_id", "client_secret", "refresh_token"}`` (an access token is
minted per call — Google access tokens expire hourly, refresh tokens don't). With no
USABLE auth it documents the gate and returns ``created=False`` — never fakes an event.
HTTP is one injectable seam (:func:`_http_post_json`) so tests prove the wiring with
zero network; ``client_fn`` stays injectable for callers that bring their own client.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.calendar")

GOOGLE_CREDS = runtime.UTAH_HOME / "secrets" / "google.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
#: Bound on every Google hop (token mint + event create). Env-tunable, never unbounded.
HTTP_TIMEOUT_S = float(os.environ.get("UTAH_CALENDAR_HTTP_TIMEOUT_S", "15"))

#: ``YYYY-MM-DD`` exactly → an all-day event (Google wants ``date``, not ``dateTime``;
#: sending a bare date as ``dateTime`` is a 400 on every all-day create).
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_creds() -> dict:
    """Parse the creds file; ``{}`` when missing/garbled (a gate, not a crash)."""
    try:
        creds = json.loads(GOOGLE_CREDS.read_text())
        return creds if isinstance(creds, dict) else {}
    except (OSError, ValueError):
        return {}


def auth_available() -> bool:
    """True only for USABLE creds: a direct access token, or the full refresh trio.

    A bare ``stat()`` gate lies twice: a garbled file says "wired" and then every real
    create fails, and a half-filled file (client id + secret, no refresh token) can
    mint nothing."""
    creds = _load_creds()
    if creds.get("access_token"):
        return True
    return bool(creds.get("client_id") and creds.get("client_secret")
                and creds.get("refresh_token"))


def _http_post_json(url: str, payload: dict, headers: dict | None = None, *,
                    form: bool = False, timeout: float | None = None) -> dict:
    """Bounded POST → parsed-JSON reply; raises on HTTP/parse failure (callers turn
    that into an honest result). ``form=True`` sends urlencoded (Google's token
    endpoint rejects JSON bodies). Tests inject this away — zero network."""
    if form:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": content_type, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


def _mint_access_token(creds: dict) -> str:
    """Exchange the refresh token for a fresh access token. Raises with Google's own
    error body (e.g. ``invalid_grant``) when the exchange is rejected."""
    reply = _http_post_json(TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": creds.get("refresh_token", ""),
        "client_id": creds.get("client_id", ""),
        "client_secret": creds.get("client_secret", ""),
    }, form=True)
    token = reply.get("access_token")
    if not token:
        raise RuntimeError(f"token refresh rejected: {json.dumps(reply)[:200]}")
    return str(token)


def _when(value: str) -> dict:
    """Google's event-time shape: ``{"date": ...}`` all-day, ``{"dateTime": ...}`` timed."""
    return {"date": value} if _DATE_ONLY.match(value) else {"dateTime": value}


def _google_create(title: str, start: str, end: str) -> str:
    """The real client: (mint token if needed) → POST the event → return its id."""
    creds = _load_creds()
    token = creds.get("access_token") or _mint_access_token(creds)
    reply = _http_post_json(
        EVENTS_URL,
        {"summary": title, "start": _when(start), "end": _when(end)},
        headers={"Authorization": f"Bearer {token}"})
    event_id = reply.get("id")
    if not event_id:
        raise RuntimeError(f"Google created no event: {json.dumps(reply)[:200]}")
    return str(event_id)


def create_event(title: str, start: str, end: str, *, client_fn=None) -> dict:
    """Create a calendar event. Never raises.

    Validation → gate → create: missing fields never reach Google (or the injected
    client); with no usable auth the gate is DOCUMENTED and ``created=False`` comes
    back — never a fabricated event id."""
    title = (title or "").strip()
    if not title or not (start or "").strip() or not (end or "").strip():
        return {"created": False, "gated": False, "title": title,
                "error": "title, start and end are required"}
    if client_fn is None and not auth_available():
        failures.record("calendar", "gated",
                        f"event {title[:40]!r} gated: no usable Google auth at {GOOGLE_CREDS}")
        return {"created": False, "gated": True, "title": title}
    client = client_fn or _google_create
    try:
        event_id = client(title, start, end)
        return {"created": True, "gated": False, "id": event_id, "title": title}
    except Exception as exc:  # noqa: BLE001 — never-raises boundary; callers get an honest result
        failures.record("calendar", "create_failed", f"{title[:40]}: {exc}")
        return {"created": False, "gated": False, "error": str(exc), "title": title}


__all__ = ["create_event", "auth_available", "GOOGLE_CREDS"]

"""Calendar capability — ready-skeleton, GATED on Google Calendar auth.

Ace's calendar agent transitions HERE as a capability behind the brain, not an agent. The
event-create path is wired; it activates when Google creds land at
``~/.utah/secrets/google.json``. With no auth it documents the gate and returns
``created=False`` — never fakes an event. Client injectable for tests.
"""
from __future__ import annotations

import logging

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.calendar")

GOOGLE_CREDS = runtime.UTAH_HOME / "secrets" / "google.json"


def auth_available() -> bool:
    return GOOGLE_CREDS.exists()


def _real_client(title: str, start: str, end: str):  # pragma: no cover
    raise RuntimeError(f"Google Calendar not configured (creds at {GOOGLE_CREDS})")


def create_event(title: str, start: str, end: str, *, client_fn=None) -> dict:
    """Create a calendar event. With an injected client or real auth it creates; with
    neither it documents the gate and returns created=False. Never raises."""
    if client_fn is None and not auth_available():
        failures.record("calendar", "gated",
                        f"event {title[:40]!r} gated: no Google auth at {GOOGLE_CREDS}")
        return {"created": False, "gated": True, "title": title}
    client = client_fn or _real_client
    try:
        event_id = client(title, start, end)
        return {"created": True, "gated": False, "id": event_id, "title": title}
    except Exception as exc:  # noqa: BLE001
        failures.record("calendar", "create_failed", f"{title[:40]}: {exc}")
        return {"created": False, "gated": False, "error": str(exc), "title": title}


__all__ = ["create_event", "auth_available", "GOOGLE_CREDS"]

"""Pushover capability — phone push alerts (Ace's phone-notifier transitions HERE,
not an agent). Real transport: HTTPS POST to api.pushover.net.

Honest gate: with no creds it records the gate and returns ``sent=False, gated=True``
— it NEVER fakes a send. Emergency priority (2) carries ``retry``/``expire`` so the
phone re-alerts until acked. Every send failure is documented to the failure log.

Creds live OUTSIDE the repo at ``~/.utah/secrets/pushover.json`` (Michael's input):
``{api_token, user_key, group_key?, default_target?}``. The HTTP transport is
injectable (``http_post=`` per call, or module-global :func:`set_transport`) so the
whole stack is testable with zero network and zero real pushes.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from utah import config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.pushover")

SECRET = runtime.UTAH_HOME / "secrets" / "pushover.json"
API_URL = "https://api.pushover.net/1/messages.json"

#: Pushover hard limits (we clamp to stay well inside them).
_MSG_MAX, _TITLE_MAX, _URL_MAX, _URLT_MAX = 1024, 250, 512, 100

_transport = None  # injected HTTP poster (tests); None => real urllib


def set_transport(fn) -> None:
    """Inject the HTTP poster (tests). ``None`` restores the real urllib transport."""
    global _transport
    _transport = fn


def _load_creds() -> dict | None:
    try:
        c = json.loads(SECRET.read_text())
        return c if isinstance(c, dict) else None
    except Exception:  # noqa: BLE001 — missing/garbled creds is a gate, not a crash
        return None


def available() -> bool:
    """True when creds are present (token + at least one recipient key)."""
    c = _load_creds()
    return bool(c and c.get("api_token") and (c.get("user_key") or c.get("group_key")))


def _resolve_target(creds: dict, target: str | None) -> str | None:
    """Map a logical target to a real recipient key. ``user``→personal, ``group``→
    delivery group, anything else is treated as a literal key. Falls back across the
    two so a missing one degrades instead of dropping the alert."""
    if not target or target == "user":
        return creds.get("user_key") or creds.get("group_key")
    if target == "group":
        return creds.get("group_key") or creds.get("user_key")
    return target


def _real_http_post(url: str, fields: dict, timeout: float = 10.0):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — fixed https URL
        return r.status, r.read().decode("utf-8", "replace")


def send(message: str, *, title: str = "Utah", priority: int = 0,
         target: str | None = None, url: str | None = None, url_title: str | None = None,
         retry: int | None = None, expire: int | None = None, http_post=None) -> dict:
    """Push *message* to the phone via Pushover. Honest gate (no creds → ``gated``),
    never fakes, never raises. Returns a dict describing the outcome."""
    if not config.PUSHOVER_ENABLED:
        return {"sent": False, "gated": True, "reason": "disabled"}

    creds = _load_creds()
    if not creds or not creds.get("api_token"):
        failures.record("pushover", "gated", f"phone push gated: no creds ({SECRET})")
        return {"sent": False, "gated": True, "reason": "no_creds"}

    to = _resolve_target(creds, target or creds.get("default_target") or config.PUSHOVER_DEFAULT_TARGET)
    if not to:
        failures.record("pushover", "gated", "phone push gated: no user_key/group_key in creds")
        return {"sent": False, "gated": True, "reason": "no_target"}

    fields = {
        "token": creds["api_token"],
        "user": to,
        "message": (message or "")[:_MSG_MAX] or "(empty)",
        "title": (title or "Utah")[:_TITLE_MAX],
        "priority": int(priority),
    }
    if url:
        fields["url"] = url[:_URL_MAX]
    if url_title:
        fields["url_title"] = url_title[:_URLT_MAX]
    if int(priority) >= 2:  # emergency: keep re-alerting until acked
        fields["retry"] = int(retry or config.PUSHOVER_EMERGENCY_RETRY)
        fields["expire"] = int(expire or config.PUSHOVER_EMERGENCY_EXPIRE)

    poster = http_post or _transport or _real_http_post
    try:
        status, body = poster(API_URL, fields)
    except Exception as exc:  # noqa: BLE001 — network/transport failure
        failures.record("pushover", "send_failed", str(exc))
        return {"sent": False, "gated": False, "error": str(exc)}

    try:
        ok = int(status) == 200 and json.loads(body).get("status") == 1
    except Exception:  # noqa: BLE001 — unparseable body counts as failure
        ok = False
    if not ok:
        failures.record("pushover", "send_failed", f"HTTP {status}: {str(body)[:300]}")
        return {"sent": False, "gated": False, "status": status, "body": str(body)[:300]}

    return {"sent": True, "gated": False, "priority": int(priority),
            "target": (to[:6] + "…") if len(to) > 6 else to}


__all__ = ["send", "available", "set_transport", "SECRET", "API_URL"]

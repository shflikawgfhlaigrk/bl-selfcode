"""Introspection capability — Ace's reflector/self_model/operator transition here (NOT
agents): Utah's model of itself — what it is, what it can do, and how it's doing. Built from
live state (daemon status + memory + the registered capability set), never fabricated.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.introspect")

#: Utah's capability surface — the things it can do (Ace agents folded into each).
CAPABILITIES = (
    "leads", "probate", "outreach", "researcher", "selfcode", "brief", "watchdog",
    "maintenance", "mail", "trading", "marketer", "calendar", "notes", "contacts",
    "tasks", "trackers", "advisory", "timers", "notify", "news", "connectivity",
    "macos", "external", "profile", "browser", "courier", "introspect",
)


_UNSET = object()  # distinguishes "not passed" (fetch live) from "explicitly None/empty"


def capabilities() -> tuple[str, ...]:
    return CAPABILITIES


def self_model(*, status=_UNSET, memory_counts=_UNSET) -> dict:
    """Utah's self-model from live state: identity, capability set, memory, health.
    ``status``/``memory_counts`` injectable; default to the live daemon + memory."""
    if status is _UNSET:
        from utah.daemon import client as ctl
        try:
            status = ctl.call_sync("status", timeout=5.0)
        except Exception:  # noqa: BLE001
            status = None
    if memory_counts is _UNSET:
        try:
            from utah import memory
            memory_counts = memory.get_backend().live_counts()
        except Exception:  # noqa: BLE001
            memory_counts = {}
    return {
        "identity": "Utah — clean-room AceOS rebuild: brain loop + capabilities, on Postgres.",
        "capabilities": list(CAPABILITIES),
        "capability_count": len(CAPABILITIES),
        "daemon_up": status is not None,
        "memory": memory_counts,
    }


__all__ = ["capabilities", "self_model", "CAPABILITIES"]

"""Outward integrations — every surface Utah talks to but does not own.

Each submodule wraps one external surface (macOS apps via osascript, Discord
webhooks, the headless browser, the WealthCharts feed, Pushover, Gmail OAuth)
behind never-raises boundary functions with HONEST gates: a missing permission,
dead binary, or empty secret reports ``gated``/``ok: False`` — it never fakes
success and never crashes a caller.

This init stays import-light on purpose: nothing here imports a submodule, so
``from utah.integrations import pushover`` pays only for pushover (several
submodules pull heavy deps lazily). The inventory below locates modules with
``find_spec`` — presence on disk, no side effects — so the deck/foundation can
report which lanes EXIST without booting any of them.
"""
from __future__ import annotations

import importlib.util
import logging

log = logging.getLogger("utah.integrations")

#: Every integration module this package ships. The inventory is the single
#: place that knows the roster — a module that vanishes (iCloud eviction,
#: botched merge) shows up as ``missing`` instead of an ImportError at 3am.
MODULES: tuple[str, ...] = (
    "browser",
    "calendar",
    "contacts",
    "discord",
    "discord_feed",
    "external",
    "imessage",
    "macos",
    "maps",
    "notes",
    "notify",
    "oauth",
    "pushover",
    "social_post",
    "wc_feed",
)


def available(name: str) -> bool:
    """True when *name* is a declared integration whose module is locatable.

    Never raises: unknown names, adversarial strings, and broken package state
    all read as ``False`` — an integration that cannot even be located must
    report dark, not crash the inventory. Locating uses ``find_spec`` (a file
    lookup), NOT an import, so heavy submodules stay unloaded.
    """
    if name not in MODULES:
        return False
    try:
        return importlib.util.find_spec(f"{__name__}.{name}") is not None
    except (ImportError, AttributeError, ValueError) as exc:
        log.warning("could not locate integration %s: %s", name, exc)
        return False


def inventory() -> dict:
    """Honest roster snapshot: ``{"ok": bool, "present": [...], "missing": [...]}``.

    ``ok`` is True only when EVERY declared module is present on disk —
    a partial tree (the iCloud-eviction failure class) is never reported green.
    """
    present = [m for m in MODULES if available(m)]
    missing = [m for m in MODULES if m not in present]
    return {"ok": not missing, "present": present, "missing": missing}


__all__ = ["MODULES", "available", "inventory"]

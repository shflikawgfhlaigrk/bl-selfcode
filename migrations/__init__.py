"""Utah's one-time data migrations — deliberately OUTSIDE ``utah/``.

Migration modules may import legacy stores (e.g. sqlite3 to read old-Ace's
``~/.ace/ace.db``) that the Utah runtime must stay provably free of, so they live in
their own top-level package. Every migration follows the same contract:

* **idempotent** — re-running converges (the memory admission gate turns
  already-migrated rows into REINFORCED, not duplicates);
* **bounded** — legacy reads carry explicit timeouts, Postgres writes ride
  :mod:`utah.db_pool` (``connect_timeout`` + checkout cap + statement timeout);
* **honest** — the entrypoint ``migrate(...)`` returns a stats dict with an ``ok``
  flag and never raises; failures land in the failures ledger, not a traceback.

This init is the front door: :func:`available` discovers what migrations exist,
:func:`load` imports one by allowlist (refusing arbitrary module names), and
:func:`run` is a never-raises dispatch boundary for callers (CLI, selfcode tiers)
that want ``{"ok": bool, ...}`` instead of an import + call dance.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import ModuleType
from typing import Callable

log = logging.getLogger("migrations")

_PKG_DIR = Path(__file__).resolve().parent


def available() -> list[str]:
    """Names of the migration modules shipped in this package, sorted. Never raises.

    Excludes ``__init__``, private ``_helpers``, and anything with a space in the
    stem — iCloud Desktop spawns ``"name 2.py"`` conflict copies that are never
    source (the 2026-06-08 false-RED class) and must not become loadable.
    """
    try:
        return sorted(
            p.stem
            for p in _PKG_DIR.glob("*.py")
            if p.stem != "__init__" and not p.stem.startswith("_") and " " not in p.stem
        )
    except OSError as exc:
        log.error("migrations.available: cannot list %s: %s", _PKG_DIR, exc)
        return []


def load(name: str) -> ModuleType:
    """Import migration *name*, allowlisted against :func:`available`.

    Raises ``ValueError`` for anything not in the allowlist — callers can never
    turn a user-supplied string into an arbitrary import (``"os"``, dotted paths,
    conflict copies all refuse).
    """
    names = available()
    if name not in names:
        raise ValueError(f"unknown migration {name!r}; available: {names}")
    return importlib.import_module(f"{__name__}.{name}")


def run(name: str, *, loader: Callable[[str], ModuleType] = load, **kwargs) -> dict:
    """Run migration *name*'s ``migrate(**kwargs)`` entrypoint. Never raises.

    Returns ``{"ok": bool, "migration": name, "stats": dict | None, "error": str | None}``.
    ``ok`` is honest twice over: False when the dispatch itself fails (unknown name,
    no entrypoint, entrypoint raised) AND False when the migration ran but reported
    ``ok=False`` in its own stats (e.g. aborted on a downed store).

    *loader* is the injectable import seam (tests drive the dispatch without
    touching real stores); production callers leave the default.
    """
    try:
        mod = loader(name)
    except ValueError as exc:
        return {"ok": False, "migration": name, "stats": None, "error": str(exc)}
    entry = getattr(mod, "migrate", None)
    if not callable(entry):
        return {"ok": False, "migration": name, "stats": None,
                "error": f"migration {name!r} has no migrate() entrypoint"}
    try:
        stats = entry(**kwargs)
    except Exception as exc:  # noqa: BLE001 — dispatch boundary: report, never propagate
        log.error("migrations.run(%s) entrypoint raised: %s", name, exc)
        return {"ok": False, "migration": name, "stats": None,
                "error": f"{type(exc).__name__}: {exc}"}
    ok = bool(stats.get("ok", True)) if isinstance(stats, dict) else True
    return {"ok": ok, "migration": name, "stats": stats, "error": None}

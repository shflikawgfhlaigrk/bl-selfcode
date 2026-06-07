"""Contacts capability — ready-skeleton, GATED on macOS automation permission.

Ace's contacts transitions HERE as a capability behind the brain, not an agent. The lookup
path is wired (AppleScript via ``osascript``); it activates when macOS automation perms are
granted (flag ``~/.utah/secrets/macos.json``). With no perms it documents the gate and
returns ``found=False`` — never fabricates a contact. Runner injectable for tests.
"""
from __future__ import annotations

import logging

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.contacts")

MACOS_FLAG = runtime.UTAH_HOME / "secrets" / "macos.json"


def perms_available() -> bool:
    return MACOS_FLAG.exists()


def _real_run(name: str) -> list[dict]:  # pragma: no cover
    import subprocess

    script = (f'tell application "Contacts" to get the name of every person whose '
              f'name contains {name!r}')
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                         timeout=15, check=True).stdout.strip()
    return [{"name": n.strip()} for n in out.split(",") if n.strip()] if out else []


def lookup(name: str, *, run_fn=None) -> dict:
    """Look up macOS contacts by name. Gated on automation perms; documents the gate with
    none. Never raises."""
    if run_fn is None and not perms_available():
        failures.record("contacts", "gated",
                        f"lookup {name[:40]!r} gated: macOS automation perms not granted ({MACOS_FLAG})")
        return {"found": False, "gated": True, "results": []}
    runner = run_fn or _real_run
    try:
        results = runner(name) or []
        return {"found": bool(results), "gated": False, "results": results}
    except Exception as exc:  # noqa: BLE001
        failures.record("contacts", "lookup_failed", f"{name[:40]}: {exc}")
        return {"found": False, "gated": False, "error": str(exc), "results": []}


__all__ = ["lookup", "perms_available", "MACOS_FLAG"]

"""Contacts capability — macOS Contacts lookup via argv-passed AppleScript.

Ace's contacts transitions HERE as a capability behind the brain, not an agent. The
lookup drives ``osascript`` against Contacts.app; it activates when macOS automation
perms are granted (flag ``~/.utah/secrets/macos.json``). With no perms it documents the
gate and returns ``found=False`` — never fabricates a contact.

The query rides ``osascript`` ARGV, never the script source. Same incident class the
iMessage relay fixed live (2026-06-09): interpolating user text via Python ``!r`` repr
produces single-quoted strings AppleScript rejects — every real call dies at compile
time — and a crafted name could inject script (CWE-78 by another door). argv-passing
makes both structurally impossible. Results come back one name per line (names contain
commas — "Smith, Jr." — so comma-splitting shears real contacts apart). Runner
injectable for tests.
"""
from __future__ import annotations

import logging
import subprocess

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.contacts")

MACOS_FLAG = runtime.UTAH_HOME / "secrets" / "macos.json"

_LOOKUP_TIMEOUT_S = 15

#: Query arrives as ``item 1 of argv`` — never compiled into the script. Output is
#: linefeed-joined so names with commas survive the trip back.
_LOOKUP_SCRIPT = '''
on run argv
    set theQuery to item 1 of argv
    tell application "Contacts"
        set matchNames to name of every person whose name contains theQuery
    end tell
    set out to ""
    repeat with n in matchNames
        set out to out & n & linefeed
    end repeat
    return out
end run
'''


def perms_available() -> bool:
    return MACOS_FLAG.exists()


def _osascript_lookup(name: str) -> list[dict]:
    """Run the Contacts query with *name* as argv. Returns ``[{"name": ...}, ...]``.
    Raises on a non-zero exit (TCC denial -1743, Contacts not running, ...)."""
    proc = subprocess.run(
        ["osascript", "-", name],
        input=_LOOKUP_SCRIPT,
        capture_output=True, text=True, timeout=_LOOKUP_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "osascript failed").strip())
    return [{"name": line.strip()} for line in (proc.stdout or "").splitlines()
            if line.strip()]


def lookup(name: str, *, run_fn=None) -> dict:
    """Look up macOS contacts by name. Gated on automation perms; documents the gate
    with none. Never raises.

    Returns ``{"found": bool, "gated": bool, "results": [...]}`` (+``error`` on a real
    failure). An empty query is a gate, not a wildcard — it never reaches osascript."""
    name = (name or "").strip()
    if not name:
        return {"found": False, "gated": True, "results": [], "reason": "empty query"}
    if run_fn is None and not perms_available():
        failures.record("contacts", "gated",
                        f"lookup {name[:40]!r} gated: macOS automation perms not granted ({MACOS_FLAG})")
        return {"found": False, "gated": True, "results": []}
    runner = run_fn or _osascript_lookup
    try:
        results = runner(name) or []
        return {"found": bool(results), "gated": False, "results": results}
    except Exception as exc:  # noqa: BLE001 — never-raises boundary; callers get an honest result
        failures.record("contacts", "lookup_failed", f"{name[:40]}: {exc}")
        return {"found": False, "gated": False, "error": str(exc), "results": []}


__all__ = ["lookup", "perms_available", "MACOS_FLAG"]

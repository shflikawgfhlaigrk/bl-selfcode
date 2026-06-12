"""Notes capability — ready-skeleton, GATED on macOS automation permission.

Ace's notes/apple_notes transitions HERE as a capability behind the brain, not an agent. The
add-note path is wired (AppleScript via ``osascript``); it activates when macOS automation
perms are granted (flag ``~/.utah/secrets/macos.json``). With no perms it documents the gate
and returns ``saved=False`` — never fakes a save. Runner injectable for tests.
"""
from __future__ import annotations

import logging
import subprocess

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.notes")

MACOS_FLAG = runtime.UTAH_HOME / "secrets" / "macos.json"


def perms_available() -> bool:
    return MACOS_FLAG.exists()


def _as_str(s: str) -> str:
    """AppleScript string literal: double quotes + backslash escapes. AppleScript
    accepts ONLY double-quoted strings — the previous ``!r`` (Python repr → SINGLE
    quotes) was an osascript syntax error on every call, the same bug class proven
    live in notify on 2026-06-10."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _script(title: str, body: str) -> str:
    """The AppleScript for one new note — PURE so the literal contract is testable
    without macOS/Notes."""
    return (f'tell application "Notes" to make new note with properties '
            f'{{name:{_as_str(title)}, body:{_as_str(body)}}}')


def _real_run(title: str, body: str) -> bool:  # pragma: no cover
    subprocess.run(["osascript", "-e", _script(title, body)], check=True,
                   capture_output=True, text=True, timeout=15)
    return True


def add_note(title: str, body: str, *, run_fn=None) -> dict:
    """Add a macOS note. Gated on automation perms; documents the gate with none. Never raises."""
    if run_fn is None and not perms_available():
        failures.record("notes", "gated",
                        f"note {title[:40]!r} gated: macOS automation perms not granted ({MACOS_FLAG})")
        return {"saved": False, "gated": True, "title": title}
    runner = run_fn or _real_run
    try:
        runner(title, body)
        return {"saved": True, "gated": False, "title": title}
    except Exception as exc:  # noqa: BLE001
        failures.record("notes", "save_failed", f"{title[:40]}: {exc}")
        return {"saved": False, "gated": False, "error": str(exc), "title": title}


__all__ = ["add_note", "perms_available", "MACOS_FLAG"]

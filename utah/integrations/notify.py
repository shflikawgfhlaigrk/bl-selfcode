"""Notify capability — Ace's alert/notifier transition here (NOT agents): a desktop
notification, GATED on macOS automation perms (flag ``~/.utah/secrets/macos.json``). Wired
via ``osascript display notification``; with no perms it documents the gate and returns
``sent=False`` — never fakes. Runner injectable. (Pushover/phone alerts drop into the same
boundary later when those creds land.)
"""
from __future__ import annotations

import logging
import subprocess

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.notify")

MACOS_FLAG = runtime.UTAH_HOME / "secrets" / "macos.json"


def perms_available() -> bool:
    return MACOS_FLAG.exists()


def _as_str(s: str) -> str:
    """AppleScript string literal: double quotes + backslash escapes. The previous
    ``!r`` (Python repr → SINGLE quotes) was an AppleScript syntax error on every
    call — live 2026-06-10: all trade-fire alerts died with osascript errors while
    the engines fired. AppleScript only accepts double-quoted strings."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osascript_args(title: str, message: str) -> list[str]:
    """The exact osascript argv for one notification — PURE so the escaping contract
    is provable at the argv level without macOS."""
    return ["osascript", "-e",
            f"display notification {_as_str(message)} with title {_as_str(title)}"]


def _osascript_notify(title: str, message: str) -> bool:  # pragma: no cover
    subprocess.run(_osascript_args(title, message),
                   check=True, capture_output=True, text=True, timeout=10)
    return True


def notify(message: str, *, title: str = "Utah", run_fn=None) -> dict:
    """Show a desktop notification. Gated on macOS perms; documents the gate with none.
    Never raises."""
    if run_fn is None and not perms_available():
        failures.record("notify", "gated",
                        f"notification gated: macOS perms not granted ({MACOS_FLAG})")
        return {"sent": False, "gated": True}
    runner = run_fn or _osascript_notify
    try:
        runner(title, message)
        return {"sent": True, "gated": False}
    except Exception as exc:  # noqa: BLE001
        failures.record("notify", "send_failed", str(exc))
        return {"sent": False, "gated": False, "error": str(exc)}


__all__ = ["notify", "perms_available", "MACOS_FLAG"]

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


def _osascript_notify(title: str, message: str) -> bool:  # pragma: no cover
    subprocess.run(["osascript", "-e",
                    f'display notification {message!r} with title {title!r}'],
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

"""macOS ops capability — Ace's clipboard/dictation/files/macos/shortcuts transition here
(NOT agents): read the clipboard, reveal a file, run a Shortcut. GATED on macOS automation
perms (flag ``~/.utah/secrets/macos.json``); wired via pbpaste/open/shortcuts. With no perms
each documents the gate and returns done=False — never fakes. Runners injectable.
"""
from __future__ import annotations

import logging
import subprocess

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.macos")

MACOS_FLAG = runtime.UTAH_HOME / "secrets" / "macos.json"


def perms_available() -> bool:
    return MACOS_FLAG.exists()


def _gate(op: str) -> dict:
    failures.record("macos", "gated", f"{op} gated: macOS perms not granted ({MACOS_FLAG})")
    return {"ok": False, "gated": True}


def read_clipboard(*, run_fn=None) -> dict:
    if run_fn is None and not perms_available():
        return _gate("read_clipboard")
    runner = run_fn or (lambda: subprocess.run(["pbpaste"], capture_output=True, text=True,
                                               timeout=5).stdout)
    try:
        return {"ok": True, "gated": False, "text": runner()}
    except Exception as exc:  # noqa: BLE001
        failures.record("macos", "clipboard_failed", str(exc))
        return {"ok": False, "gated": False, "error": str(exc)}


def reveal_file(path: str, *, run_fn=None) -> dict:
    if run_fn is None and not perms_available():
        return _gate("reveal_file")
    runner = run_fn or (lambda p: subprocess.run(["open", "-R", p], check=True,
                                                 capture_output=True, timeout=5))
    try:
        runner(path)
        return {"ok": True, "gated": False, "path": path}
    except Exception as exc:  # noqa: BLE001
        failures.record("macos", "reveal_failed", str(exc))
        return {"ok": False, "gated": False, "error": str(exc)}


def run_shortcut(name: str, *, run_fn=None) -> dict:
    if run_fn is None and not perms_available():
        return _gate("run_shortcut")
    runner = run_fn or (lambda n: subprocess.run(["shortcuts", "run", n], check=True,
                                                 capture_output=True, text=True, timeout=30))
    try:
        runner(name)
        return {"ok": True, "gated": False, "shortcut": name}
    except Exception as exc:  # noqa: BLE001
        failures.record("macos", "shortcut_failed", str(exc))
        return {"ok": False, "gated": False, "error": str(exc)}


__all__ = ["read_clipboard", "reveal_file", "run_shortcut", "perms_available", "MACOS_FLAG"]

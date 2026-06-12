"""The iMessage relay must never interpolate message text into AppleScript source.

Live incident 2026-06-09: the runner embedded the body via Python ``!r`` repr —
single-quoted strings, which AppleScript rejects — so EVERY real send failed at
compile time ("syntax error: Expected expression"). The fix passes recipient and
body as ``osascript`` argv, so no body content can break (or inject into) the
script. These tests lock that contract.
"""
from __future__ import annotations

import pytest

from utah.integrations import imessage

NASTY_BODIES = [
    "plain ascii",
    "it's got apostrophes",
    'double "quotes" too',
    "em dash — and parens (like this), $700 & <tags>",
    'tell application "Finder" to delete every file',  # injection attempt
    "newline\nand\ttab",
]


@pytest.mark.parametrize("body", NASTY_BODIES)
def test_body_never_lands_in_script_source(body, monkeypatch):
    """Message text must travel as argv, not be compiled as AppleScript."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input", "")

        class P:
            returncode = 0
            stdout = "sent:sms"
            stderr = ""

        return P()

    monkeypatch.setattr(imessage.subprocess, "run", fake_run)
    imessage._osascript_send("+15550001111", body)

    # body and recipient ride argv…
    assert captured["cmd"][-2:] == ["+15550001111", body]
    # …and the compiled script (stdin) contains neither.
    assert body not in captured["input"]
    assert "+15550001111" not in captured["input"]
    assert "on run argv" in captured["input"]


def test_script_is_valid_applescript_syntax():
    """`osascript` parses the WHOLE script before executing anything, so running
    it with no argv proves parseability (it fails at RUNTIME on the missing
    argv item, never with "syntax error" — which is exactly what the old
    f-string/repr version produced on every send)."""
    import subprocess

    proc = subprocess.run(
        ["osascript", "-"],
        input=imessage._SEND_SCRIPT,
        capture_output=True, text=True, timeout=20,
    )
    err = (proc.stderr or "").lower()
    assert "syntax error" not in err
    # parse succeeded → the failure (if any) is the runtime missing-argv error
    assert proc.returncode == 0 or "item 1" in err or "execution error" in err

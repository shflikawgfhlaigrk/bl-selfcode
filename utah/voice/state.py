"""Real voice state for the deck. The mic loop writes a heartbeat'd state file
(``~/.utah/run/voice.json``); the web ``/voice`` route reads it. This replaces the
hardcoded ``{status:idle, listening:false}`` the deck used to show while the voice loop
was actually running — a fake value on a live deck (a no-injection violation). A stale
file (loop dead/hung) reads as ``down``, never a frozen lie.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time

from utah.daemon import runtime

log = logging.getLogger("utah.voice.state")

STATE_PATH = runtime.RUN_DIR / "voice.json"
STALE_S = 15.0  # no heartbeat in this long => the loop is not alive => report 'down'


def write(**fields) -> None:
    """Atomically write the current voice state (stamped with wall-clock ``ts`` so the
    reader can detect staleness). Never raises — voice state is best-effort telemetry."""
    fields["ts"] = time.time()
    try:
        STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(STATE_PATH.parent), prefix=".voice", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(fields, f)
        os.replace(tmp, STATE_PATH)
    except Exception as exc:  # noqa: BLE001
        log.debug("voice state write failed: %s", exc)


def read() -> dict | None:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status(now: float | None = None) -> dict:
    """Normalized live voice status for the deck — REAL or ``down`` (never faked)."""
    now = time.time() if now is None else now
    st = read()
    if not st or "ts" not in st:
        return {"status": "down", "listening": False, "speaking": False}
    age = now - st["ts"]
    if age > STALE_S:
        return {"status": "down", "listening": False, "speaking": False,
                "stale_s": round(age, 1)}
    return {
        "status": st.get("status", "idle"),
        "listening": bool(st.get("listening", False)),
        "speaking": bool(st.get("speaking", False)),
        "segments": st.get("segments", 0),
        "last_transcript": st.get("last_transcript"),
        "last_wake": st.get("last_wake"),
        "age_s": round(age, 1),
    }


__all__ = ["STATE_PATH", "STALE_S", "write", "read", "status"]

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
    reader can detect staleness). Never raises — voice state is best-effort telemetry.
    A failed write cleans up its temp file: the loop heartbeats every few seconds, so
    leaked ``.voice*.json`` litter would otherwise accumulate in the run dir forever."""
    fields["ts"] = time.time()
    tmp: str | None = None
    try:
        STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(STATE_PATH.parent), prefix=".voice", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(fields, f)
        os.replace(tmp, STATE_PATH)
    except Exception as exc:  # noqa: BLE001 — telemetry boundary: degrade, never crash the loop
        log.debug("voice state write failed: %s", exc)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read() -> dict | None:
    """The raw state dict, or ``None`` for missing/corrupt/non-dict content (a JSON
    array is not a heartbeat — callers .get() on the result, so the type is the API)."""
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def status(now: float | None = None) -> dict:
    """Normalized live voice status for the deck — REAL or ``down`` (never faked)."""
    now = time.time() if now is None else now
    st = read()
    if not st or "ts" not in st:
        return {"status": "down", "listening": False, "speaking": False}
    try:
        age = now - float(st["ts"])
    except (TypeError, ValueError):
        # Garbage ts (hand-edited/partial file) — honest degraded answer, never a crash
        # in the /voice route or the supervisor's deaf-restart probe.
        return {"status": "down", "listening": False, "speaking": False}
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

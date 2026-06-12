"""Button barge — cross-process signal from the deck to the voice loop.

Auto barge (loud speech during playback) is off by default. Michael cuts Ace with an
explicit control (◼ BARGE on the deck); that path stops TTS here and drops a request
file the mic loop consumes to arm capture for the next utterance (no wake word).
"""
from __future__ import annotations

import logging
import time

from utah.daemon import runtime

log = logging.getLogger("utah.voice.barge_control")

BARGE_REQUEST = runtime.RUN_DIR / "voice_barge.request"


def request_button_barge() -> None:
    """Ask the voice loop to arm capture after playback was cut (best-effort)."""
    try:
        runtime.RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        BARGE_REQUEST.write_text(f"{time.time():.3f}\n", encoding="utf-8")
    except OSError as exc:
        log.debug("button barge request write failed: %s", exc)


def consume_button_barge() -> bool:
    """True once per button press; voice loop main thread only."""
    try:
        if not BARGE_REQUEST.is_file():
            return False
        BARGE_REQUEST.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def stop_and_arm_barge() -> dict:
    """Kill Ace's voice and arm the mic for Michael's next words."""
    from utah.voice import tts

    out = tts.stop_speaking()
    request_button_barge()
    return {**out, "barge_armed": True}


__all__ = ["BARGE_REQUEST", "request_button_barge", "consume_button_barge", "stop_and_arm_barge"]

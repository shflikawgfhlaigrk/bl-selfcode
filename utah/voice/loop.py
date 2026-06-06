"""Always-on mic loop: capture an utterance, transcribe (MLX Whisper), gate on
the wake word "ace", run the turn through the brain, speak the reply. Resilient —
a bad turn or a transient mic error is logged and the loop continues; it never
fast-exits (so the supervisor doesn't restart-storm). The live "say ace" test is
the human's; everything up to the mic is unit/round-trip proven.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
import wave

from utah.voice import agent, stt

log = logging.getLogger("utah.voice.loop")

SAMPLE_RATE = 16_000
CHANNELS = 1
BLOCK = 1_600  # 100 ms
SILENCE_RMS = float(os.environ.get("UTAH_VOICE_SILENCE", "0.02"))
SILENCE_BLOCKS = int(os.environ.get("UTAH_VOICE_SILENCE_BLOCKS", "15"))  # 1.5 s
MAX_BLOCKS = int(os.environ.get("UTAH_VOICE_MAX_BLOCKS", "150"))  # 15 s cap
PREROLL = 2  # keep 200 ms before onset so the first word isn't clipped


def _rms(pcm) -> float:
    import numpy as np

    a = np.frombuffer(pcm, dtype="int16").astype("float32")
    return float((a * a).mean() ** 0.5) / 32768.0 if a.size else 0.0


def _write_wav(pcm: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="utah_voice_")
    os.close(fd)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return path


def _capture_one(q) -> bytes:
    """Block until a full utterance (speech → 1.5 s silence) is captured."""
    from collections import deque

    preroll: deque[bytes] = deque(maxlen=PREROLL)
    buf = bytearray()
    capturing = False
    silent = 0
    blocks = 0
    while True:
        pcm = q.get()
        loud = _rms(pcm) > SILENCE_RMS
        if not capturing:
            preroll.append(pcm)
            if loud:
                capturing = True
                for p in preroll:
                    buf += p
            continue
        buf += pcm
        blocks += 1
        silent = 0 if loud else silent + 1
        if silent >= SILENCE_BLOCKS or blocks >= MAX_BLOCKS:
            return bytes(buf)


def run() -> None:
    """Run the mic loop forever. Degrades (logs + retries) instead of crashing."""
    try:
        import queue

        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        log.error("voice loop unavailable (audio import failed): %s — idling", exc)
        while True:
            time.sleep(3600)

    q: "queue.Queue[bytes]" = queue.Queue()

    def _cb(indata, frames, t, status):  # noqa: ANN001
        q.put(bytes(indata))

    fail_n = 0
    while True:
        try:
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK,
                                   channels=CHANNELS, dtype="int16", callback=_cb):
                fail_n = 0  # mic opened cleanly
                log.info("voice loop: mic open — always listening for 'ace'")
                while True:
                    pcm = _capture_one(q)
                    path = _write_wav(pcm)
                    try:
                        text = stt.transcribe(path)
                        if text:
                            result = agent.handle_utterance(text)
                            if result and result.get("command"):
                                log.info("voice turn: %r → %r", result["command"],
                                         (result.get("answer") or "")[:60])
                    finally:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
        except Exception as exc:  # noqa: BLE001 — mic unavailable / glitch / device change
            fail_n += 1
            if fail_n == 1:  # log ONCE (no spam if mic permission is missing)
                import sys as _sys
                log.warning(
                    "voice loop: mic unavailable (%s) — retrying quietly; if this persists, "
                    "grant Microphone permission to %s", exc, _sys.executable,
                )
            time.sleep(min(2 * fail_n, 30))  # backoff to 30s — never a tight error loop


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

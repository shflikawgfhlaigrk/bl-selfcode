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
MONITOR_S = 5.0  # how often the level monitor logs the observed mic RMS
SILENCE_ALERT_S = 30.0  # mic delivering TRUE silence (zeros) this long -> record a failure
TRUE_SILENCE = 0.0008  # below this = mic delivering zeros (deaf); a quiet room is well above


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


def _ambient_floor(q, blocks: int = 15) -> float:
    """Sample ~1.5 s to learn the room's noise floor (the max ambient RMS)."""
    mx = 0.0
    for _ in range(blocks):
        mx = max(mx, _rms(q.get()))
    return mx


def _speech_threshold(ambient: float) -> float:
    """Speech must clearly exceed ambient — adaptive so a noisy room (high floor)
    doesn't make every block read 'loud' (the 15 s-blob bug), but the floor is low
    enough to catch normal speech in a quiet room (~0.05+)."""
    return max(ambient * 1.8, ambient + 0.015, 0.02)


def _capture_one(q, threshold: float) -> bytes:
    """Block until a full utterance (speech above *threshold* → 1.5 s below) is
    captured. ``threshold`` is calibrated above the room's ambient floor."""
    from collections import deque

    preroll: deque[bytes] = deque(maxlen=PREROLL)
    buf = bytearray()
    capturing = False
    silent = 0
    blocks = 0
    onset = 0
    while True:
        pcm = q.get()
        loud = _rms(pcm) > threshold
        if not capturing:
            preroll.append(pcm)
            onset = onset + 1 if loud else 0
            if onset >= 2:  # 2 consecutive loud blocks = real speech onset (not a spike)
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

    import threading

    from utah import failures

    q: "queue.Queue[bytes]" = queue.Queue()
    level = {"max": 0.0, "last_loud": time.monotonic(), "alerted": False}

    def _cb(indata, frames, t, status):  # noqa: ANN001
        pcm = bytes(indata)
        r = _rms(pcm)
        if r > level["max"]:
            level["max"] = r
        if r > TRUE_SILENCE:  # mic is ALIVE (hearing the room); only zeros = deaf
            level["last_loud"] = time.monotonic()
            level["alerted"] = False
        q.put(pcm)

    def _monitor():
        # Surfaces a DEAF mic (launchd has no Microphone TCC -> stream opens but
        # delivers silence): logs the level and records ONE failure so it's never
        # a silent failure. Also the Phase-1 diagnostic (is it silence or a bug?).
        while True:
            time.sleep(MONITOR_S)
            mx = level["max"]; level["max"] = 0.0
            log.info("voice: audio level (max rms / %ds) = %.4f", MONITOR_S, mx)
            quiet_for = time.monotonic() - level["last_loud"]
            if quiet_for > SILENCE_ALERT_S and not level["alerted"]:
                level["alerted"] = True
                failures.record(
                    "voice", "mic_silent",
                    f"mic delivering pure silence (zeros) for {int(quiet_for)}s — audio "
                    "device unavailable (not a quiet room — that reads well above zero)",
                )
                log.warning("voice: TRUE silence (zeros) for %ds — recorded mic_silent", int(quiet_for))

    threading.Thread(target=_monitor, daemon=True).start()

    fail_n = 0
    while True:
        try:
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK,
                                   channels=CHANNELS, dtype="int16", callback=_cb):
                fail_n = 0  # mic opened cleanly
                level["last_loud"] = time.monotonic()  # fresh silence countdown
                ambient = _ambient_floor(q)
                threshold = _speech_threshold(ambient)
                log.info("voice loop: mic open — always listening for 'ace' "
                         "(ambient %.4f, speech threshold %.4f)", ambient, threshold)
                while True:
                    pcm = _capture_one(q, threshold)
                    secs = len(pcm) / 2 / SAMPLE_RATE
                    log.info("voice: captured %.1fs clip (rms %.3f) → transcribing", secs, _rms(pcm))
                    path = _write_wav(pcm)
                    try:
                        text = stt.transcribe(path)
                        log.info("voice: transcript=%r", text)
                        if text:
                            result = agent.handle_utterance(text)
                            cmd = result.get("command") if result else None
                            log.info("voice: wake_fired=%s command=%r", result is not None, cmd)
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

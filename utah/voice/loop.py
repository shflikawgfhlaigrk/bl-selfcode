"""Always-on mic loop: Silero VAD segments real SPEECH out of a noisy/musical room,
the segment is transcribed (Moonshine), gated on the wake word "ace", run through the
brain, and the reply spoken (Piper). Resilient — a bad turn or a transient mic error is
logged and the loop continues; it never fast-exits (so the supervisor doesn't
restart-storm). The live "say ace" test is the human's; everything up to the mic is
unit/round-trip proven.

Two faults the live test exposed, both fixed here:
  * energy VAD couldn't tell music from speech -> 15s garbage blobs, wake never fired.
    Silero (utah.voice.vad) scores P(speech) per 32ms frame; only real speech is cut.
  * the mic heard Utah's own TTS (and the room) DURING a turn -> self-triggering. While
    we transcribe/think/speak, mic frames are dropped and the buffer is drained after.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import wave

from utah.voice import agent, state, stt, tts, vad

log = logging.getLogger("utah.voice.loop")

SAMPLE_RATE = vad.SAMPLE_RATE   # 16 kHz
FRAME = vad.FRAME               # 512 samples = 32 ms (Silero-native)
CHANNELS = 1
MONITOR_S = 5.0                 # how often the level monitor logs observed mic RMS
SILENCE_ALERT_S = 30.0          # mic delivering TRUE silence (zeros) this long -> failure
TRUE_SILENCE = 0.0008           # below this = mic delivering zeros (deaf); a room reads higher


def _rms(pcm: bytes) -> float:
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


def run() -> None:
    """Run the mic loop forever. Degrades (logs + retries) instead of crashing."""
    try:
        import queue

        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        log.error("voice loop unavailable (audio import failed): %s — idling", exc)
        while True:
            time.sleep(3600)

    from utah import failures

    q: "queue.Queue[bytes]" = queue.Queue()
    # Set while transcribing / thinking / speaking: the mic callback drops frames so we
    # never capture Utah's own TTS or the room during a turn (the feedback that turned
    # one reply into an endless self-conversation).
    processing = threading.Event()
    level = {"max": 0.0, "last_loud": time.monotonic(), "alerted": False}
    # Real voice state surfaced on the deck /voice route (heartbeat'd by _monitor).
    vstate = {"status": "starting", "listening": False, "speaking": False,
              "segments": 0, "last_transcript": None, "last_wake": None}

    def _cb(indata, frames, t, status):  # noqa: ANN001
        if processing.is_set() or tts.is_anything_playing():
            return  # echo/feedback guard — drop frames while WE *or any process* speak,
            # so the mic never captures another process's TTS (a reply saying "Ace" would
            # else re-fire the wake word → a self-conversation cascade of voices).
        pcm = bytes(indata)
        r = _rms(pcm)
        if r > level["max"]:
            level["max"] = r
        if r > TRUE_SILENCE:
            level["last_loud"] = time.monotonic()
            level["alerted"] = False
        q.put(pcm)

    def _monitor():
        # Surfaces a DEAF mic (no Microphone TCC -> stream opens but delivers silence):
        # logs the level and records ONE failure so it's never silent.
        while True:
            time.sleep(MONITOR_S)
            state.write(**vstate)  # heartbeat: deck sees a live (fresh) voice state
            if processing.is_set():
                level["last_loud"] = time.monotonic()  # not deaf — we muted ourselves
                continue
            mx = level["max"]; level["max"] = 0.0
            log.debug("voice: audio level (max rms / %ds) = %.4f", MONITOR_S, mx)
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

    detector = vad.get_vad()
    fail_n = 0
    while True:
        try:
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME,
                                   channels=CHANNELS, dtype="int16", callback=_cb):
                fail_n = 0
                level["last_loud"] = time.monotonic()
                seg = vad.Segmenter()
                detector.reset()
                vstate.update(status="listening", listening=True, speaking=False)
                state.write(**vstate)
                log.info("voice loop: mic open — Silero VAD listening for 'ace' "
                         "(speech threshold %.2f)", vad.SPEECH_THRESHOLD)

                def _on_speaking() -> None:  # flip to SPEAKING when audio actually starts
                    vstate.update(status="speaking", speaking=True, listening=False)
                    state.write(**vstate)

                while True:
                    frame = q.get()
                    is_speech = detector.prob(frame) > vad.SPEECH_THRESHOLD
                    pcm = seg.feed(frame, is_speech)
                    if pcm is None:
                        continue
                    secs = len(pcm) / 2 / SAMPLE_RATE
                    log.info("voice: speech segment %.1fs → transcribing", secs)
                    processing.set()  # mute the mic for the whole turn (no self-capture)
                    vstate.update(status="thinking", listening=False, speaking=False)
                    state.write(**vstate)
                    try:
                        path = _write_wav(pcm)
                        try:
                            text = stt.transcribe(path)
                            log.info("voice: transcript=%r", text)
                            if text:
                                vstate["last_transcript"] = text[:120]
                                result = agent.handle_utterance(text, on_speaking=_on_speaking)
                                cmd = result.get("command") if result else None
                                log.info("voice: wake_fired=%s command=%r",
                                         result is not None, cmd)
                                if result is not None:
                                    vstate["last_wake"] = cmd or "ace"
                                if result and result.get("command"):
                                    log.info("voice turn: %r → %r", result["command"],
                                             (result.get("answer") or "")[:60])
                        finally:
                            try:
                                os.remove(path)
                            except OSError:
                                pass
                    finally:
                        detector.reset()
                        vstate.update(status="listening", listening=True, speaking=False,
                                      segments=vstate["segments"] + 1)
                        state.write(**vstate)
                        try:  # drop everything the room/TTS queued during the turn
                            while True:
                                q.get_nowait()
                        except queue.Empty:
                            pass
                        level["last_loud"] = time.monotonic()
                        processing.clear()
        except Exception as exc:  # noqa: BLE001 — mic unavailable / glitch / device change
            fail_n += 1
            if fail_n == 1:
                import sys as _sys
                log.warning(
                    "voice loop: mic unavailable (%s) — retrying quietly; if this persists, "
                    "grant Microphone permission to %s", exc, _sys.executable,
                )
            time.sleep(min(2 * fail_n, 30))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Always-on mic loop: openWakeWord arms → Silero VAD captures command → Moonshine
STT → wake/text gate → brain → Piper TTS.

Two-stage wake (permanent):
  Stage A — openWakeWord scores "hey ace" on the live mic and *arms* command capture.
            ONNX never opens a brain turn alone (broadband false-fire lesson from ace).
  Stage B — STT transcript + :func:`wake.resolve_command` extracts the command.
            When Stage A armed the segment, the command is accepted even if Moonshine
            dropped the short "ace" syllable ("is what's the weather" → weather query).

When audio wake is unavailable, falls back to segment-all + transcript-only gate.

Resilience: bad turns and transient mic errors are logged; the loop never fast-exits.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import wave

from utah.voice import agent, oww, state, stt, tts, vad

log = logging.getLogger("utah.voice.loop")

SAMPLE_RATE = vad.SAMPLE_RATE   # 16 kHz
FRAME = vad.FRAME               # 512 samples = 32 ms (Silero-native)
CHANNELS = 1
MONITOR_S = 5.0                 # how often the level monitor logs observed mic RMS
SILENCE_ALERT_S = 30.0          # mic delivering TRUE silence (zeros) this long -> failure
TRUE_SILENCE = 0.0008           # below this = mic delivering zeros (deaf); a room reads higher
MIC_SILENT_COOLDOWN_S = 1800.0  # 30 min


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


def _should_capture(*, audio_wake_ok: bool, armed_until: float) -> bool:
    """Only transcribe speech when audio-wake armed, or when falling back to text-only."""
    if not audio_wake_ok:
        return True  # legacy: segment everything, gate on transcript
    return time.monotonic() < armed_until


def run() -> None:
    """Run the mic loop forever. Degrades (logs + retries) instead of crashing."""
    try:
        import queue

        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        log.error("voice loop unavailable (audio import failed): %s — idling", exc)
        while True:
            time.sleep(3600)

    from utah import config, failures

    wake_det = oww.get_oww()
    audio_wake_ok = wake_det is not None
    if audio_wake_ok:
        log.info("voice loop: two-stage wake ON (openWakeWord → STT → brain)")
    else:
        log.info("voice loop: two-stage wake OFF — transcript-only fallback")

    q: "queue.Queue[bytes]" = queue.Queue()
    processing = threading.Event()
    level = {"max": 0.0, "last_loud": time.monotonic(), "alerted": False, "last_record": 0.0}
    vstate = {"status": "starting", "listening": False, "speaking": False,
              "segments": 0, "last_transcript": None, "last_wake": None}
    armed_until = 0.0

    def _cb(indata, frames, t, status):  # noqa: ANN001
        if processing.is_set() or tts.is_anything_playing():
            return
        pcm = bytes(indata)
        r = _rms(pcm)
        if r > level["max"]:
            level["max"] = r
        if r > TRUE_SILENCE:
            level["last_loud"] = time.monotonic()
            level["alerted"] = False
        q.put(pcm)

    def _monitor():
        while True:
            time.sleep(MONITOR_S)
            state.write(**vstate)
            if processing.is_set():
                level["last_loud"] = time.monotonic()
                continue
            mx = level["max"]; level["max"] = 0.0
            log.debug("voice: audio level (max rms / %ds) = %.4f", MONITOR_S, mx)
            quiet_for = time.monotonic() - level["last_loud"]
            now = time.monotonic()
            cooled = (now - level["last_record"]) > MIC_SILENT_COOLDOWN_S
            if quiet_for > SILENCE_ALERT_S and not level["alerted"] and cooled:
                level["alerted"] = True
                level["last_record"] = now
                failures.record(
                    "voice", "mic_silent",
                    f"mic delivering pure silence (zeros) for {int(quiet_for)}s — audio device "
                    "unavailable; grant Microphone permission to the Utah daemon python in "
                    "System Settings > Privacy & Security > Microphone (TCC).",
                )
                log.warning("voice: TRUE silence (zeros) for %ds — recorded mic_silent", int(quiet_for))

    threading.Thread(target=_monitor, daemon=True).start()

    def _warmup() -> None:
        """Pre-load Silero/Moonshine/Piper so the first real turn is not cold-start."""
        try:
            silent = b"\x00" * vad.FRAME_BYTES
            vad.get_vad().prob(silent)
            stt.get_stt()
            tts.get_tts()
            if wake_det is not None:
                wake_det.feed(silent)
        except Exception as exc:  # noqa: BLE001
            log.debug("voice warmup: %s", exc)

    threading.Thread(target=_warmup, daemon=True).start()

    detector = vad.get_vad()
    fail_n = 0
    while True:
        try:
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME,
                                   channels=CHANNELS, dtype="int16", callback=_cb):
                fail_n = 0
                level["last_loud"] = time.monotonic()
                vad_off = (config.VAD_OFFSET_ARMED if audio_wake_ok
                           else config.VAD_OFFSET)
                seg = vad.Segmenter(offset=vad_off)
                detector.reset()
                if wake_det is not None:
                    wake_det.reset()
                armed_until = 0.0
                vstate.update(status="listening", listening=True, speaking=False)
                state.write(**vstate)
                log.info("voice loop: mic open — Silero VAD (speech threshold %.2f)",
                         vad.SPEECH_THRESHOLD)

                def _on_speaking() -> None:
                    vstate.update(status="speaking", speaking=True, listening=False)
                    state.write(**vstate)

                def _on_audio_wake() -> None:
                    nonlocal armed_until
                    armed_until = time.monotonic() + config.WAKE_ARM_S
                    vstate["last_wake"] = "ace"
                    agent.pulse_wake("")
                    seg.arm()
                    log.info("voice: audio wake armed (%.0fs capture window)", config.WAKE_ARM_S)

                def _process_segment(pcm: bytes, *, segment_armed: bool) -> None:
                    secs = len(pcm) / 2 / SAMPLE_RATE
                    log.info("voice: speech segment %.1fs → transcribing (audio_wake=%s)",
                             secs, segment_armed)
                    processing.set()
                    vstate.update(status="thinking", listening=False, speaking=False)
                    state.write(**vstate)
                    try:
                        path = _write_wav(pcm)
                        try:
                            text = stt.transcribe(path)
                            log.debug("voice: transcript=%r", text)
                            if text:
                                vstate["last_transcript"] = text[:120]
                            if not text and not segment_armed:
                                return
                            result = agent.handle_utterance(
                                text or "",
                                audio_wake=segment_armed,
                                on_speaking=_on_speaking,
                            )
                            cmd = result.get("command") if result else None
                            log.info("voice: wake_fired=%s command=%r",
                                     result is not None, cmd)
                            if result is not None and not vstate.get("last_wake"):
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
                        try:
                            while True:
                                q.get_nowait()
                        except queue.Empty:
                            pass
                        level["last_loud"] = time.monotonic()
                        processing.clear()

                while True:
                    frame = q.get()
                    if wake_det is not None:
                        for hit in wake_det.feed(frame):
                            log.info("voice: openWakeWord hit %s=%.2f",
                                     hit.keyword, hit.confidence)
                            _on_audio_wake()

                    capture = _should_capture(audio_wake_ok=audio_wake_ok,
                                              armed_until=armed_until)
                    if not capture:
                        continue

                    is_speech = detector.prob(frame) > vad.SPEECH_THRESHOLD
                    pcm = seg.feed(frame, is_speech)
                    if pcm is None:
                        continue
                    segment_armed = audio_wake_ok and time.monotonic() < armed_until
                    armed_until = 0.0  # one segment per arm window
                    _process_segment(pcm, segment_armed=segment_armed)
        except Exception as exc:  # noqa: BLE001
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

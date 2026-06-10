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
from utah.voice.liveness import MicLiveness

log = logging.getLogger("utah.voice.loop")

SAMPLE_RATE = vad.SAMPLE_RATE   # 16 kHz
FRAME = vad.FRAME               # 512 samples = 32 ms (Silero-native)
CHANNELS = 1
MONITOR_S = 5.0                 # how often the level monitor logs observed mic RMS
SILENCE_ALERT_S = 30.0          # mic delivering TRUE silence (zeros) this long -> failure
TRUE_SILENCE = 0.0008           # below this = mic delivering zeros (deaf); a room reads higher
MIC_SILENT_COOLDOWN_S = 1800.0  # 30 min

# ── Gated AGC ───────────────────────────────────────────────────────────────
# Measured on Michael's built-in mic: real commands arrive QUIET and erratic — most
# frames score below the Silero speech cutoff, so only the loudest syllable registers
# and the segment is a clipped fragment that transcribes to "". A fixed gain can't help
# (loud syllables would clip); a fixed threshold can't either (it's a coin-flip). Gated
# AGC tracks a fast-attack/slow-release envelope and boosts ONLY frames above a noise
# gate toward a target peak — quiet speech becomes reliably detectable while silence is
# left alone (and amplified noise is still rejected by Silero's spectral model). Disable
# with UTAH_VOICE_AGC=0.
_AGC_ON = os.environ.get("UTAH_VOICE_AGC", "1") == "1"
_AGC_TARGET = float(os.environ.get("UTAH_VOICE_AGC_TARGET", "0.30"))   # target peak (0-1)
_AGC_GATE = float(os.environ.get("UTAH_VOICE_AGC_GATE", "0.025"))      # below this peak = silence, no boost
_AGC_MAX_GAIN = float(os.environ.get("UTAH_VOICE_AGC_MAX_GAIN", "12.0"))

# ── High-pass filter ────────────────────────────────────────────────────────
# The built-in mic on a desk picks up keyboard/chassis vibration + room rumble as
# strong sub-300 Hz energy (measured: 51% of capture energy below 300 Hz), which
# masks speech so Silero scores it as non-speech and the command never segments. A
# 2nd-order Butterworth high-pass at ~120 Hz strips the rumble while preserving the
# voice band (speech intelligibility lives 300 Hz–3.4 kHz). Runs BEFORE AGC so the
# gain boosts cleaned speech, not rumble. Disable with UTAH_VOICE_HPF=0.
_HPF_ON = os.environ.get("UTAH_VOICE_HPF", "1") == "1"
_HPF_HZ = float(os.environ.get("UTAH_VOICE_HPF_HZ", "120"))

# ── Force-capture (VAD-bypass) ──────────────────────────────────────────────
# Silero VAD rejects the command audio on a noisy built-in mic (it scores the
# rumble-contaminated speech as non-speech), so the two-stage wake never segments
# the command. When openWakeWord fires confidently, we instead grab a FIXED window
# and hand it straight to Whisper, which is far more noise-robust than Silero. The
# wake itself is the gate; Whisper returning "" (noise/false-fire) is a harmless
# no-op. Disable with UTAH_VOICE_FORCE_CAPTURE=0.
_FORCE_CAPTURE = os.environ.get("UTAH_VOICE_FORCE_CAPTURE", "1") == "1"
_FORCE_CAPTURE_S = float(os.environ.get("UTAH_VOICE_FORCE_CAPTURE_S", "4.0"))


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
    level = {"max": 0.0}
    # Device liveness, measured on the RAW (pre-DSP) signal — the HPF strips the
    # sub-120Hz floor a quiet room still has, so post-filter "zeros" cannot tell a
    # healthy-but-quiet device from a dead one (2026-06-09 investigation).
    liveness = MicLiveness(threshold=TRUE_SILENCE, alert_after_s=SILENCE_ALERT_S,
                           cooldown_s=MIC_SILENT_COOLDOWN_S)
    vstate = {"status": "starting", "listening": False, "speaking": False,
              "segments": 0, "last_transcript": None, "last_wake": None}
    armed_until = 0.0
    _agc = {"env": _AGC_GATE}  # running peak envelope for gated AGC

    # High-pass filter state (persisted across frames for continuity).
    _hpf = {"b": None, "a": None, "zi": None}
    if _HPF_ON:
        try:
            import numpy as _np
            from scipy.signal import butter, lfilter_zi
            _hpf["b"], _hpf["a"] = butter(2, _HPF_HZ / (SAMPLE_RATE / 2), btype="high")
            _hpf["zi"] = lfilter_zi(_hpf["b"], _hpf["a"]).astype("float64")
            log.info("voice loop: high-pass filter ON (%.0f Hz) — strips desk/rumble noise", _HPF_HZ)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice loop: high-pass filter unavailable (%s) — continuing without", exc)
            _hpf["b"] = None

    def _apply_hpf(pcm: bytes) -> bytes:
        if _hpf["b"] is None:
            return pcm
        import numpy as np
        from scipy.signal import lfilter

        x = np.frombuffer(pcm, dtype="int16").astype("float64")
        if x.size == 0:
            return pcm
        y, _hpf["zi"] = lfilter(_hpf["b"], _hpf["a"], x, zi=_hpf["zi"])
        return np.clip(y, -32768, 32767).astype("int16").tobytes()

    def _apply_agc(pcm: bytes) -> bytes:
        """Boost quiet speech toward a target peak; leave silence/noise alone."""
        import numpy as np

        a = np.frombuffer(pcm, dtype="int16").astype("float32") / 32768.0
        if a.size == 0:
            return pcm
        peak = float(np.abs(a).max())
        # fast attack (jump up to a louder peak), slow release (decay gently)
        _agc["env"] = peak if peak > _agc["env"] else _agc["env"] * 0.92 + peak * 0.08
        if _agc["env"] < _AGC_GATE:        # silence/noise floor — do not amplify
            return pcm
        gain = min(_AGC_TARGET / _agc["env"], _AGC_MAX_GAIN)
        if gain <= 1.0:                    # already at/above target — leave as-is
            return pcm
        out = np.clip(a * gain, -1.0, 1.0)
        return (out * 32767.0).astype("int16").tobytes()

    def _cb(indata, frames, t, status):  # noqa: ANN001
        if processing.is_set() or tts.is_anything_playing():
            liveness.feed_muted()   # intentionally muted — not deaf
            return
        raw = bytes(indata)
        liveness.feed_frame(_rms(raw))   # liveness = RAW-signal question
        pcm = raw
        if _HPF_ON:
            pcm = _apply_hpf(pcm)   # strip sub-120Hz desk/rumble FIRST
        if _AGC_ON:
            pcm = _apply_agc(pcm)   # then boost the cleaned speech
        r = _rms(pcm)
        if r > level["max"]:
            level["max"] = r
        q.put(pcm)

    def _monitor():
        while True:
            time.sleep(MONITOR_S)
            # The callback drops mic frames while WE process OR while ANY process is speaking
            # (echo guard). The monitor must skip those windows too, or it counts a correctly-
            # muted mic as "deaf" → false mic_silent. (The real deaf cause — a hung afplay
            # holding the play-lock — is now bounded by AFPLAY_TIMEOUT_S in tts._afplay.)
            if processing.is_set() or tts.is_anything_playing():
                liveness.feed_muted()
                vstate["deaf"] = False           # intentionally muted, not deaf
                vstate["mic_quiet_s"] = 0.0
                state.write(**vstate)
                continue
            mx = level["max"]; level["max"] = 0.0
            log.debug("voice: audio level (max rms / %ds) = %.4f", MONITOR_S, mx)
            # Publish a DEAF heartbeat (B15): RAW-measured — the device delivering nothing,
            # never a quiet room post-filter. The supervisor's audio-liveness probe restarts
            # the loop past VOICE_DEAF_RESTART_S; reopening the stream recovers a wedged
            # CoreAudio handle.
            quiet_for = liveness.quiet_for_s
            vstate["deaf"] = quiet_for > SILENCE_ALERT_S
            vstate["mic_quiet_s"] = round(quiet_for, 1)
            state.write(**vstate)
            for ev in liveness.poll():
                if ev["event"] == "recovered":
                    # Episode DURATION is the diagnosis discriminator: seconds = playback/
                    # CoreAudio glitch; minutes = another app or the lock held the device.
                    log.warning("voice: mic RECOVERED after %.0fs of device zeros",
                                ev["deaf_for_s"])
                    failures.record(
                        "voice", "mic_recovered",
                        f"mic recovered after {ev['deaf_for_s']:.0f}s of device-level zeros "
                        "(episode evidence for the mic_silent diagnosis: seconds=transient "
                        "glitch, minutes=another app/lock held the input device).",
                    )
                    continue
                dev = "?"
                try:
                    import sounddevice as sd
                    dev = str(sd.query_devices(kind="input").get("name", "?"))
                except Exception:  # noqa: BLE001 — diagnostics must not kill the monitor
                    pass
                failures.record(
                    "voice", "mic_silent",
                    f"mic delivering DEVICE-LEVEL zeros for {ev['quiet_for_s']:.0f}s "
                    f"(max raw rms {ev['max_raw_rms']:.6f}, post-filter max {mx:.4f}, "
                    f"input device: {dev}) — raw-measured, so this is a real device "
                    "outage (TCC grant, device switch, or another process holding the "
                    "mic), not a quiet room.",
                )
                log.warning("voice: DEVICE zeros for %.0fs (raw-measured) — recorded mic_silent",
                            ev["quiet_for_s"])

    threading.Thread(target=_monitor, daemon=True).start()

    def _warmup() -> None:
        """Pre-load Silero/Moonshine/Piper so the first real turn is not cold-start."""
        try:
            silent = b"\x00" * vad.FRAME_BYTES
            vad.get_vad().prob(silent)
            stt.get_stt()
            # MLX Whisper compiles on its FIRST real transcribe (~10s) — that would
            # otherwise land on the first spoken turn. Transcribe 0.5s of silence
            # now to pay the compile up front (the no_speech gate returns "").
            _wp = _write_wav(b"\x00" * SAMPLE_RATE)
            try:
                stt.transcribe(_wp)
            finally:
                try:
                    os.remove(_wp)
                except OSError:
                    pass
            tts.get_tts()
            if wake_det is not None:
                wake_det.feed(silent)
        except Exception as exc:  # noqa: BLE001
            log.debug("voice warmup: %s", exc)
        # Warm the brain CLI too. The first spoken turn otherwise pays the cold
        # Claude-CLI start (Node + auth + on-disk caches, ~5-7s) BEFORE the first
        # word; a tiny throwaway turn warms it so the first real "ace" answers in
        # ~2s like the rest. Best-effort and offline-safe (never blocks the loop).
        try:
            from utah import brain

            brain.ask("hi", timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.debug("voice brain warmup: %s", exc)

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
                seg = vad.Segmenter(offset=vad_off, arm_grace=config.VAD_ARM_GRACE)
                detector.reset()
                if wake_det is not None:
                    wake_det.reset()
                armed_until = 0.0
                vstate.update(status="listening", listening=True, speaking=False)
                state.write(**vstate)
                try:
                    _dev = sd.query_devices(kind="input")
                    log.info("voice loop: input device = %r (%d ch @ %.0f Hz default)",
                             _dev.get("name"), _dev.get("max_input_channels"),
                             _dev.get("default_samplerate", 0))
                except Exception as _e:  # noqa: BLE001
                    log.info("voice loop: input device query failed: %s", _e)
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
                    seg_rms = _rms(pcm)
                    log.info("voice: speech segment %.1fs rms=%.4f → transcribing (audio_wake=%s)",
                             secs, seg_rms, segment_armed)
                    processing.set()
                    vstate.update(status="thinking", listening=False, speaking=False)
                    state.write(**vstate)
                    try:
                        path = _write_wav(pcm)
                        try:
                            text = stt.transcribe(path)
                            log.info("voice: transcript=%r", text)
                            if text:
                                vstate["last_transcript"] = text[:120]
                            if not text and not segment_armed:
                                return
                            if text and not _ECHO.allow(text):
                                log.info("voice: echo-dropped duplicate transcript")
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

                _collecting = False
                _collect_frames: list[bytes] = []
                _collect_deadline = 0.0
                while True:
                    frame = q.get()
                    if wake_det is not None:
                        for hit in wake_det.feed(frame):
                            log.info("voice: openWakeWord hit %s=%.2f",
                                     hit.keyword, hit.confidence)
                            _on_audio_wake()
                            if _FORCE_CAPTURE and not _collecting:
                                _collecting = True
                                _collect_frames = []
                                _collect_deadline = time.monotonic() + _FORCE_CAPTURE_S
                                log.info("voice: force-capture %.1fs (Whisper, VAD-bypass)",
                                         _FORCE_CAPTURE_S)

                    if _FORCE_CAPTURE:
                        # VAD-bypass path: collect a fixed window after the wake, then
                        # hand it straight to Whisper (robust to the noisy built-in mic).
                        if _collecting:
                            _collect_frames.append(frame)
                            if time.monotonic() >= _collect_deadline:
                                _collecting = False
                                armed_until = 0.0
                                _process_segment(b"".join(_collect_frames), segment_armed=True)
                        continue

                    # ── Silero two-stage path (force-capture disabled) ──
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


class EchoGate:
    """Drop an utterance IDENTICAL to the one just processed (TTL-bounded).

    2026-06-10: STT thrashing under machine load emitted the same transcript twice —
    two brain calls, two stored turns, doubled replies in Michael's chat (the
    'hallucination' he flagged). A human repeating themselves beyond the TTL still
    gets through; the echo inside it never does. Pure (unit-tested)."""

    def __init__(self, ttl_s: float = 20.0):
        self.ttl = ttl_s
        self.last: str | None = None
        self.at = 0.0

    def allow(self, text: str, now: float | None = None) -> bool:
        import time as _t

        now = _t.monotonic() if now is None else now
        t = (text or "").strip().lower()
        if not t:
            return True
        if t == self.last and (now - self.at) < self.ttl:
            return False
        self.last, self.at = t, now
        return True


_ECHO = EchoGate()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

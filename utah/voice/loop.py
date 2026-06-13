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
# Below this RAW RMS = the device is delivering zeros (deaf). A WEDGED/dead CoreAudio
# handle delivers EXACT 0.0; a live 16-bit mic — even in a silent room — never floors
# below its ~3e-5 LSB-dither floor. The old 0.0008 sat ABOVE the real quiet-room floor:
# overnight (00:40–07:11 on 2026-06-13) the room's raw peaks fell to 0.00004–0.00077,
# tripping false "deaf" → supervisor restart storm → wake word repeatedly dropped. This
# sits below the dither floor so only literal zeros trip it. Env-overridable for retune.
TRUE_SILENCE = float(os.environ.get("UTAH_TRUE_SILENCE", "0.00001"))
MIC_SILENT_COOLDOWN_S = 1800.0  # 30 min
# A built-in MacBook mic at a LOW macOS input volume is the root signal-level cause
# of the voice stack's troubles: it floors raw RMS below TRUE_SILENCE, starves
# openWakeWord's SNR (false-fires), and makes real commands transcribe EMPTY
# (2026-06-13: input volume sat at 48/100). The loop floors it on startup so a
# drifted-down level can't silently cripple voice. Env-tunable; floor 0 disables.
MIC_INPUT_FLOOR = int(os.environ.get("UTAH_MIC_INPUT_FLOOR", "80"))
MIC_INPUT_TARGET = int(os.environ.get("UTAH_MIC_INPUT_TARGET", "85"))

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
_FORCE_CAPTURE_S = float(os.environ.get("UTAH_VOICE_FORCE_CAPTURE_S", "5.5"))
# Pause after the wake hit before recording the command — without this the 4s window
# starts on "hey ace" itself and Whisper often sees only silence → transcript=''.
_FORCE_CAPTURE_DELAY_S = float(os.environ.get("UTAH_VOICE_FORCE_CAPTURE_DELAY", "0.5"))
#: Wake-storm guard: a wake hit only counts within this window of genuinely LOUD
#: audio. 2026-06-10: openWakeWord fired ~3×/s on near-silent frames (AGC-amplified
#: hiss, rms ≈0.001) — 174 hits/min, each arming a capture + a Whisper pass, and the
#: bare-wake ack turned the storm audible ("Yeah?" on loop). Real speech on this mic
#: runs rms ≥0.03; 0.012 sits safely between hiss and voice. Silence cannot wake Ace.
_WAKE_MIN_RMS = float(os.environ.get("UTAH_VOICE_WAKE_MIN_RMS", "0.012"))
_WAKE_LOUD_WINDOW_S = float(os.environ.get("UTAH_VOICE_WAKE_LOUD_WINDOW_S", "2.0"))
#: Empty-wake cooldown: after a wake produces no command (ambient/TV/echo false-fire that
#: clears the loud-audio guard but isn't a real "hey ace"+command), suppress re-arming for
#: this long so the model can't churn a 5.5s Whisper pass every few seconds (2026-06-13:
#: 1,080 empty-command wakes in one log). A real command never stamps it, so genuine use is
#: unaffected; set 0 to disable. Short enough that a real wake right after a false one waits
#: only briefly.
_EMPTY_WAKE_COOLDOWN_S = float(os.environ.get("UTAH_VOICE_EMPTY_WAKE_COOLDOWN", "12.0"))

# ── Barge-in ("no time to stop") ─────────────────────────────────────────────
# While Ace is SPEAKING, the mic callback normally drops every frame (the echo guard:
# Ace's replies say "Ace", so an open mic re-fires the wake word on his own voice). That
# guard also made interruption impossible. Barge-in re-opens that window safely: during
# playback we keep measuring the mic and, on SUSTAINED genuinely-loud speech (a run of
# frames above an elevated threshold set well above Ace's own echo-bleed level), we cut
# the voice INSTANTLY (tts.stop_speaking) and arm capture for the new utterance. The
# threshold is measured on the RAW (pre-AGC) HPF'd signal so the AGC can't inflate echo
# into a false barge. Real speech on the built-in mic runs rms ≥0.03; Ace's playback
# bleed measures lower at the mic, so 0.10 with a 5-frame (~160 ms) run sits clear of
# echo while a real interruption (the user leaning in to talk over him) trips at once.
# Disable with UTAH_VOICE_BARGE_AUTO=1 (legacy UTAH_VOICE_BARGE=1 also enables auto).
_BARGE_AUTO = (
    os.environ.get("UTAH_VOICE_BARGE_AUTO", os.environ.get("UTAH_VOICE_BARGE", "0")) == "1"
)
_BARGE_RMS = float(os.environ.get("UTAH_VOICE_BARGE_RMS", "0.10"))
_BARGE_MIN_FRAMES = int(os.environ.get("UTAH_VOICE_BARGE_MIN_FRAMES", "5"))  # ~160 ms @ 32 ms/frame
# After a barge cut, record a fixed window of the interrupting utterance and hand it
# straight to Whisper (same VAD-bypass path the wake uses on this noisy mic).
_BARGE_CAPTURE_S = float(os.environ.get("UTAH_VOICE_BARGE_CAPTURE_S", "5.0"))


def _on_playback_frame(rms: float, detector, stop_fn) -> bool:
    """Loop glue for one mic frame heard WHILE Ace is speaking. Feeds *rms* to the
    barge *detector*; on the frame that fires the barge, calls *stop_fn* (cut the voice)
    exactly once and returns True. Returns False otherwise (echo / quiet / already
    latched). Pure of audio I/O so it is unit-tested; the loop passes the real
    ``tts.stop_speaking`` as *stop_fn*."""
    if detector.feed(rms):
        try:
            stop_fn()
        except Exception as exc:  # noqa: BLE001 — a stop hiccup must not crash the loop
            log.warning("voice: barge stop failed: %s", exc)
        return True
    return False


def _rms(pcm: bytes) -> float:
    import numpy as np

    a = np.frombuffer(pcm, dtype="int16").astype("float32")
    return float((a * a).mean() ** 0.5) / 32768.0 if a.size else 0.0


class GatedAGC:
    """Gated automatic gain control over int16 PCM blocks (pure, unit-tested).

    Tracks a fast-attack/slow-release peak envelope and boosts ONLY blocks whose
    envelope sits above the noise gate, toward the target peak. Quiet speech
    becomes reliably detectable; silence/hiss is left ALONE — amplified hiss is
    what openWakeWord false-fired on (the 2026-06-10 wake storm, 174 hits/min).
    Already-loud audio passes through untouched (gain never exceeds 1 downward —
    no re-encode loss), and output is clipped into int16 so boosted peaks can
    never wrap.
    """

    def __init__(self, *, target: float = _AGC_TARGET, gate: float = _AGC_GATE,
                 max_gain: float = _AGC_MAX_GAIN) -> None:
        self._target = float(target)
        self._gate = float(gate)
        self._max_gain = float(max_gain)
        self._env = self._gate  # running peak envelope

    @property
    def envelope(self) -> float:
        """Current peak envelope (0-1) — exposed for diagnostics and tests."""
        return self._env

    def process(self, pcm: bytes) -> bytes:
        import numpy as np

        if len(pcm) % 2:           # torn frame — drop the dangling byte, never raise
            pcm = pcm[:-1]
        a = np.frombuffer(pcm, dtype="int16").astype("float32") / 32768.0
        if a.size == 0:
            return pcm
        peak = float(np.abs(a).max())
        # fast attack (jump up to a louder peak), slow release (decay gently)
        self._env = peak if peak > self._env else self._env * 0.92 + peak * 0.08
        if self._env < self._gate:         # silence/noise floor — do not amplify
            return pcm
        gain = min(self._target / self._env, self._max_gain)
        if gain <= 1.0:                    # already at/above target — leave as-is
            return pcm
        out = np.clip(a * gain, -1.0, 1.0)
        return (out * 32767.0).astype("int16").tobytes()


class HighPass:
    """Stateful 2nd-order Butterworth high-pass over int16 PCM blocks.

    Strips the sub-``hz`` desk/chassis rumble (measured: 51% of capture energy
    below 300 Hz on the built-in mic) that masks speech from Silero, while
    preserving the voice band. Filter state carries across blocks so chunked
    processing has no per-frame transients (clicks) at block seams.

    Engine: scipy's ``lfilter`` when available (the live venv); otherwise an
    equivalent pure-numpy biquad (RBJ bilinear transform, Q=1/√2 ≡ 2nd-order
    Butterworth). The old implementation silently became a PASSTHROUGH when
    scipy was missing — rumble then masked speech with no signal anything was
    wrong; the fallback keeps the filter real everywhere.
    """

    def __init__(self, hz: float = _HPF_HZ, sample_rate: int = SAMPLE_RATE, *,
                 use_scipy: bool | None = None) -> None:
        import math

        self._scipy: list | None = None   # [b, a, zi] when the scipy engine is active
        self._z1 = 0.0                    # biquad state (numpy engine)
        self._z2 = 0.0
        # RBJ-cookbook high-pass biquad, Q = 1/sqrt(2) -> Butterworth response.
        w0 = 2.0 * math.pi * float(hz) / float(sample_rate)
        cosw, sinw = math.cos(w0), math.sin(w0)
        alpha = sinw / (2.0 * (1.0 / math.sqrt(2.0)))
        a0 = 1.0 + alpha
        self._b0 = (1.0 + cosw) / 2.0 / a0
        self._b1 = -(1.0 + cosw) / a0
        self._b2 = self._b0
        self._a1 = (-2.0 * cosw) / a0
        self._a2 = (1.0 - alpha) / a0
        if use_scipy is not False:
            try:
                from scipy.signal import butter, lfilter_zi

                b, a = butter(2, float(hz) / (sample_rate / 2), btype="high")
                self._scipy = [b, a, lfilter_zi(b, a).astype("float64")]
            except Exception as exc:  # noqa: BLE001 — numpy biquad covers the gap
                log.debug("voice loop: scipy HPF unavailable (%s) — numpy biquad engine", exc)

    def process(self, pcm: bytes) -> bytes:
        import numpy as np

        if len(pcm) % 2:           # torn frame — drop the dangling byte, never raise
            pcm = pcm[:-1]
        x = np.frombuffer(pcm, dtype="int16").astype("float64")
        if x.size == 0:
            return pcm
        if self._scipy is not None:
            from scipy.signal import lfilter

            b, a, zi = self._scipy
            y, self._scipy[2] = lfilter(b, a, x, zi=zi)
        else:
            y = np.empty_like(x)
            b0, b1, b2, a1, a2 = self._b0, self._b1, self._b2, self._a1, self._a2
            z1, z2 = self._z1, self._z2
            for i in range(x.size):       # direct form II transposed
                xi = x[i]
                yi = b0 * xi + z1
                z1 = b1 * xi - a1 * yi + z2
                z2 = b2 * xi - a2 * yi
                y[i] = yi
            self._z1, self._z2 = z1, z2
        return np.clip(y, -32768, 32767).astype("int16").tobytes()


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
    """Only transcribe after an openWakeWord hit (ace) — no transcript-only fallback."""
    if not audio_wake_ok:
        return False
    return time.monotonic() < armed_until


def _empty_wake_cooling(now: float, last_empty_at: float, cooldown: float) -> bool:
    """True if a recent wake produced NO command and we're still inside the cooldown —
    used to suppress re-arming so an ambient false-wake can't churn Whisper every few
    seconds (2026-06-13: openWakeWord misfired ~10x/min on room/TV/echo speech at conf up
    to 0.99 — ABOVE the loud-audio storm guard — yielding 1,080 empty-command wakes, each
    a 5.5s Whisper pass + agent call). ``cooldown<=0`` disables it; a wake that yields a
    REAL command never stamps ``last_empty_at``, so genuine 'hey ace' use is unaffected."""
    return cooldown > 0 and last_empty_at > 0 and (now - last_empty_at) < cooldown


def _needs_input_bump(current: int, floor: int) -> bool:
    """True when the macOS mic input volume is below the floor and the floor is
    active. A failed read (current < 0) or a disabled floor (<= 0) never acts."""
    return floor > 0 and 0 <= current < floor


def _ensure_input_volume(floor: int | None = None, target: int | None = None) -> None:
    """Floor the macOS mic input volume on startup so a low/drifted level can't
    silently cripple voice (empty transcripts, openWakeWord false-fires). Best-effort
    via osascript — any failure is logged at debug and ignored; voice never depends
    on it succeeding."""
    import subprocess
    floor = MIC_INPUT_FLOOR if floor is None else floor
    target = MIC_INPUT_TARGET if target is None else target
    if floor <= 0:
        return
    try:
        out = subprocess.run(["osascript", "-e", "input volume of (get volume settings)"],
                             capture_output=True, text=True, timeout=5)
        cur = int((out.stdout or "").strip() or "-1")
    except Exception as exc:  # noqa: BLE001 — diagnostics must never block voice startup
        log.debug("voice: input-volume read failed: %s", exc)
        return
    if _needs_input_bump(cur, floor):
        try:
            subprocess.run(["osascript", "-e", f"set volume input volume {target}"],
                           capture_output=True, text=True, timeout=5)
            log.info("voice: raised mic input volume %d → %d (was below floor %d)",
                     cur, target, floor)
        except Exception as exc:  # noqa: BLE001
            log.debug("voice: input-volume set failed: %s", exc)


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

    _ensure_input_volume()   # floor the mic input level before arming (signal-level fix)
    wake_det = oww.get_oww()
    audio_wake_ok = wake_det is not None
    if audio_wake_ok:
        log.info("voice loop: two-stage wake ON (openWakeWord → STT → brain)")
    else:
        log.info("voice loop: two-stage wake OFF — transcript-only fallback")

    q: "queue.Queue[bytes]" = queue.Queue()
    processing = threading.Event()
    # Monotonic ts of the last wake that produced NO command — drives the empty-wake
    # cooldown (see _empty_wake_cooling). 0.0 = none yet.
    _last_empty_wake_at = 0.0
    # Barge-in: detector + event, shared between the mic callback (which detects the
    # interruption during playback) and the main loop (which captures the new utterance).
    # The detector latches after firing and is reset before each playback (see arming).
    from utah.voice.barge import BargeDetector
    from utah.voice.barge_control import consume_button_barge
    _barge_det = BargeDetector(rms_threshold=_BARGE_RMS, min_frames=_BARGE_MIN_FRAMES)
    barge_evt = threading.Event()
    level = {"max": 0.0}
    # Device liveness, measured on the RAW (pre-DSP) signal — the HPF strips the
    # sub-120Hz floor a quiet room still has, so post-filter "zeros" cannot tell a
    # healthy-but-quiet device from a dead one (2026-06-09 investigation).
    liveness = MicLiveness(threshold=TRUE_SILENCE, alert_after_s=SILENCE_ALERT_S,
                           cooldown_s=MIC_SILENT_COOLDOWN_S)
    vstate = {"status": "starting", "listening": False, "speaking": False,
              "segments": 0, "last_transcript": None, "last_wake": None}
    armed_until = 0.0
    agc = GatedAGC() if _AGC_ON else None
    hpf = HighPass() if _HPF_ON else None
    if hpf is not None:
        log.info("voice loop: high-pass filter ON (%.0f Hz) — strips desk/rumble noise", _HPF_HZ)

    def _cb(indata, frames, t, status):  # noqa: ANN001
        raw = bytes(indata)
        # ── Barge-in window: Ace is SPEAKING right now ──────────────────────
        # Keep LISTENING through playback so Michael can talk over Ace ("no time to
        # stop"). The mic is otherwise muted while Ace speaks (echo guard). We measure
        # the cleaned RAW signal (HPF, but NOT AGC — AGC would amplify Ace's own echo
        # into a false barge) and fire only on sustained, genuinely-loud speech. On a
        # barge: cut the voice instantly and flag the loop to capture the interruption.
        if _BARGE_AUTO and tts.is_anything_playing():
            liveness.feed_muted()   # intentionally muted — not deaf
            bpcm = hpf.process(raw) if hpf is not None else raw
            if _on_playback_frame(_rms(bpcm), _barge_det, tts.stop_speaking):
                barge_evt.set()
                log.info("voice: BARGE-IN — user spoke over Ace; cutting voice + capturing")
            return
        if processing.is_set() or tts.is_anything_playing():
            liveness.feed_muted()   # intentionally muted — not deaf
            return
        liveness.feed_frame(_rms(raw))   # liveness = RAW-signal question
        pcm = raw
        if hpf is not None:
            pcm = hpf.process(pcm)  # strip sub-120Hz desk/rumble FIRST
        if agc is not None:
            pcm = agc.process(pcm)  # then boost the cleaned speech
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
                wake_conf = 1.0          # peak oww confidence of the live arm window
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
                    # Playback is starting: re-arm barge-in for THIS turn (clear the
                    # latch + any stale event + any half-run left from before) so the
                    # very next loud frame from Michael can cut Ace off cleanly.
                    _barge_det.reset()
                    barge_evt.clear()
                    vstate.update(status="speaking", speaking=True, listening=False)
                    state.write(**vstate)

                def _on_audio_wake(confidence: float = 1.0) -> None:
                    nonlocal armed_until, wake_conf
                    armed_until = time.monotonic() + config.WAKE_ARM_S
                    wake_conf = confidence          # peak confidence that armed this window
                    vstate["last_wake"] = "ace"
                    agent.pulse_wake("")
                    seg.arm()
                    log.info("voice: audio wake armed (%.0fs window, conf=%.2f)",
                             config.WAKE_ARM_S, confidence)

                def _process_segment(pcm: bytes, *, segment_armed: bool,
                                     button_barge: bool = False) -> None:
                    nonlocal _last_empty_wake_at
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
                            if not text and segment_armed and seg_rms < config.VOICE_SILENCE_RMS:
                                log.info(
                                    "voice: empty quiet segment after wake (rms=%.4f) — skip",
                                    seg_rms,
                                )
                                _last_empty_wake_at = time.monotonic()   # arm empty-wake cooldown
                                return
                            if text and not _ECHO.allow(text):
                                log.info("voice: echo-dropped duplicate transcript")
                                return
                            result = agent.handle_utterance(
                                text or "",
                                audio_wake=segment_armed,
                                wake_confidence=(wake_conf if segment_armed else None),
                                button_barge=button_barge,
                                on_speaking=_on_speaking,
                            )
                            cmd = result.get("command") if result else None
                            log.info("voice: wake_fired=%s command=%r",
                                     result is not None, cmd)
                            if segment_armed and not cmd:
                                # Wake fired but produced no command — an ambient/echo
                                # false-fire. Arm the cooldown so it can't re-churn at once.
                                _last_empty_wake_at = time.monotonic()
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
                _collect_start = 0.0
                _collect_deadline = 0.0
                _last_loud_at = 0.0
                _button_barge_arm = False
                if not _BARGE_AUTO:
                    log.info("voice loop: auto barge OFF — use deck BARGE button to interrupt")
                while True:
                    frame = q.get()
                    if consume_button_barge():
                        barge_evt.set()
                        _button_barge_arm = True
                        log.info("voice: button barge — stop acknowledged, arming capture")
                    if _rms(frame) >= _WAKE_MIN_RMS:
                        _last_loud_at = time.monotonic()
                    # ── Barge-in: the mic callback cut Ace's voice because Michael talked
                    # over him. He is ALREADY speaking — start capturing the interruption
                    # NOW (no wake word, no post-wake pause) and answer it. This is the
                    # "no time to stop" path: interrupt freely, like a real conversation.
                    if barge_evt.is_set():
                        barge_evt.clear()
                        _on_audio_wake(1.0)            # treat as an armed turn (orb pulse + arm)
                        if _FORCE_CAPTURE:
                            _collecting = True
                            _collect_frames = []
                            _collect_start = time.monotonic()   # no delay: he is mid-word
                            _collect_deadline = _collect_start + _BARGE_CAPTURE_S
                            log.info("voice: barge capture %.1fs (Whisper, VAD-bypass)",
                                     _BARGE_CAPTURE_S)
                        elif _button_barge_arm:
                            log.info("voice: button barge — listening (say ace or speak command)")
                    if wake_det is not None:
                        for hit in wake_det.feed(frame):
                            # Storm guard: no genuinely loud audio recently = the model
                            # fired on hiss/echo, not a person. Ignore without arming.
                            if time.monotonic() - _last_loud_at > _WAKE_LOUD_WINDOW_S:
                                log.info("voice: oww hit %.2f ignored — no loud audio "
                                         "in %.1fs (storm guard)",
                                         hit.confidence, _WAKE_LOUD_WINDOW_S)
                                continue
                            # Empty-wake cooldown: a recent wake produced no command
                            # (ambient/TV/echo false-fire). Don't re-arm/re-transcribe yet.
                            if _empty_wake_cooling(time.monotonic(), _last_empty_wake_at,
                                                   _EMPTY_WAKE_COOLDOWN_S):
                                log.info("voice: oww hit %.2f ignored — empty-wake cooldown "
                                         "(%.0fs)", hit.confidence, _EMPTY_WAKE_COOLDOWN_S)
                                continue
                            log.info("voice: openWakeWord hit %s=%.2f",
                                     hit.keyword, hit.confidence)
                            _on_audio_wake(hit.confidence)
                            if _FORCE_CAPTURE and not _collecting:
                                _collecting = True
                                _collect_frames = []
                                _collect_start = time.monotonic() + _FORCE_CAPTURE_DELAY_S
                                _collect_deadline = _collect_start + _FORCE_CAPTURE_S
                                log.info(
                                    "voice: force-capture %.1fs after %.1fs pause (Whisper, VAD-bypass)",
                                    _FORCE_CAPTURE_S,
                                    _FORCE_CAPTURE_DELAY_S,
                                )

                    if _FORCE_CAPTURE:
                        # VAD-bypass path: collect a fixed window after the wake, then
                        # hand it straight to Whisper (robust to the noisy built-in mic).
                        if _collecting:
                            if time.monotonic() >= _collect_start:
                                _collect_frames.append(frame)
                            if time.monotonic() >= _collect_deadline:
                                _collecting = False
                                armed_until = 0.0
                                _barge = _button_barge_arm
                                _process_segment(
                                    b"".join(_collect_frames),
                                    segment_armed=True,
                                    button_barge=_barge,
                                )
                                if _barge:
                                    _button_barge_arm = False
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
                    _barge = _button_barge_arm
                    armed_until = 0.0  # one segment per arm window
                    _process_segment(pcm, segment_armed=segment_armed, button_barge=_barge)
                    if _barge:
                        _button_barge_arm = False
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

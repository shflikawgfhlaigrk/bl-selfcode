"""Voice loop helpers. Speech segmentation moved to Silero VAD + the pure
``utah.voice.vad.Segmenter`` (see test_voice_vad); what remains loop-local is the
PCM->WAV writer (what STT consumes) and the RMS meter (deaf-mic detection)."""
from __future__ import annotations

import importlib.util
import wave
from pathlib import Path

import numpy as np

from utah.voice import loop, vad


def test_empty_wake_cooldown_suppresses_rearm_then_clears():
    """2026-06-13 storm: openWakeWord misfired ~10x/min on ambient/TV/echo speech at conf
    up to 0.99 (above the loud-audio storm guard), each arming a 5.5s Whisper pass that
    produced an EMPTY command — 1,080 empty wakes churning. After an empty wake, re-arming
    is suppressed for the cooldown; a wake that yields a REAL command never stamps, so
    genuine 'hey ace' + command is unaffected."""
    cd = 12.0
    assert loop._empty_wake_cooling(now=100.0, last_empty_at=95.0, cooldown=cd) is True   # within
    assert loop._empty_wake_cooling(now=200.0, last_empty_at=95.0, cooldown=cd) is False  # expired
    assert loop._empty_wake_cooling(now=100.0, last_empty_at=0.0, cooldown=cd) is False   # no empty yet
    assert loop._empty_wake_cooling(now=95.5, last_empty_at=95.0, cooldown=0.0) is False  # disabled


def test_input_volume_floor_bumps_only_when_below():
    """A low macOS input volume (2026-06-13: 48/100) is the root signal-level cause
    of the voice stack's troubles — sub-TRUE_SILENCE floor, openWakeWord false-fires,
    empty transcripts. The loop floors it on startup; the decision raises ONLY when
    below the floor, leaves an already-loud level alone, treats a failed read (-1) as
    no-op, and is disabled by floor=0."""
    assert loop._needs_input_bump(48, 80) is True       # the real case
    assert loop._needs_input_bump(85, 80) is False      # already above floor
    assert loop._needs_input_bump(80, 80) is False      # at floor — fine
    assert loop._needs_input_bump(-1, 80) is False      # read failed — never act on garbage
    assert loop._needs_input_bump(10, 0) is False       # floor=0 disables the feature


def test_write_wav_roundtrips_pcm_at_16k_mono():
    pcm = (np.ones(vad.FRAME * 4, dtype="int16") * 5000).tobytes()
    path = loop._write_wav(pcm)
    try:
        with wave.open(path, "rb") as wf:
            assert wf.getframerate() == loop.SAMPLE_RATE   # 16 kHz (what Moonshine expects)
            assert wf.getsampwidth() == 2                  # 16-bit
            assert wf.getnchannels() == 1                  # mono
            assert wf.getnframes() == vad.FRAME * 4
    finally:
        import os
        os.remove(path)


def test_rms_zero_for_silence_positive_for_signal():
    assert loop._rms(np.zeros(512, dtype="int16").tobytes()) == 0.0
    assert loop._rms((np.ones(512, dtype="int16") * 8000).tobytes()) > 0.2


def test_frame_size_is_silero_native():
    # the mic blocksize must equal Silero's required 32ms window
    assert loop.FRAME == vad.FRAME == 512
    assert loop.SAMPLE_RATE == 16_000


def test_should_reopen_stream_decision_locks_the_restart_storm_fix():
    """2026-06-14 mic-zeros restart-storm: a wedged CoreAudio handle never raises — it
    silently delivers zero-filled buffers (another app grabbing input / App-Nap
    starvation / device switch). The OLD behavior nuked the whole voice process,
    orphaning the adhoc cdhash TCC grant on rebuild. The fix reopens the stream
    IN-PROCESS. This decision is the lock: reopen ONLY when raw zeros have run longer
    than the threshold AND in-process attempts remain; otherwise fall through to the
    supervisor's heavier kill+respawn, or (when disabled) restart-only behavior."""
    # the live default: 8s zeros threshold, up to 3 in-process attempts
    assert loop._should_reopen_stream(quiet_for_s=9.0, reopen_after_s=8.0,
                                      reopen_count=0, max_reopens=3) is True   # wedged, retries left
    assert loop._should_reopen_stream(quiet_for_s=2.0, reopen_after_s=8.0,
                                      reopen_count=0, max_reopens=3) is False  # brief quiet — not a wedge
    assert loop._should_reopen_stream(quiet_for_s=9.0, reopen_after_s=8.0,
                                      reopen_count=3, max_reopens=3) is False  # exhausted → let supervisor act
    assert loop._should_reopen_stream(quiet_for_s=9.0, reopen_after_s=8.0,
                                      reopen_count=4, max_reopens=3) is False  # over the cap, never loop forever
    assert loop._should_reopen_stream(quiet_for_s=999.0, reopen_after_s=0.0,
                                      reopen_count=0, max_reopens=3) is False  # disabled → restart-only fallback
    assert loop._should_reopen_stream(quiet_for_s=999.0, reopen_after_s=-1.0,
                                      reopen_count=0, max_reopens=3) is False  # negative window also disables
    # boundary: exactly at the threshold is NOT yet a wedge (strictly greater)
    assert loop._should_reopen_stream(quiet_for_s=8.0, reopen_after_s=8.0,
                                      reopen_count=0, max_reopens=3) is False


def test_app_nap_optout_targets_the_voice_bundle_defaults_domain(monkeypatch):
    """App Nap throttling the backgrounded (LSUIElement) voice app starves the CoreAudio
    callback into delivering zeros — a cause of the mic_silent/restart-storm class. The
    opt-out writes NSAppSleepDisabled into the SIGNED BUNDLE's user-defaults domain rather
    than rebuilding the bundle, because the adhoc cdhash-only designated requirement means
    a rebuild orphans the mic TCC grant (deaf under launchd). Lock: the write targets the
    voice bundle id with the App-Nap key, and a non-zero exit returns False (never crashes
    startup)."""
    import subprocess

    calls = {}

    class _OK:
        returncode = 0

    def _fake_run(argv, **kw):
        calls["argv"] = argv
        return _OK()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert loop._set_app_nap_default() is True
    # zero-TCC-risk path: it must address the voice bundle's defaults domain, not rebuild it
    assert calls["argv"][0] == "defaults" and calls["argv"][1] == "write"
    assert calls["argv"][2] == loop._VOICE_BUNDLE_ID == "com.utah.voice"
    assert "NSAppSleepDisabled" in calls["argv"]

    class _Fail:
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _Fail())
    assert loop._set_app_nap_default() is False       # bad write → honest False

    def _boom(argv, **kw):
        raise OSError("defaults missing")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert loop._set_app_nap_default() is False       # never propagates an exception into startup


# ---------------------------------------------------------------------------
# LIVE IMPORT-PATH LOCK
# ---------------------------------------------------------------------------
# The "no reload needed / wake word can't silently regress" guarantee rests on a
# single fact: the live UtahVoice.app is spawned as `<bundle-exec> -m utah.voice.loop`,
# and `-m` resolves the module by import — i.e. it runs THIS repo's loop.py, never a
# stale baked-in copy. The signed bundle wrapper only swaps the interpreter (to carry
# the mic TCC grant); it does NOT vendor the source. So a source edit here IS the live
# behavior on next process start. These tests pin that contract so a refactor that
# vendored loop.py into the bundle, or changed the spawn argv, fails CI loudly.

def test_loop_module_resolves_to_this_repo_not_a_vendored_copy():
    """`-m utah.voice.loop` imports the module the interpreter's import machinery
    finds. Pin that the resolved file is the in-repo source under utah/voice/, so the
    live bundle (which launches with `-m utah.voice.loop`) runs edits made here."""
    spec = importlib.util.find_spec("utah.voice.loop")
    assert spec is not None and spec.origin is not None
    resolved = Path(spec.origin).resolve()
    # the imported module object and the spec must agree, and both must be the repo file
    assert Path(loop.__file__).resolve() == resolved
    repo_loop = (Path(__file__).resolve().parents[1] / "utah" / "voice" / "loop.py").resolve()
    assert resolved == repo_loop, f"loop.py imported from {resolved}, not the repo {repo_loop}"


def test_live_loop_is_the_wake_word_superset_not_a_pre_fix_stub():
    """Regression-lock the 1044-line superset: the live loop must carry the two-stage
    wake docstring AND all five hardened restart-storm/false-wake fixes by NAME, so a
    branch checkout of a pre-fix loop.py (the 2026-06-17 regression: false-wakes, empty
    commands, 22 restarts/day) can never silently become the imported module again."""
    src = Path(loop.__file__).resolve().read_text()
    # the permanent two-stage wake design (Stage A openWakeWord arms, Stage B STT gate)
    assert "Two-stage wake (permanent)" in src
    assert "openWakeWord" in src and "Silero VAD" in src
    # the five fixes that were lost on the pre-fix branch — each must be present by its
    # public surface, not just by comment:
    for symbol in (
        "_empty_wake_cooling",        # empty-wake cooldown (false-fire storm)
        "_needs_input_bump",          # input-volume floor (sub-silence signal level)
        "_should_reopen_stream",      # in-process stream reopen (mic-zeros wedge)
        "_set_app_nap_default",       # App-Nap opt-out (callback starvation)
        "MicLiveness",                # deaf-but-alive liveness self-heal
    ):
        assert symbol in src, f"live loop.py is missing the {symbol} fix — pre-fix stub?"


def test_supervisor_voice_spec_launches_via_dash_m_module_import():
    """The supervisor is the only spawner of the voice child. Pin that it launches with
    `-m utah.voice.loop` (module import, resolves THIS repo) — whether via the signed
    bundle exec or the sys.executable fallback. If a refactor pointed it at a vendored
    script path instead, the no-reload/can't-regress guarantee would silently break."""
    from utah.daemon import supervisor

    spec = supervisor._voice_spec()
    assert spec.name == "voice"
    argv = list(spec.argv)
    # `-m utah.voice.loop` must be the tail of argv (argv[0] is the interpreter/bundle exec)
    assert argv[-2:] == ["-m", "utah.voice.loop"], f"voice argv is {argv} — not a -m import launch"
    # and never a bare file path to a (potentially stale/vendored) loop.py
    assert not any(str(a).endswith("loop.py") for a in argv), "voice launched from a file path, not -m import"

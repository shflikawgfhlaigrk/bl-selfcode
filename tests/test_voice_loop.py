"""Voice loop helpers. Speech segmentation moved to Silero VAD + the pure
``utah.voice.vad.Segmenter`` (see test_voice_vad); what remains loop-local is the
PCM->WAV writer (what STT consumes) and the RMS meter (deaf-mic detection)."""
from __future__ import annotations

import wave

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

"""The mic-cleanup DSP — gated AGC and the 120 Hz high-pass — as module-level,
injectable units (previously closures buried in run(), untestable).

GatedAGC: quiet speech is boosted toward the target peak, silence below the gate
is left ALONE (amplified hiss is what fed the 2026-06-10 wake storm), gain is
capped, and output can never clip. HighPass: strips the sub-120 Hz desk rumble
that masks speech from Silero while passing the voice band — stateful across
blocks so chunked processing has no seams."""
from __future__ import annotations

import numpy as np
import pytest

from utah.voice import loop


def _tone(freq_hz: float, seconds: float = 0.5, amp: float = 0.5,
          rate: int = loop.SAMPLE_RATE) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * freq_hz * t) * amp * 32767).astype("int16").tobytes()


def _rms(pcm: bytes) -> float:
    return loop._rms(pcm)


# ── GatedAGC ─────────────────────────────────────────────────────────────────

def test_agc_boosts_quiet_speech_toward_target():
    agc = loop.GatedAGC(target=0.30, gate=0.025, max_gain=12.0)
    quiet = _tone(440, amp=0.05)           # quiet speech, above the gate
    out = agc.process(quiet)
    assert _rms(out) > _rms(quiet) * 2     # genuinely boosted
    peak = np.abs(np.frombuffer(out, dtype="int16")).max() / 32768.0
    assert peak <= 0.35                    # near the target, not slammed to full scale


def test_agc_leaves_silence_alone():
    """Below the noise gate NOTHING is amplified — boosted hiss is what the wake
    model false-fired on (174 hits/min storm)."""
    agc = loop.GatedAGC(target=0.30, gate=0.025, max_gain=12.0)
    hiss = (np.random.default_rng(7).normal(0, 0.002, 4096) * 32767).astype("int16").tobytes()
    assert agc.process(hiss) == hiss


def test_agc_gain_is_capped():
    agc = loop.GatedAGC(target=0.90, gate=0.01, max_gain=3.0)
    quiet = _tone(440, amp=0.05)
    out = agc.process(quiet)
    assert _rms(out) <= _rms(quiet) * 3.05   # max_gain bound holds (small numeric slack)


def test_agc_does_not_touch_already_loud_audio():
    agc = loop.GatedAGC(target=0.30, gate=0.025, max_gain=12.0)
    loud = _tone(440, amp=0.8)
    assert agc.process(loud) == loud         # gain <= 1 → passthrough, no re-encode loss


def test_agc_never_clips():
    agc = loop.GatedAGC(target=1.0, gate=0.001, max_gain=50.0)
    sig = _tone(300, amp=0.4)
    out = np.frombuffer(agc.process(sig), dtype="int16")
    assert out.max() <= 32767 and out.min() >= -32768


def test_agc_empty_block_passthrough():
    agc = loop.GatedAGC()
    assert agc.process(b"") == b""


def test_agc_envelope_attacks_fast_and_releases_slow():
    """A loud burst raises the envelope INSTANTLY (so the burst itself is not
    over-boosted); after the burst the envelope decays gradually, not in one step."""
    agc = loop.GatedAGC(target=0.30, gate=0.01, max_gain=12.0)
    agc.process(_tone(440, seconds=0.05, amp=0.6))
    env_after_burst = agc.envelope
    assert env_after_burst == pytest.approx(0.6, rel=0.05)      # fast attack
    agc.process(_tone(440, seconds=0.032, amp=0.02))             # one quiet block
    assert 0.02 < agc.envelope < env_after_burst                 # slow release


# ── HighPass ─────────────────────────────────────────────────────────────────

def test_hpf_strips_rumble_and_passes_voice():
    hpf = loop.HighPass(hz=120.0, use_scipy=False)
    rumble = _tone(40, amp=0.5)             # desk/chassis rumble band
    voice = _tone(1000, amp=0.5)            # speech band
    assert _rms(hpf.process(rumble)) < _rms(rumble) * 0.12      # ≥ ~19 dB down
    hpf2 = loop.HighPass(hz=120.0, use_scipy=False)
    assert _rms(hpf2.process(voice)) > _rms(voice) * 0.9        # voice band preserved


def test_hpf_removes_dc_offset():
    hpf = loop.HighPass(hz=120.0, use_scipy=False)
    dc = (np.full(8192, 0.3) * 32767).astype("int16").tobytes()
    out = np.frombuffer(hpf.process(dc), dtype="int16").astype("float64")
    # steady-state (after the filter settles) must sit at ~zero
    assert abs(out[4096:].mean()) < 200


def test_hpf_is_stateful_across_blocks():
    """Filtering in 512-sample mic frames must equal filtering the whole signal —
    carried state means no per-frame transients (clicks) at block seams."""
    sig = _tone(700, seconds=0.2, amp=0.3)
    whole = loop.HighPass(hz=120.0, use_scipy=False).process(sig)
    chunked = loop.HighPass(hz=120.0, use_scipy=False)
    frames = [sig[i:i + loop.FRAME * 2] for i in range(0, len(sig), loop.FRAME * 2)]
    joined = b"".join(chunked.process(f) for f in frames)
    a = np.frombuffer(whole, dtype="int16").astype("float64")
    b = np.frombuffer(joined, dtype="int16").astype("float64")
    assert np.abs(a - b).max() <= 1.0       # identical up to int16 rounding


def test_hpf_empty_block_passthrough():
    hpf = loop.HighPass(use_scipy=False)
    assert hpf.process(b"") == b""


def test_hpf_scipy_auto_falls_back_when_missing():
    """use_scipy=None auto-detects; with scipy absent the numpy biquad still
    filters (the old behavior — silent passthrough — left rumble masking speech)."""
    hpf = loop.HighPass(hz=120.0)            # auto: scipy may or may not be present
    rumble = _tone(40, amp=0.5)
    assert _rms(hpf.process(rumble)) < _rms(rumble) * 0.12

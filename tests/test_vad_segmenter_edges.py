"""VAD edges the main suite doesn't pin: preroll CONTENT (the first phoneme must be
in the emitted bytes, not just 'a segment exists'), mid-capture reset, the armed
fast path (command starts instantly after the wake), and the pure frame-normalizer
that feeds Silero (pad/trim/scale — torch not required to prove it)."""
from __future__ import annotations

import numpy as np

from utah.voice import vad


def _pcm(value: int) -> bytes:
    return (np.ones(vad.FRAME, dtype="int16") * value).tobytes()


def test_preroll_bytes_lead_the_emitted_segment():
    """The preroll isn't a counter — it's the actual first-phoneme audio. The emitted
    segment must BEGIN with the pre-onset frames, in order."""
    seg = vad.Segmenter(onset=2, offset=3, preroll=3)
    a, b, c = _pcm(111), _pcm(222), _pcm(333)
    seg.feed(a, False)                       # quiet room, rolls into preroll
    seg.feed(b, True)                        # speech frame 1 (also prerolled)
    seg.feed(c, True)                        # speech frame 2 → onset → capture starts
    out = None
    for _ in range(3):
        out = seg.feed(_pcm(0), False) or out
    assert out is not None
    assert out[: 3 * vad.FRAME_BYTES] == a + b + c   # preroll content, byte-exact


def test_reset_mid_capture_drops_the_buffer_and_recovers():
    seg = vad.Segmenter(onset=2, offset=3, preroll=2)
    for _ in range(5):
        seg.feed(_pcm(500), True)            # capturing now
    seg.reset()                              # e.g. loop drops frames during TTS
    assert all(seg.feed(_pcm(0), False) is None for _ in range(10))  # nothing leaks
    # a fresh turn still works after the reset
    out = None
    for _ in range(4):
        out = seg.feed(_pcm(700), True) or out
    for _ in range(3):
        out = seg.feed(_pcm(0), False) or out
    assert out is not None
    a = np.frombuffer(out, dtype="int16")
    assert int(np.abs(a).max()) == 700       # only the NEW turn's audio, no stale tail


def test_armed_command_with_no_gap_captures_immediately():
    """openWakeWord can fire while the command is already flowing ("ace what's…"
    with no pause). Armed capture must onset right away, preroll included."""
    seg = vad.Segmenter(onset=2, offset=3, preroll=2, arm_grace=63)
    seg.arm()
    out = None
    for _ in range(6):
        out = seg.feed(_pcm(900), True) or out
    for _ in range(3):
        out = seg.feed(_pcm(0), False) or out
    assert out is not None
    a = np.frombuffer(out, dtype="int16")
    assert int(np.abs(a).max()) == 900


def test_armed_stand_down_then_late_speech_still_captured():
    """After arm_grace elapses with no command, the segmenter stands down to plain
    onset detection — late speech must STILL produce a segment (never a dead mic)."""
    seg = vad.Segmenter(onset=2, offset=3, preroll=2, arm_grace=5)
    seg.arm()
    assert all(seg.feed(_pcm(0), False) is None for _ in range(8))  # grace expires
    out = None
    for _ in range(5):
        out = seg.feed(_pcm(800), True) or out
    for _ in range(3):
        out = seg.feed(_pcm(0), False) or out
    assert out is not None


def test_max_frames_cap_is_a_hard_byte_bound():
    seg = vad.Segmenter(onset=1, offset=99, max_frames=4, preroll=1)
    outs = [seg.feed(_pcm(100), True) for _ in range(20)]
    segs = [s for s in outs if s is not None]
    assert segs and all(len(s) <= 4 * vad.FRAME_BYTES for s in segs)


# ── pure frame normalizer (what SileroVAD feeds the model) ──────────────────

def test_frame_f32_scales_int16_into_unit_range():
    pcm = (np.ones(vad.FRAME, dtype="int16") * 16384).tobytes()
    a = vad._frame_f32(pcm)
    assert a.dtype == np.float32 and a.shape == (vad.FRAME,)
    assert abs(float(a[0]) - 0.5) < 1e-3


def test_frame_f32_pads_short_frames():
    short = np.ones(100, dtype="int16").tobytes()
    a = vad._frame_f32(short)
    assert a.shape == (vad.FRAME,)
    assert float(a[vad.FRAME - 1]) == 0.0            # zero-padded tail


def test_frame_f32_trims_long_frames():
    long = np.ones(vad.FRAME * 2, dtype="int16").tobytes()
    a = vad._frame_f32(long)
    assert a.shape == (vad.FRAME,)


def test_speech_threshold_is_env_tunable(monkeypatch):
    import importlib

    monkeypatch.setenv("UTAH_VAD_SPEECH_THRESHOLD", "0.71")
    importlib.reload(vad)
    try:
        assert vad.SPEECH_THRESHOLD == 0.71
    finally:
        monkeypatch.undo()
        importlib.reload(vad)                        # restore the live default

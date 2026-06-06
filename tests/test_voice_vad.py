"""VAD segmenter: turn per-frame speech booleans into clean speech segments, so a
noisy/musical room no longer yields 15s garbage blobs. The Segmenter is pure (no model).
A separate real-Silero test proves it actually scores speech >> music/noise."""
from __future__ import annotations

import numpy as np

from utah.voice import vad


def _frame(speechy: bool = False) -> bytes:
    n = vad.FRAME
    if speechy:
        return (np.ones(n, dtype="int16") * 8000).tobytes()
    return np.zeros(n, dtype="int16").tobytes()


SP = _frame(True)
SIL = _frame(False)


def test_silence_never_starts_a_segment():
    seg = vad.Segmenter(onset=3, offset=5)
    assert all(seg.feed(SIL, False) is None for _ in range(50))


def test_lone_spike_below_onset_is_rejected():
    """A single noise frame must not start a capture (onset needs >= N consecutive)."""
    seg = vad.Segmenter(onset=3, offset=5)
    assert seg.feed(SP, True) is None          # 1 speech frame
    assert seg.feed(SIL, False) is None         # then quiet — run resets
    for _ in range(10):
        assert seg.feed(SIL, False) is None     # still nothing captured


def test_speech_then_silence_emits_one_segment():
    seg = vad.Segmenter(onset=3, offset=4, preroll=2)
    out = []
    for _ in range(8):
        out.append(seg.feed(SP, True))          # sustained speech
    for _ in range(4):
        out.append(seg.feed(SIL, False))        # trailing silence ends it
    segments = [s for s in out if s is not None]
    assert len(segments) == 1
    # captured audio contains the speech (non-zero samples)
    a = np.frombuffer(segments[0], dtype="int16")
    assert int(np.abs(a).max()) > 1000


def test_segment_capped_at_max_frames():
    seg = vad.Segmenter(onset=2, offset=50, max_frames=10)
    out = [seg.feed(SP, True) for _ in range(40)]   # continuous speech, never silent
    segments = [s for s in out if s is not None]
    assert len(segments) >= 1                        # the cap forced a cut
    assert len(segments[0]) <= 10 * vad.FRAME_BYTES


def test_two_segments_in_sequence():
    seg = vad.Segmenter(onset=2, offset=3)
    def turn():
        out = [seg.feed(SP, True) for _ in range(5)] + [seg.feed(SIL, False) for _ in range(3)]
        return [s for s in out if s is not None]
    assert len(turn()) == 1
    assert len(turn()) == 1                          # segmenter resets cleanly between turns


def test_silero_scores_speech_far_above_noise():
    """Real Silero ONNX: synthesized speech must score >> white noise (the property the
    energy VAD lacked). Loads the model — the genuine proof, not a fake."""
    import wave
    from utah.voice import tts

    tts.PiperTTS().synth_wav("ace what is project utah", "/tmp/vad_test_speech.wav")
    with wave.open("/tmp/vad_test_speech.wav") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    sp = np.frombuffer(raw, dtype="int16")
    idx = (np.arange(int(len(sp) * 16000 / sr)) * sr / 16000).astype(int)
    sp16 = sp[idx]
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(len(sp16)) * 3000).astype("int16")

    v = vad.SileroVAD()
    def maxprob(sig):
        v.reset()
        return max(v.prob(sig[i:i + vad.FRAME].tobytes())
                   for i in range(0, len(sig) - vad.FRAME, vad.FRAME))
    assert maxprob(sp16) > 0.8        # clearly speech
    assert maxprob(noise) < 0.3       # clearly not

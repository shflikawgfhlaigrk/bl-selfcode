"""Bottom-up gate verification — capture policy + segment arming without a mic."""
from __future__ import annotations

import time

from utah.voice import loop, vad, wake


def test_should_capture_text_only_fallback():
    assert loop._should_capture(audio_wake_ok=False, armed_until=0.0) is True


def test_should_capture_requires_arm_when_audio_wake_on():
    future = time.monotonic() + 5.0
    assert loop._should_capture(audio_wake_ok=True, armed_until=future) is True
    assert loop._should_capture(audio_wake_ok=True, armed_until=0.0) is False


def test_segmenter_arm_starts_capture():
    seg = vad.Segmenter(onset=3, offset=3)
    silent = b"\x00" * vad.FRAME_BYTES
    speech = b"\x7f\xff" * vad.FRAME_BYTES
    seg.arm()
    for _ in range(2):
        assert seg.feed(speech, True) is None
    out = None
    for _ in range(seg.offset):
        out = seg.feed(silent, False)
    assert out is not None
    assert len(out) > 0


def test_end_to_end_gate_chain_audio_wake_path():
    """Mic armed → STT mishears wake → resolve_command → non-None command."""
    transcript = "Is what's the weather right now buddy?"
    assert wake.extract_command(transcript) == "what's the weather right now buddy?"
    cmd = wake.resolve_command(transcript, audio_wake=True)
    assert cmd == "what's the weather right now buddy?"

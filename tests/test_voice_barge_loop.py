"""Loop-level barge-in wiring (no mic): a sustained-loud frame DURING playback must
call stop_speaking() exactly once and signal the loop to capture the interruption.

The pure decision lives in :class:`utah.voice.barge.BargeDetector`; this proves the
loop's glue — :func:`utah.voice.loop._on_playback_frame` — actually pulls the trigger
(stops the voice + raises the barge flag) and stays silent on echo-level frames.
"""
from __future__ import annotations

from utah.voice import loop
from utah.voice.barge import BargeDetector


def test_playback_frame_stops_voice_and_flags_on_sustained_loud():
    det = BargeDetector(rms_threshold=0.08, min_frames=3)
    stops = {"n": 0}

    def fake_stop():
        stops["n"] += 1
        return {"stopped": True}

    # three consecutive loud frames → barge fires on the 3rd
    r1 = loop._on_playback_frame(0.20, det, fake_stop)
    r2 = loop._on_playback_frame(0.20, det, fake_stop)
    r3 = loop._on_playback_frame(0.20, det, fake_stop)
    assert (r1, r2, r3) == (False, False, True)
    assert stops["n"] == 1                      # voice cut exactly once, on the trigger frame


def test_playback_frame_ignores_echo_level_audio():
    det = BargeDetector(rms_threshold=0.08, min_frames=3)
    stops = {"n": 0}
    for _ in range(40):
        assert loop._on_playback_frame(0.04, det, lambda: stops.__setitem__("n", stops["n"] + 1)) is False
    assert stops["n"] == 0                       # Ace's own echo never stops Ace


def test_playback_frame_does_not_double_stop_once_latched():
    det = BargeDetector(rms_threshold=0.08, min_frames=2)
    stops = {"n": 0}
    stop = lambda: stops.__setitem__("n", stops["n"] + 1)
    loop._on_playback_frame(0.20, det, stop)
    assert loop._on_playback_frame(0.20, det, stop) is True   # fires here
    loop._on_playback_frame(0.20, det, stop)                  # latched
    loop._on_playback_frame(0.20, det, stop)
    assert stops["n"] == 1                       # one stop, not one-per-frame

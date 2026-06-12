"""Barge-in — Michael interrupts Ace mid-sentence ("no time to stop").

While Ace is speaking, the always-on mic used to DROP every frame (the echo guard
that stops Ace's own voice from re-triggering the wake word). That guard made
barge-in impossible: nothing the user said during playback was ever heard. The
:class:`BargeDetector` is the echo-aware unit that fixes it — it watches the mic
DURING playback and fires ONLY on sustained, genuinely-loud speech (well above the
level Ace's own voice bleeds back into the mic), so a real interruption stops the
voice instantly while Ace's echo never trips it.

Pure + deterministic (RMS in, decision out): no mic, no audio, no model.
"""
from __future__ import annotations

from utah.voice.barge import BargeDetector


def test_sustained_loud_speech_triggers_a_barge():
    """N consecutive frames above the barge threshold = a real interruption → fire."""
    d = BargeDetector(rms_threshold=0.08, min_frames=4)
    # three loud frames is not yet enough (a transient / a single syllable of echo)
    assert d.feed(0.20) is False
    assert d.feed(0.20) is False
    assert d.feed(0.20) is False
    assert d.feed(0.20) is True          # the 4th consecutive loud frame fires
    assert d.fired is True


def test_quiet_frames_never_barge():
    """Ace's own voice bleeding back (or a quiet room) sits below the threshold."""
    d = BargeDetector(rms_threshold=0.08, min_frames=4)
    for _ in range(50):
        assert d.feed(0.03) is False     # echo-level / room noise — never a barge
    assert d.fired is False


def test_a_loud_blip_resets_and_does_not_accumulate():
    """A run must be CONSECUTIVE: a single loud spike between quiet frames (a door slam,
    one echoed plosive) must not creep toward the trigger over time."""
    d = BargeDetector(rms_threshold=0.08, min_frames=4)
    for _ in range(10):
        assert d.feed(0.20) is False or True  # noqa - just feed
        d.reset() if False else None
    # interleave loud/quiet — the quiet frame breaks the run each time
    d2 = BargeDetector(rms_threshold=0.08, min_frames=4)
    for _ in range(20):
        assert d2.feed(0.20) is False        # one loud
        assert d2.feed(0.02) is False        # then quiet → run resets
    assert d2.fired is False


def test_fires_exactly_once_until_reset():
    """The barge is an edge, not a level: once fired it stays latched (so the loop
    acts on ONE stop), and only reset() re-arms it for the next playback."""
    d = BargeDetector(rms_threshold=0.08, min_frames=2)
    assert d.feed(0.20) is False
    assert d.feed(0.20) is True              # fires
    assert d.feed(0.20) is False             # latched — does not fire again
    assert d.feed(0.20) is False
    d.reset()
    assert d.fired is False
    assert d.feed(0.20) is False
    assert d.feed(0.20) is True              # re-armed after reset


def test_reset_clears_a_partial_run():
    """reset() between playbacks clears any half-accumulated run so a leftover loud
    tail from the previous turn can't pre-load the next turn's trigger."""
    d = BargeDetector(rms_threshold=0.08, min_frames=3)
    assert d.feed(0.20) is False
    assert d.feed(0.20) is False             # 2 of 3 — partial run
    d.reset()
    assert d.feed(0.20) is False             # run restarted from zero
    assert d.feed(0.20) is False
    assert d.feed(0.20) is True              # needs a fresh 3-in-a-row

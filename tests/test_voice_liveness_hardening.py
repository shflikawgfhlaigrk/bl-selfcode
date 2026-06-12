"""Hardening for MicLiveness — config that can't work must fail at construction
(a silent zero/negative cooldown would page on every poll), and a corrupt frame
value (NaN from a bad buffer) must not poison the quiet clock either way."""
from __future__ import annotations

import math

import pytest

from utah.voice.liveness import MicLiveness


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(clock, threshold=0.0008, alert_after=30.0, cooldown=1800.0):
    return MicLiveness(threshold=threshold, alert_after_s=alert_after,
                       cooldown_s=cooldown, now=clock)


@pytest.mark.parametrize("kwargs", [
    {"threshold": -0.1},          # negative RMS threshold is meaningless
    {"alert_after": 0.0},         # would alert instantly, forever
    {"alert_after": -5.0},
    {"cooldown": -1.0},           # negative cooldown re-fires every poll (page storm)
    {"threshold": float("nan")},  # NaN disables every comparison silently
])
def test_invalid_config_rejected_at_construction(kwargs):
    clk = Clock()
    with pytest.raises(ValueError):
        make(clk, **kwargs)


def test_zero_cooldown_is_allowed():
    # cooldown=0 = "no dedup", a legitimate test/diagnostic setting
    clk = Clock()
    ml = make(clk, cooldown=0.0)
    clk.t += 40
    assert ml.poll()[0]["event"] == "deaf"


def test_nan_frame_is_ignored_not_counted_as_audio():
    """A NaN RMS (corrupt capture buffer) must neither reset the quiet clock (it is
    not real audio) nor crash; the deaf episode still fires on schedule."""
    clk = Clock()
    ml = make(clk)
    clk.t += 31
    ml.feed_frame(math.nan)
    events = ml.poll()
    assert len(events) == 1 and events[0]["event"] == "deaf"


def test_exactly_at_threshold_counts_as_quiet():
    """The contract is strictly-above: a frame AT the zeros threshold is still 'zeros'.
    This pins the > (vs >=) semantic the loop's TRUE_SILENCE constant was tuned for."""
    clk = Clock()
    ml = make(clk, threshold=0.0008)
    clk.t += 31
    ml.feed_frame(0.0008)            # equal — must NOT reset the quiet clock
    assert ml.poll()[0]["event"] == "deaf"


def test_recovered_event_is_not_lost_if_polls_are_sparse():
    """recover → another deaf-window's worth of silence → ONE poll must deliver the
    queued recovered event (evidence is never dropped between sparse polls)."""
    clk = Clock()
    ml = make(clk, cooldown=0.0)
    clk.t += 40
    assert ml.poll()[0]["event"] == "deaf"
    ml.feed_frame(0.05)              # recovered — queued, not yet polled
    clk.t += 40                      # silence resumes before the monitor polls
    events = ml.poll()
    kinds = [e["event"] for e in events]
    assert "recovered" in kinds      # the queued evidence survived
    assert kinds.index("recovered") < len(kinds) - kinds[::-1].index("deaf") or "deaf" not in kinds


def test_quiet_for_s_reflects_clock():
    clk = Clock()
    ml = make(clk)
    clk.t += 12.5
    assert ml.quiet_for_s == 12.5

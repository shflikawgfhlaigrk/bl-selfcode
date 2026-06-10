"""MicLiveness — the device-liveness state machine (raw-signal semantic).

Locks the contract the 2026-06-09 investigation needed and didn't have: one
deaf event per episode with the raw level attached, a recovered event carrying
episode DURATION (the diagnosis discriminator), muted-is-not-deaf, and cooldown
dedup across episodes."""
from __future__ import annotations

from utah.voice.liveness import MicLiveness


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(clock, threshold=0.0008, alert_after=30.0, cooldown=1800.0):
    return MicLiveness(threshold=threshold, alert_after_s=alert_after,
                       cooldown_s=cooldown, now=clock)


def test_zeros_past_threshold_emit_one_deaf_event_with_raw_level():
    clk = Clock()
    ml = make(clk)
    ml.feed_frame(0.0)
    clk.t += 31
    ml.feed_frame(0.0002)          # below threshold — still "zeros"
    events = ml.poll()
    assert len(events) == 1
    assert events[0]["event"] == "deaf"
    assert events[0]["quiet_for_s"] >= 30
    assert events[0]["max_raw_rms"] == 0.0002   # the evidence the detail needs
    clk.t += 5
    assert ml.poll() == []          # same episode — never re-fires


def test_recovery_event_carries_episode_duration():
    clk = Clock()
    ml = make(clk)
    clk.t += 40
    assert ml.poll()[0]["event"] == "deaf"
    clk.t += 20                     # zeros continued another 20s
    ml.feed_frame(0.05)             # audio returns
    events = ml.poll()
    assert events == [{"event": "recovered", "deaf_for_s": 60.0}]


def test_quiet_room_above_threshold_never_alerts():
    clk = Clock()
    ml = make(clk)
    for _ in range(10):
        clk.t += 10
        ml.feed_frame(0.0012)       # quiet room, raw floor above zeros threshold
    assert ml.poll() == []


def test_muted_windows_do_not_accumulate_quiet():
    clk = Clock()
    ml = make(clk)
    for _ in range(8):              # 40s of intentional mute (we're speaking)
        clk.t += 5
        ml.feed_muted()
    assert ml.poll() == []
    assert ml.quiet_for_s < 30


def test_cooldown_separates_episodes():
    clk = Clock()
    ml = make(clk, cooldown=1800.0)
    clk.t += 40
    assert ml.poll()[0]["event"] == "deaf"
    ml.feed_frame(0.05)             # recover
    ml.poll()
    clk.t += 60                     # second episode 1 min later — inside cooldown
    assert ml.poll() == []          # deduped (no page storm)
    ml.feed_frame(0.05)
    ml.poll()
    clk.t += 1801                   # past cooldown
    assert ml.poll()[0]["event"] == "deaf"


def test_max_raw_resets_per_poll_window():
    clk = Clock()
    ml = make(clk)
    ml.feed_frame(0.5)
    ml.poll()
    clk.t += 40
    ev = ml.poll()
    assert ev[0]["max_raw_rms"] == 0.0   # the loud frame was the PREVIOUS window

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


# --- 2026-06-13 restart-storm regression -----------------------------------------
# Overnight (00:40–07:11) the supervisor restarted the voice child every ~90s
# ("voice wedged → restart"), repeatedly dropping the armed wake word ("wake word
# not configured again"). Cause was NOT the state machine — it was the calibrated
# TRUE_SILENCE constant. The recorded mic_silent episodes carried max_raw_rms peaks
# of 0.000041–0.000770 (every one NON-zero: a live-but-very-quiet room), yet
# TRUE_SILENCE=0.0008 sat ABOVE that floor, so a quiet room read as a dead device.
# A genuinely wedged/dead CoreAudio device delivers EXACT 0.0; the threshold must sit
# below the live 16-bit mic dither floor (~3e-5) so only true zeros trip deaf.

#: The lowest max-raw peak seen across the overnight false episodes.
_OBSERVED_QUIET_FLOOR_MIN = 0.000041


def test_true_silence_is_below_real_room_floor():
    from utah.voice.loop import TRUE_SILENCE
    assert TRUE_SILENCE < _OBSERVED_QUIET_FLOOR_MIN, (
        f"TRUE_SILENCE={TRUE_SILENCE} sits at/above the observed quiet-room floor "
        f"{_OBSERVED_QUIET_FLOOR_MIN} → quiet room misread as dead mic → restart storm"
    )


def test_observed_quiet_room_floor_never_goes_deaf():
    from utah.voice.loop import TRUE_SILENCE
    clk = Clock()
    ml = make(clk, threshold=TRUE_SILENCE)
    # The exact recorded episode peaks — a live room, never a dead device.
    for peak in (0.000041, 0.000138, 0.000770, 0.000218, 0.000524):
        for _ in range(4):           # well past the 30s alert window
            clk.t += 10
            ml.feed_frame(peak)
    assert ml.poll() == [], "a live-but-quiet room must never be flagged deaf"


def test_truly_dead_device_zeros_still_detected():
    from utah.voice.loop import TRUE_SILENCE
    clk = Clock()
    ml = make(clk, threshold=TRUE_SILENCE)
    ml.feed_frame(0.0)               # wedged CoreAudio handle: pure zeros
    clk.t += 31
    ml.feed_frame(0.0)
    ev = ml.poll()
    assert ev and ev[0]["event"] == "deaf"   # a real outage is still caught

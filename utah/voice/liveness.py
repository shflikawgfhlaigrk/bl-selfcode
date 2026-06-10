"""Mic device-liveness — a pure state machine, fed RAW (pre-DSP) frame RMS.

Why raw: liveness asks "is the DEVICE delivering anything?" — a raw-signal
question. The capture chain's 120 Hz high-pass can floor a quiet room's residual
(mostly sub-120 Hz rumble on this mic) toward zero, making a healthy-but-quiet
device indistinguishable from a dead one post-filter. (2026-06-09 investigation:
three mic_silent episodes/day, each self-recovering in <75 s; the detector
measured POST-filter RMS and recorded no recovery, no raw level, no device — so
episodes could not be diagnosed. This class fixes the measurement semantic and
emits the evidence.)

Pure + injectable clock: the audio callback calls :meth:`feed_frame` (cheap, no
I/O); the monitor thread calls :meth:`poll` and performs all logging/recording
on the events returned. One ``deaf`` event per episode (cooldown-gated), one
``recovered`` event when audio returns — carrying the episode duration, which is
the discriminator the next investigation needs (seconds = playback/CoreAudio
glitch; minutes = another process or lock held the device).
"""
from __future__ import annotations

import time

__all__ = ["MicLiveness"]


class MicLiveness:
    def __init__(self, *, threshold: float, alert_after_s: float, cooldown_s: float,
                 now=time.monotonic) -> None:
        self._threshold = threshold
        self._alert_after_s = alert_after_s
        self._cooldown_s = cooldown_s
        self._now = now
        t = now()
        self._last_loud = t
        self._alerted = False
        # NOT 0.0: time.monotonic() starts near zero after boot, so "now - 0 < cooldown"
        # would silently suppress the FIRST episode for the cooldown length (30 min).
        self._last_record = t - cooldown_s - 1.0
        self._pending: list[dict] = []
        self._max_raw = 0.0

    # --- hot path (audio callback): no I/O, no allocation beyond a possible event ----

    def feed_frame(self, raw_rms: float) -> None:
        """Per-frame update with the RAW (pre-DSP) RMS of the captured block."""
        if raw_rms > self._max_raw:
            self._max_raw = raw_rms
        if raw_rms > self._threshold:
            t = self._now()
            if self._alerted:
                # audio is back after a reported episode — queue the evidence
                self._pending.append({
                    "event": "recovered",
                    "deaf_for_s": round(t - self._last_loud, 1),
                })
                self._alerted = False
            self._last_loud = t

    def feed_muted(self) -> None:
        """The capture window is intentionally muted (we are processing or speaking) —
        muted is not deaf; the quiet clock must not accumulate."""
        self._last_loud = self._now()

    # --- monitor thread -------------------------------------------------------------

    @property
    def quiet_for_s(self) -> float:
        return self._now() - self._last_loud

    def poll(self) -> list[dict]:
        """Events since the last poll. At most one ``deaf`` per episode (and never
        again within the cooldown); ``recovered`` events carry episode duration."""
        events, self._pending = self._pending, []
        quiet = self.quiet_for_s
        now = self._now()
        if (quiet > self._alert_after_s and not self._alerted
                and (now - self._last_record) > self._cooldown_s):
            self._alerted = True
            self._last_record = now
            events.append({
                "event": "deaf",
                "quiet_for_s": round(quiet, 1),
                "max_raw_rms": round(self._max_raw, 6),
            })
        self._max_raw = 0.0
        return events

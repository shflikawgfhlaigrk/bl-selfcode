"""Barge-in detection — let Michael interrupt Ace mid-sentence ("no time to stop").

The always-on mic loop normally DROPS every frame while any process is speaking
(the echo guard: Ace's replies say "Ace", so an open mic re-fires the wake word on
his own voice — a self-conversation). That guard is correct for the wake path but it
also makes interruption impossible: nothing said *during* playback is ever heard.

:class:`BargeDetector` is the echo-aware unit that re-opens that window safely. The
loop feeds it the RMS of each (HPF+AGC-cleaned) frame *while Ace speaks*; it fires
only when speech is **sustained AND genuinely loud** — a run of consecutive frames
above an elevated threshold set well above the level Ace's own voice bleeds back into
the built-in mic. So a real interruption stops the voice instantly, while Ace's echo,
a quiet room, or a single transient (a door slam, one echoed plosive) never trip it.

Why RMS + a consecutive run rather than the wake word: barge-in must work for ANY
speech ("stop", "no", "wait", a fresh command), not only "ace"; and it must be
near-instant (per-32ms-frame), so a model pass per frame during playback is both too
slow and too narrow. The threshold/run are the two knobs the loop tunes from real mic
levels. Pure and deterministic — no audio, no model, no I/O — so it is fully unit
tested.
"""
from __future__ import annotations


class BargeDetector:
    """Decide, frame by frame, whether the user is talking OVER Ace.

    ``feed(rms)`` returns ``True`` exactly once — on the frame that completes a run of
    ``min_frames`` consecutive frames whose RMS is ``>= rms_threshold``. After it fires
    it stays latched (returns ``False``) until :meth:`reset`, so the loop acts on ONE
    stop per playback. A frame below the threshold breaks the run (the run must be
    consecutive, so a lone loud blip between quiet frames never accumulates toward the
    trigger). :meth:`reset` re-arms it for the next playback and clears any partial run.
    """

    def __init__(self, rms_threshold: float = 0.08, min_frames: int = 4) -> None:
        self.rms_threshold = float(rms_threshold)
        self.min_frames = max(1, int(min_frames))
        self._run = 0
        self.fired = False

    def reset(self) -> None:
        """Re-arm for the next playback; drop any half-accumulated run."""
        self._run = 0
        self.fired = False

    def feed(self, rms: float) -> bool:
        """Feed one frame's RMS. Returns True only on the frame that fires the barge."""
        if self.fired:                       # latched — one stop per playback
            return False
        if rms >= self.rms_threshold:
            self._run += 1
        else:
            self._run = 0                    # the run must be consecutive
        if self._run >= self.min_frames:
            self.fired = True
            return True
        return False


__all__ = ["BargeDetector"]

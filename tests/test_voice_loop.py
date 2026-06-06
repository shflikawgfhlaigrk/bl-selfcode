"""Voice capture VAD: the speech threshold adapts ABOVE the room's ambient floor
so a noisy mic (ambient > old fixed 0.02) doesn't make every block read 'loud'
and grab 15 s noise blobs (the bug Michael's live test surfaced). A real speech
burst is segmented out of ambient."""
from __future__ import annotations

import queue

import numpy as np

from utah.voice import loop


def _block(rms: float) -> bytes:
    """A BLOCK-sized int16 PCM block with the given approximate RMS."""
    return np.full(loop.BLOCK, int(rms * 32768), dtype=np.int16).tobytes()


def test_speech_threshold_adapts_above_ambient():
    assert loop._speech_threshold(0.10) > 0.10        # noisy room → high threshold
    assert 0.02 <= loop._speech_threshold(0.001) <= 0.05  # quiet room → low floor (catch speech)
    assert loop._speech_threshold(0.08) > 0.08


def test_capture_one_segments_speech_from_noisy_ambient():
    thr = loop._speech_threshold(0.08)  # ~0.144 — ambient 0.08 is BELOW this
    q: queue.Queue = queue.Queue()
    for _ in range(5):
        q.put(_block(0.08))                       # ambient — must NOT trigger onset
    for _ in range(8):
        q.put(_block(0.30))                       # speech — triggers capture
    for _ in range(loop.SILENCE_BLOCKS + 1):
        q.put(_block(0.04))                       # back to quiet — ends the turn
    pcm = loop._capture_one(q, thr)
    a = np.frombuffer(pcm, dtype=np.int16).astype("float32")
    assert float(np.abs(a).max()) / 32768 > 0.2   # the loud speech was captured
    assert loop._rms(pcm) > thr                    # clip is speech-dominated, not ambient


def test_capture_one_onset_needs_two_loud_blocks():
    """A single ambient spike must not start a capture (avoids noise false-starts)."""
    thr = loop._speech_threshold(0.05)
    q: queue.Queue = queue.Queue()
    q.put(_block(0.30))                            # one spike
    for _ in range(3):
        q.put(_block(0.04))                        # then quiet
    for _ in range(8):
        q.put(_block(0.30))                        # real speech (2+ consecutive)
    for _ in range(loop.SILENCE_BLOCKS + 1):
        q.put(_block(0.04))
    pcm = loop._capture_one(q, thr)
    # captured the real speech burst, not derailed by the lone spike
    assert loop._rms(pcm) > thr

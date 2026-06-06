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


def test_speech_threshold_tracks_floor():
    assert loop._speech_threshold(0.10) > 0.10            # noisy floor → high threshold
    assert 0.01 <= loop._speech_threshold(0.001) <= 0.05  # quiet floor → low (catch speech)
    assert loop._speech_threshold(0.08) > 0.08


def test_capture_one_segments_speech_from_quiet_ambient():
    floor = [0.01]                                # quiet room → threshold ~0.03
    q: queue.Queue = queue.Queue()
    for _ in range(5):
        q.put(_block(0.008))                      # ambient (below threshold)
    for _ in range(8):
        q.put(_block(0.08))                       # normal speech (above threshold)
    for _ in range(loop.SILENCE_BLOCKS + 1):
        q.put(_block(0.005))                      # quiet again → ends the turn
    pcm = loop._capture_one(q, floor)
    a = np.frombuffer(pcm, dtype=np.int16).astype("float32")
    assert float(np.abs(a).max()) / 32768 > 0.05  # the speech was captured


def test_capture_one_onset_needs_two_loud_blocks():
    """A single spike must not start a capture (avoids noise false-starts)."""
    floor = [0.01]
    q: queue.Queue = queue.Queue()
    q.put(_block(0.08))                            # one lone spike
    for _ in range(3):
        q.put(_block(0.006))                      # then quiet
    for _ in range(8):
        q.put(_block(0.08))                       # real speech (2+ consecutive)
    for _ in range(loop.SILENCE_BLOCKS + 1):
        q.put(_block(0.005))
    pcm = loop._capture_one(q, floor)
    assert loop._rms(pcm) > loop._speech_threshold(0.01)


def test_floor_adapts_down_in_a_quiet_room():
    """The noise floor decays toward a quiet ambient so the threshold stays low
    enough to catch speech (fixes the 'calibrated too high on a noisy snapshot' bug)."""
    floor = [0.05]                                # snapshot started high
    q: queue.Queue = queue.Queue()
    for _ in range(20):
        q.put(_block(0.004))                      # quiet idle
    for _ in range(8):
        q.put(_block(0.06))                       # speech that 0.05*3=0.15 would have MISSED
    for _ in range(loop.SILENCE_BLOCKS + 1):
        q.put(_block(0.004))
    pcm = loop._capture_one(q, floor)
    assert floor[0] < 0.02                        # floor adapted down during the quiet idle
    a = np.frombuffer(pcm, dtype=np.int16).astype("float32")
    assert float(np.abs(a).max()) / 32768 > 0.04  # and the speech was then captured

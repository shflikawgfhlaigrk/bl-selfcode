"""openWakeWord arming boundary — injectable, no native deps in unit tests."""
from __future__ import annotations

import numpy as np

from utah.voice import oww, vad


class _FakeModel:
    def __init__(self, scores: list[dict[str, float]] | None = None) -> None:
        self._scores = list(scores or [])
        self._i = 0

    def predict(self, chunk: np.ndarray) -> dict[str, float]:
        if self._i < len(self._scores):
            out = self._scores[self._i]
            self._i += 1
            return out
        return {}


def _feed_chunk(det: oww.OpenWakeWord) -> list[oww.Detection]:
    buf = np.zeros(oww.CHUNK_SAMPLES, dtype=np.int16).tobytes()
    hits: list[oww.Detection] = []
    step = vad.FRAME_BYTES
    for i in range(0, len(buf), step):
        hits.extend(det.feed(buf[i : i + step]))
    return hits


def test_feed_emits_detection_above_threshold():
    det = oww.OpenWakeWord(threshold=0.5, model_factory=lambda: _FakeModel(
        [{"hey_ace": 0.9}],
    ))
    det.load()
    hits = _feed_chunk(det)
    assert len(hits) == 1
    assert hits[0].keyword == "hey_ace"
    assert hits[0].confidence == 0.9


def test_refractory_suppresses_repeat_hits():
    det = oww.OpenWakeWord(threshold=0.5, refractory_chunks=2,
                           model_factory=lambda: _FakeModel(
                               [{"hey_ace": 0.9}, {"hey_ace": 0.9}, {"hey_ace": 0.9}],
                           ))
    det.load()
    all_hits: list[oww.Detection] = []
    for _ in range(3):
        all_hits.extend(_feed_chunk(det))
    assert len(all_hits) == 1  # refractory swallowed the rest

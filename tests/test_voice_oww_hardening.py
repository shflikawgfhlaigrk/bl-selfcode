"""Hardening for the openWakeWord wrapper: honest load gates (missing model /
missing dep NEVER pretends to arm), the singleton seam, refractory/reset
semantics, and a bounded resource bootstrap (a hung download must not wedge the
voice loop's startup forever)."""
from __future__ import annotations

import numpy as np
import pytest

from utah.voice import oww, vad


class _FakeModel:
    def __init__(self, scores=None):
        self._scores = list(scores or [])
        self._i = 0
        self.reset_calls = 0

    def predict(self, chunk):
        if self._i < len(self._scores):
            out = self._scores[self._i]
            self._i += 1
            return out
        return {}

    def reset(self):
        self.reset_calls += 1


def _feed_chunk(det):
    buf = np.zeros(oww.CHUNK_SAMPLES, dtype=np.int16).tobytes()
    hits = []
    for i in range(0, len(buf), vad.FRAME_BYTES):
        hits.extend(det.feed(buf[i:i + vad.FRAME_BYTES]))
    return hits


@pytest.fixture(autouse=True)
def _isolate_singleton():
    before = oww._oww
    yield
    oww.set_oww(before)


# ── honest gates: unavailable NEVER reports armed ────────────────────────────

def test_load_false_when_model_file_missing(monkeypatch):
    monkeypatch.setattr(oww, "_ensure_wake_model", lambda path: None)
    det = oww.OpenWakeWord(model_path="/nonexistent/hey_ace.onnx")
    assert det.load() is False
    assert det.available is False


def test_load_false_when_openwakeword_not_installed(monkeypatch, tmp_path):
    model = tmp_path / "hey_ace.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setattr(oww, "_import_openwakeword", lambda: None)
    det = oww.OpenWakeWord(model_path=str(model))
    assert det.load() is False
    assert det.available is False


def test_feed_before_load_returns_no_hits():
    det = oww.OpenWakeWord(threshold=0.5)
    assert det.feed(b"\x00\x00" * vad.FRAME) == []


def test_get_oww_returns_none_when_load_fails(monkeypatch):
    oww.set_oww(None)
    monkeypatch.setattr(oww.OpenWakeWord, "load", lambda self: False)
    assert oww.get_oww() is None        # text-only fallback, never a dead-armed object


def test_set_oww_injects_the_singleton():
    det = oww.OpenWakeWord(threshold=0.5, model_factory=lambda: _FakeModel())
    det.load()
    oww.set_oww(det)
    assert oww.get_oww() is det


# ── scoring semantics ────────────────────────────────────────────────────────

def test_below_threshold_scores_are_ignored():
    det = oww.OpenWakeWord(threshold=0.68,
                           model_factory=lambda: _FakeModel([{"hey_ace": 0.67}]))
    det.load()
    assert _feed_chunk(det) == []


def test_best_of_multiple_keywords_wins_the_chunk():
    det = oww.OpenWakeWord(threshold=0.5, model_factory=lambda: _FakeModel(
        [{"hey_ace": 0.7, "hey_ace_v3": 0.9}]))
    det.load()
    hits = _feed_chunk(det)
    assert len(hits) == 1 and hits[0].keyword == "hey_ace_v3" and hits[0].confidence == 0.9


def test_sample_index_advances_per_chunk():
    det = oww.OpenWakeWord(threshold=0.5, refractory_chunks=0,
                           model_factory=lambda: _FakeModel(
                               [{"hey_ace": 0.9}, {"hey_ace": 0.9}]))
    det.load()
    hits = _feed_chunk(det) + _feed_chunk(det)
    assert [h.sample_index for h in hits] == [0, oww.CHUNK_SAMPLES]


def test_reset_clears_refractory_and_buffer_and_model():
    model = _FakeModel([{"hey_ace": 0.9}, {"hey_ace": 0.9}])
    det = oww.OpenWakeWord(threshold=0.5, refractory_chunks=100,
                           model_factory=lambda: model)
    det.load()
    assert len(_feed_chunk(det)) == 1
    det.reset()                          # stream reopened — old refractory must not leak
    assert len(_feed_chunk(det)) == 1    # fires again immediately after reset
    assert model.reset_calls == 1        # model state cleared too


def test_partial_frames_accumulate_to_one_chunk():
    """80ms chunks are assembled from 32ms frames — no frame is dropped."""
    det = oww.OpenWakeWord(threshold=0.5,
                           model_factory=lambda: _FakeModel([{"hey_ace": 0.9}]))
    det.load()
    frame = np.zeros(vad.FRAME, dtype=np.int16).tobytes()
    hits = []
    n = 0
    while not hits and n < 10:
        hits.extend(det.feed(frame))
        n += 1
    assert hits and n == 3               # 3 × 512 = 1536 ≥ 1280 samples → first chunk


# ── bounded bootstrap ────────────────────────────────────────────────────────

def test_bootstrap_download_is_socket_bounded(monkeypatch):
    """The one-time ONNX resource fetch runs under a socket default timeout — a
    hung CDN must not wedge voice startup forever (rubric: no unbounded network)."""
    import socket
    import sys
    import types

    observed = {}

    fake_oww = types.ModuleType("openwakeword")
    fake_oww.__file__ = "/nonexistent/openwakeword/__init__.py"
    fake_oww.FEATURE_MODELS = {"mel": {"download_url": "http://example.invalid/m.tflite"}}
    fake_utils = types.ModuleType("openwakeword.utils")

    def download_file(url, target):
        observed["timeout"] = socket.getdefaulttimeout()
    fake_utils.download_file = download_file
    fake_oww.utils = fake_utils

    monkeypatch.setitem(sys.modules, "openwakeword", fake_oww)
    monkeypatch.setitem(sys.modules, "openwakeword.utils", fake_utils)
    before = socket.getdefaulttimeout()
    oww._bootstrap_oww_resources()
    assert observed.get("timeout") not in (None,)        # bounded during the fetch
    assert socket.getdefaulttimeout() == before          # and restored after

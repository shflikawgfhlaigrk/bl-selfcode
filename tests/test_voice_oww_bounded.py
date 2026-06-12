"""openWakeWord wrapper boundaries the hardening file left open: a corrupt mic
frame (odd byte length) must never crash the feed path, a misbehaving model
(garbage score values, factory blow-up) degrades honestly instead of killing the
loop's startup, and the one-time resource bootstrap survives a partial failure
(one dead URL must not abort the rest) while reporting completion HONESTLY."""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from utah.voice import oww, vad


class _FakeModel:
    def __init__(self, scores=None):
        self._scores = list(scores or [])
        self._i = 0

    def predict(self, chunk):
        if self._i < len(self._scores):
            out = self._scores[self._i]
            self._i += 1
            return out
        return {}


def _feed_chunk(det):
    buf = np.zeros(oww.CHUNK_SAMPLES, dtype=np.int16).tobytes()
    hits = []
    for i in range(0, len(buf), vad.FRAME_BYTES):
        hits.extend(det.feed(buf[i:i + vad.FRAME_BYTES]))
    return hits


def test_odd_length_frame_does_not_crash_feed():
    """A torn frame (odd byte count) from a glitching stream must not raise out of
    feed() — that exception would bubble into run()'s retry loop and bounce the mic."""
    det = oww.OpenWakeWord(threshold=0.5, model_factory=lambda: _FakeModel([{"hey_ace": 0.9}]))
    det.load()
    torn = b"\x00" * (vad.FRAME_BYTES + 1)
    hits = det.feed(torn)               # must not raise
    assert isinstance(hits, list)
    # subsequent intact frames still assemble chunks and detect
    hits += _feed_chunk(det)
    assert any(h.keyword == "hey_ace" for h in hits)


def test_garbage_score_values_are_skipped_not_fatal():
    det = oww.OpenWakeWord(threshold=0.5, model_factory=lambda: _FakeModel(
        [{"bad": "not-a-number", "none": None, "good": 0.9}]))
    det.load()
    hits = _feed_chunk(det)
    assert [h.keyword for h in hits] == ["good"]


def test_nan_confidence_never_fires():
    det = oww.OpenWakeWord(threshold=0.5, model_factory=lambda: _FakeModel(
        [{"hey_ace": float("nan")}]))
    det.load()
    assert _feed_chunk(det) == []


def test_model_factory_raising_is_an_honest_load_failure():
    """A blowing-up factory must not propagate out of load() — get_oww() would take
    the whole voice process down at startup. False = text-only fallback."""
    def boom():
        raise RuntimeError("onnxruntime exploded")

    det = oww.OpenWakeWord(threshold=0.5, model_factory=boom)
    assert det.load() is False
    assert det.available is False


def _fake_openwakeword(monkeypatch, tmp_path, urls, downloader):
    fake = types.ModuleType("openwakeword")
    fake.__file__ = str(tmp_path / "openwakeword" / "__init__.py")
    fake.FEATURE_MODELS = {
        f"f{i}": {"download_url": u} for i, u in enumerate(urls)
    }
    fake_utils = types.ModuleType("openwakeword.utils")
    fake_utils.download_file = downloader
    fake.utils = fake_utils
    monkeypatch.setitem(sys.modules, "openwakeword", fake)
    monkeypatch.setitem(sys.modules, "openwakeword.utils", fake_utils)
    return tmp_path / "openwakeword" / "resources" / "models"


def test_bootstrap_skips_download_when_resources_present(monkeypatch, tmp_path):
    target = _fake_openwakeword(
        monkeypatch, tmp_path, ["http://example.invalid/a.tflite"],
        lambda url, t: pytest.fail("must not download when resources exist"),
    )
    target.mkdir(parents=True)
    (target / "melspectrogram.onnx").write_bytes(b"m")
    (target / "embedding_model.onnx").write_bytes(b"e")
    assert oww._bootstrap_oww_resources() is True


def test_bootstrap_one_dead_url_does_not_abort_the_rest(monkeypatch, tmp_path):
    """First URL times out — the remaining downloads must still be attempted, and
    the result is honest (False: required files still missing)."""
    calls = []

    def downloader(url, t):
        calls.append(url)
        if "dead" in url:
            raise OSError("connection timed out")

    _fake_openwakeword(
        monkeypatch, tmp_path,
        ["http://example.invalid/dead.tflite", "http://example.invalid/ok.tflite"],
        downloader,
    )
    assert oww._bootstrap_oww_resources() is False     # files never materialized
    assert any("ok" in u for u in calls)               # later URLs still tried


def test_bootstrap_reports_true_only_when_required_files_exist(monkeypatch, tmp_path):
    """A downloader that 'succeeds' without producing the files must not be
    reported as a working bootstrap (honest gate)."""
    _fake_openwakeword(
        monkeypatch, tmp_path, ["http://example.invalid/a.tflite"], lambda url, t: None,
    )
    assert oww._bootstrap_oww_resources() is False

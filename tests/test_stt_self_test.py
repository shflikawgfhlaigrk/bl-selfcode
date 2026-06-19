"""STT engine self-test — PROVE death, never assume it (2026-06-19).

A loud post-wake segment that transcribes to "" is the NORMAL result for ambient
noise / music / a TV / Ace's own echo — a healthy engine correctly returns "".
The brand-new dead-engine self-heal (2026-06-18) wrongly read those empties as
engine death, churning the healthy whisper.cpp server into a cooldown and flipping
to mlx on plain no-speech audio. The fix: only conclude an engine is dead when it
ALSO fails to transcribe a KNOWN-GOOD speech clip. These tests lock that contract.
"""
from __future__ import annotations

from utah.voice import stt


class _FakeEngine:
    def __init__(self, out):
        self._out = out

    def transcribe(self, _wav):
        if isinstance(self._out, Exception):
            raise self._out
        return self._out


def _use(monkeypatch, engine, *, probe="/tmp/known_good.wav"):
    monkeypatch.setattr(stt, "_probe_wav", lambda: probe)
    monkeypatch.setattr(stt, "get_stt", lambda: engine)


def test_engine_that_transcribes_known_speech_is_ALIVE(monkeypatch):
    _use(monkeypatch, _FakeEngine("the quick brown fox"))
    assert stt.engine_self_test() is True   # passes -> caller must NOT flag dead


def test_engine_empty_on_known_speech_is_DEAD(monkeypatch):
    _use(monkeypatch, _FakeEngine(""))
    assert stt.engine_self_test() is False  # the real 2026-06-18 silent-deaf failure


def test_engine_raising_on_known_speech_is_DEAD(monkeypatch):
    _use(monkeypatch, _FakeEngine(ImportError("mlx_whisper gone")))
    assert stt.engine_self_test() is False  # deps died mid-run -> dead


def test_whitespace_only_transcript_is_DEAD(monkeypatch):
    _use(monkeypatch, _FakeEngine("   \n  "))
    assert stt.engine_self_test() is False  # no alphanumerics -> not real output


def test_probe_unavailable_is_INDETERMINATE(monkeypatch):
    # No `say`/`afconvert` on this host -> probe can't be built -> None, and the
    # caller must NOT flag a maybe-healthy engine dead on a hunch.
    _use(monkeypatch, _FakeEngine(""), probe=None)
    assert stt.engine_self_test() is None

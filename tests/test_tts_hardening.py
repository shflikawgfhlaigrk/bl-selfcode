"""TTS lock + boundary hardening.

The machine-wide play lock is the mic's deafness lever: while ANY process holds it,
``is_anything_playing()`` is True and the voice loop drops every mic frame. So the
lock itself must be (a) honest — a held lock reads as "playing"; (b) BOUNDED — a
wedged holder must not block a waiting speaker forever (degraded overlap beats a
permanently mute voice). Plus: the module-level ``speak``/``speak_stream`` boundaries
never raise, whatever the engine does.

All lock tests run against a tmp lockfile (never the live ~/.utah/run lock).
"""
from __future__ import annotations

import fcntl
import logging
import os
import time

from utah.voice import tts as tts_mod


def _own_lockfile(monkeypatch, tmp_path) -> str:
    path = str(tmp_path / "tts-play.lock")
    monkeypatch.setattr(tts_mod, "_PLAY_LOCKFILE", path)
    monkeypatch.setattr(tts_mod, "_CHECK_FD", None)   # drop the cached probe fd
    return path


def test_is_anything_playing_sees_a_foreign_exclusive_holder(monkeypatch, tmp_path):
    path = _own_lockfile(monkeypatch, tmp_path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert tts_mod.is_anything_playing() is False     # free → nobody playing
        fcntl.flock(fd, fcntl.LOCK_EX)                    # another "player" grabs it
        assert tts_mod.is_anything_playing() is True      # held → playing
        fcntl.flock(fd, fcntl.LOCK_UN)
        assert tts_mod.is_anything_playing() is False     # released → free again
    finally:
        os.close(fd)
        monkeypatch.setattr(tts_mod, "_CHECK_FD", None)


def test_play_lock_acquires_and_releases_cleanly(monkeypatch, tmp_path):
    path = _own_lockfile(monkeypatch, tmp_path)
    with tts_mod._system_play_lock():
        probe = os.open(path, os.O_RDWR)
        try:
            try:
                fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
                raise AssertionError("lock was not held exclusively inside the context")
            except OSError:
                pass                                       # exclusive, as required
        finally:
            os.close(probe)
    probe = os.open(path, os.O_RDWR)                       # after: fully released
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe, fcntl.LOCK_UN)
    finally:
        os.close(probe)


def test_play_lock_wait_is_bounded_never_a_deadlock(monkeypatch, tmp_path, caplog):
    """A wedged holder (live root cause of chronic mic_silent) must NOT hang every
    other speaker forever. The wait is bounded: on timeout the speaker proceeds
    UNSERIALIZED (logged) — overlapping audio is recoverable, a deaf/mute voice
    loop is not."""
    path = _own_lockfile(monkeypatch, tmp_path)
    monkeypatch.setattr(tts_mod, "PLAY_LOCK_TIMEOUT_S", 0.3)
    holder = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)                     # wedged foreign holder
    try:
        t0 = time.monotonic()
        entered = False
        with caplog.at_level(logging.WARNING, logger="utah.voice.tts"):
            with tts_mod._system_play_lock():
                entered = True
        waited = time.monotonic() - t0
        assert entered                                     # never deadlocks
        assert 0.2 <= waited < 5.0                         # bounded by the timeout
        assert any("play-lock" in r.message for r in caplog.records)  # degraded loudly
    finally:
        os.close(holder)


def test_play_lock_timeout_is_a_positive_bound():
    assert tts_mod.PLAY_LOCK_TIMEOUT_S > 0
    # and comfortably above one clip's afplay bound, so legitimate queueing
    # (another process mid-sentence) never triggers the degraded path
    assert tts_mod.PLAY_LOCK_TIMEOUT_S >= tts_mod.AFPLAY_TIMEOUT_S


def test_stop_requested_missing_fence_is_false(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_mod, "STOP_FILE", tmp_path / "nope.stop")
    assert tts_mod._stop_requested(time.time() - 10) is False


def test_module_speak_never_raises(monkeypatch):
    class Bomb:
        def speak(self, text):
            raise RuntimeError("piper gone")

        def speak_stream(self, chunks, on_start=None):
            raise RuntimeError("piper gone")

    tts_mod.set_tts(Bomb())
    try:
        tts_mod.speak("hello")                            # logged, not raised
        assert tts_mod.speak_stream(["hello. "]) == ""    # "" on failure, not a crash
    finally:
        tts_mod.set_tts(None)


def test_module_speak_stream_returns_engine_text():
    class Echo:
        def speak_stream(self, chunks, on_start=None):
            return " ".join(chunks)

    tts_mod.set_tts(Echo())
    try:
        assert tts_mod.speak_stream(["a", "b"]) == "a b"
    finally:
        tts_mod.set_tts(None)


def test_drain_sentences_handles_closing_quotes():
    sents, rest = tts_mod._drain_sentences('He said "stop." Then he left. And')
    assert sents == ['He said "stop."', "Then he left."]
    assert rest == " And"

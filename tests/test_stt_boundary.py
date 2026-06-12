"""STT boundary hardening — the paths the existing suites don't pin:

* SubprocessSTT happy path (worker line → text) and dead-worker respawn;
* a JUNK protocol line from the worker resets it (corrupt protocol ≠ a crash);
* the module-level ``transcribe`` NEVER raises, whatever the engine does;
* ``_reap_strangers`` kills orphaned whisper-servers but spares our own child
  and ourselves (the six-servers-on-8090 incident, 2026-06-10).
"""
from __future__ import annotations

import json
import signal
from types import SimpleNamespace

from utah.voice import stt


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, s: str) -> None:
        self.written.append(s)

    def flush(self) -> None:
        pass


def _live_proc():
    return SimpleNamespace(stdin=_FakeStdin(), poll=lambda: None, pid=4242)


def test_subprocess_stt_happy_path_returns_worker_text(monkeypatch):
    s = stt.SubprocessSTT()
    s._proc = _live_proc()
    monkeypatch.setattr(s, "_readline", lambda t: json.dumps({"text": " hello ace \n"}))
    assert s.transcribe("/clip.wav") == "hello ace"
    assert s._proc.stdin.written == ["/clip.wav\n"]   # protocol: one path per line


def test_subprocess_stt_respawns_a_dead_worker_before_writing(monkeypatch):
    s = stt.SubprocessSTT()
    s._proc = SimpleNamespace(stdin=_FakeStdin(), poll=lambda: 1, pid=1)  # exited
    events: list[str] = []

    def spawn():
        events.append("spawn")
        s._proc = _live_proc()

    monkeypatch.setattr(s, "_spawn", spawn)
    monkeypatch.setattr(s, "_readline", lambda t: json.dumps({"text": "back"}))
    assert s.transcribe("/x.wav") == "back"
    assert events == ["spawn"]                        # dead worker → fresh one


def test_junk_worker_line_resets_worker_and_arms_cooldown(monkeypatch):
    """A non-JSON line means the stdin/stdout protocol is corrupt — the only safe
    move is reset (kill + cooldown), never a crash and never a garbage transcript."""
    s = stt.SubprocessSTT()
    s._proc = _live_proc()
    killed = {"n": 0}
    monkeypatch.setattr(s, "_readline", lambda t: "not json at all")
    monkeypatch.setattr(s, "_kill", lambda: killed.__setitem__("n", killed["n"] + 1))
    monkeypatch.setattr(s, "_overloaded", lambda: False)
    monkeypatch.setattr(stt.config, "STT_RESPAWN_COOLDOWN_S", 30.0)

    assert s.transcribe("/x.wav") == ""
    assert killed["n"] == 1
    import time
    assert s._cooldown_until > time.monotonic()       # breaker armed, no respawn storm


def test_module_transcribe_never_raises(monkeypatch):
    class Bomb:
        def transcribe(self, wav_path: str) -> str:
            raise RuntimeError("engine on fire")

    stt.set_stt(Bomb())
    try:
        assert stt.transcribe("/whatever.wav") == ""   # boundary: "" not an exception
    finally:
        stt.set_stt(None)


def test_module_transcribe_uses_injected_engine():
    class Echo:
        def transcribe(self, wav_path: str) -> str:
            return f"heard {wav_path}"

    stt.set_stt(Echo())
    try:
        assert stt.transcribe("/a.wav") == "heard /a.wav"
    finally:
        stt.set_stt(None)


def test_reap_strangers_kills_orphans_spares_self_and_child(monkeypatch):
    s = stt.WhisperCppSTT(bin_path="/fake/bin", model="/fake/model.bin", port=18091)
    s._proc = SimpleNamespace(pid=500, poll=lambda: None)
    killed: list[tuple[int, int]] = []

    monkeypatch.setattr(
        stt.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="500\n777\n888\njunk\n", returncode=0))
    monkeypatch.setattr(stt.os, "getpid", lambda: 888)
    monkeypatch.setattr(stt.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    s._reap_strangers()
    assert killed == [(777, signal.SIGKILL)]          # orphan dies; child 500 + self 888 live


def test_reap_strangers_survives_pgrep_failure(monkeypatch):
    s = stt.WhisperCppSTT(bin_path="/fake/bin", model="/fake/model.bin", port=18092)

    def boom(*a, **k):
        raise OSError("pgrep missing")

    monkeypatch.setattr(stt.subprocess, "run", boom)
    s._reap_strangers()                               # best-effort: never raises


def test_public_surface_exports_the_live_engines():
    """The engines the daemon actually builds must be part of the public surface."""
    for name in ("SubprocessSTT", "WhisperCppSTT", "clean_transcript"):
        assert name in stt.__all__, name

"""STT respawn circuit-breaker — the 2026-06-10 load-storm fix. A hung worker must
NOT respawn (and recompile Metal shaders) on the very next call; under host overload
the cooldown extends, so a wedged worker stops amplifying load instead of feeding it."""
from __future__ import annotations

from utah import config
from utah.voice import stt


def test_hang_sets_cooldown_and_next_call_skips_respawn(monkeypatch):
    s = stt.SubprocessSTT()
    spawns = []
    monkeypatch.setattr(s, "_spawn", lambda: spawns.append(1))
    monkeypatch.setattr(s, "_readline", lambda t: None)   # always "hangs"
    monkeypatch.setattr(s, "_overloaded", lambda: False)
    monkeypatch.setattr(stt.config, "STT_RESPAWN_COOLDOWN_S", 20.0)

    assert s.transcribe("/x.wav") == ""      # spawns once, hangs, kills, sets cooldown
    assert len(spawns) == 1
    assert s.transcribe("/x.wav") == ""      # IN cooldown -> NO respawn, no Metal compile
    assert len(spawns) == 1                  # the amplifier is broken


def test_overload_multiplies_cooldown(monkeypatch):
    s = stt.SubprocessSTT()
    monkeypatch.setattr(s, "_spawn", lambda: None)
    monkeypatch.setattr(s, "_readline", lambda t: None)
    monkeypatch.setattr(s, "_overloaded", lambda: True)
    monkeypatch.setattr(stt.config, "STT_RESPAWN_COOLDOWN_S", 10.0)
    monkeypatch.setattr(stt.config, "STT_RESPAWN_OVERLOAD_MULT", 6.0)

    import time
    t0 = time.monotonic()
    s.transcribe("/x.wav")
    assert s._cooldown_until - t0 >= 55       # ~60s (10*6), not the base 10s


def test_cooldown_expires_then_respawns(monkeypatch):
    s = stt.SubprocessSTT()
    spawns = []
    monkeypatch.setattr(s, "_spawn", lambda: spawns.append(1))
    monkeypatch.setattr(s, "_readline", lambda t: None)
    monkeypatch.setattr(s, "_overloaded", lambda: False)
    s.transcribe("/x.wav")
    s._cooldown_until = 0.0                    # simulate cooldown elapsed
    s.transcribe("/x.wav")
    assert len(spawns) == 2                     # recovers once it's safe again

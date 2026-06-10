"""WhisperCppSTT — the post-MLX STT core (Michael 2026-06-10: voice 'failing on all
levels' → delete and redo the failing layer). whisper.cpp server: C++ Metal, model
loaded once, no Python/MLX deadlock class. These tests pin the contract with an
injected HTTP poster and a fake server lifecycle — zero network, zero processes."""
from __future__ import annotations

from utah.voice import stt


def _mk(monkeypatch, *, post=None, ready=True):
    s = stt.WhisperCppSTT(bin_path="/fake/whisper-server", model="/fake/model.bin",
                          port=18090)
    monkeypatch.setattr(s, "_spawn", lambda: None)
    monkeypatch.setattr(s, "_ready", lambda timeout=None: ready)
    monkeypatch.setattr(s, "_alive", lambda: True)
    monkeypatch.setattr(s, "_kill", lambda: None)
    monkeypatch.setattr(s, "_overloaded", lambda: False)
    if post is not None:
        s._post = post
    return s


def test_transcribe_posts_wav_and_returns_text(monkeypatch, tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFFfake")
    seen = {}

    def post(url, path, timeout):
        seen["url"], seen["path"] = url, path
        return '{"text": " hello ace \\n"}'

    s = _mk(monkeypatch, post=post)
    assert s.transcribe(str(wav)) == "hello ace"
    assert seen["url"].endswith("/inference") and "18090" in seen["url"]


def test_transcribe_failure_arms_cooldown_and_skips_until_expiry(monkeypatch, tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFFfake")
    calls = []

    def post(url, path, timeout):
        calls.append(1)
        raise TimeoutError("server wedged")

    monkeypatch.setattr(stt.config, "STT_RESPAWN_COOLDOWN_S", 20.0)
    s = _mk(monkeypatch, post=post)
    assert s.transcribe(str(wav)) == ""        # wedged -> kill + cooldown, never raises
    assert s.transcribe(str(wav)) == ""        # in cooldown -> no second HTTP call
    assert len(calls) == 1
    s._cooldown_until = 0.0                    # cooldown elapsed -> tries again
    s.transcribe(str(wav))
    assert len(calls) == 2


def test_junk_response_is_empty_not_a_crash(monkeypatch, tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFFfake")
    s = _mk(monkeypatch, post=lambda u, p, t: "not json at all")
    assert s.transcribe(str(wav)) == ""


def test_default_engine_prefers_whispercpp_when_provisioned(monkeypatch, tmp_path):
    binp = tmp_path / "whisper-server"
    binp.write_text("#!/bin/sh\n")
    model = tmp_path / "ggml.bin"
    model.write_bytes(b"m")
    monkeypatch.setattr(stt.config, "STT_ENGINE", "whisper")
    monkeypatch.setattr(stt.config, "WHISPERCPP_BIN", str(binp))
    monkeypatch.setattr(stt.config, "WHISPERCPP_MODEL", str(model))
    eng = stt._build_default_stt()
    assert isinstance(eng, stt.WhisperCppSTT)


def test_default_engine_falls_back_when_unprovisioned(monkeypatch):
    monkeypatch.setattr(stt.config, "STT_ENGINE", "whisper")
    monkeypatch.setattr(stt.config, "WHISPERCPP_BIN", "/nope/whisper-server")
    eng = stt._build_default_stt()
    assert not isinstance(eng, stt.WhisperCppSTT)   # MLX subprocess or moonshine

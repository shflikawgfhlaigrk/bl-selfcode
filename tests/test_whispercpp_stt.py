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


def test_cold_start_retries_through_model_load_then_succeeds(monkeypatch, tmp_path):
    """The 2026-06-18 race: whisper-server accepts the socket BEFORE the model loads, so
    the first /inference POST after a spawn is refused. On a COLD server that's transient —
    retry through it. The command must still transcribe, not come back '' + 20s deaf
    (which read as 'voice didn't hear me')."""
    import urllib.error

    wav = tmp_path / "x.wav"; wav.write_bytes(b"RIFFfake")
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)        # no real backoff wait
    monkeypatch.setattr(stt, "_WHISPERCPP_COLD_RETRIES", 4)
    calls = {"n": 0}

    def post(url, path, timeout):
        calls["n"] += 1
        if calls["n"] < 3:                                       # model still loading
            raise urllib.error.URLError("[Errno 61] Connection refused")
        return '{"text": "hey ace what time is it"}'

    s = _mk(monkeypatch, post=post, ready=True)
    monkeypatch.setattr(s, "_alive", lambda: False)              # COLD — just spawned
    assert s.transcribe(str(wav)) == "hey ace what time is it"
    assert calls["n"] == 3                                        # rode past the refusals
    assert s._cooldown_until == 0.0                              # recovered, no dead-zone


def test_warm_server_refusal_is_one_shot_not_retried(monkeypatch, tmp_path):
    """A refusal from a WARM server is a real fault, not a cold-start race — one attempt,
    then kill + cooldown. Retrying there would mask a genuine wedge."""
    import urllib.error

    wav = tmp_path / "x.wav"; wav.write_bytes(b"RIFFfake")
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)
    monkeypatch.setattr(stt.config, "STT_RESPAWN_COOLDOWN_S", 20.0)
    calls = {"n": 0}

    def post(url, path, timeout):
        calls["n"] += 1
        raise urllib.error.URLError("refused")

    s = _mk(monkeypatch, post=post, ready=True)                 # _alive=True → warm
    assert s.transcribe(str(wav)) == ""
    assert calls["n"] == 1                                        # warm = exactly one shot
    assert s._cooldown_until > 0                                 # cooldown armed


def test_cold_http_status_error_is_not_retried(monkeypatch, tmp_path):
    """An HTTP *status* error means the server responded — real, not a load race — so it
    is not retried even on a cold server (only connection-level refusals are)."""
    import urllib.error

    wav = tmp_path / "x.wav"; wav.write_bytes(b"RIFFfake")
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def post(url, path, timeout):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 500, "boom", {}, None)

    s = _mk(monkeypatch, post=post, ready=True)
    monkeypatch.setattr(s, "_alive", lambda: False)             # cold
    assert s.transcribe(str(wav)) == ""
    assert calls["n"] == 1                                        # HTTPError not retried


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


def test_clean_transcript_kills_non_speech_annotations():
    """Silence must never become a question (live 2026-06-10: Ace answered
    '[BLANK_AUDIO]' as if Michael said it)."""
    assert stt.clean_transcript("[BLANK_AUDIO]") == ""
    assert stt.clean_transcript(" (wind blowing) ") == ""
    assert stt.clean_transcript("[Music]") == ""
    assert stt.clean_transcript("[ Silence ]") == ""
    assert stt.clean_transcript("hello ace [BLANK_AUDIO]") == "hello ace"
    assert stt.clean_transcript("what's the engine status?") == "what's the engine status?"
    assert stt.clean_transcript("") == ""


def test_best_model_prefers_small_en(tmp_path, monkeypatch):
    root = tmp_path / "whisper"
    root.mkdir()
    (root / "ggml-base.en-q5_1.bin").write_bytes(b"b")
    (root / "ggml-small.en-q5_1.bin").write_bytes(b"s")
    monkeypatch.setattr(stt.config, "WHISPERCPP_MODEL", str(root / "ggml-small.en-q5_1.bin"))
    assert "small" in stt._best_whispercpp_model()


def test_stranger_pids_excludes_own_child_and_self():
    """Reap parser for the six-servers-on-8090 incident (2026-06-10): orphaned twins
    are returned for killing; our live child and our own pid survive; junk ignored."""
    from utah.voice.stt import _stranger_pids
    out = "415\n728\n97702\njunk\n97751\n"
    assert _stranger_pids(out, own_pid=415, self_pid=97702) == [728, 97751]
    assert _stranger_pids("", own_pid=1, self_pid=2) == []

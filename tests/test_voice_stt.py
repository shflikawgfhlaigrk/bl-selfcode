"""STT must resolve the Moonshine model from the LOCAL cache — never a network
HEAD to huggingface.co on the mic-muted critical path. A network blip there is a
multi-second *deaf window*; a full outage is dead voice. The guard lives IN CODE
(not just a launchd plist) so it survives `kickstart -k` (which never reapplies
plist env) and ships correctly inside the Sovereign buyer package (no launchd at
all). See utah/voice/stt.py.
"""
from __future__ import annotations

import importlib
import os


def test_importing_stt_forces_hf_offline(monkeypatch):
    # Simulate a process launched WITHOUT the launchd env — the live regression
    # where the supervisor's loaded job def predated the plist's HF_HUB_OFFLINE.
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    import utah.voice.stt as stt
    importlib.reload(stt)  # re-run module top-level with the env unset

    # Importing the STT boundary must have forced offline model resolution, so the
    # lazy `import moonshine_onnx` / huggingface_hub never does a network HEAD.
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_offline_default_yields_to_explicit_override(monkeypatch):
    # An operator who deliberately wants a cold download sets HF_HUB_OFFLINE=0;
    # the in-code guard is a default (setdefault), so it must not clobber that.
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    import utah.voice.stt as stt
    importlib.reload(stt)

    assert os.environ["HF_HUB_OFFLINE"] == "0"            # override preserved
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"      # unset one still defaulted


def test_default_stt_prefers_whisper_for_accuracy(monkeypatch):
    """Moonshine base mis-hears real mic speech ('What's going on?' -> 'Blun.'),
    so the default engine is MLX Whisper when available. STT_ENGINE=moonshine
    forces the portable ONNX fallback."""
    import importlib.util

    import utah.voice.stt as stt
    from utah import config

    monkeypatch.setattr(config, "STT_ENGINE", "moonshine")
    assert isinstance(stt._build_default_stt(), stt.MoonshineSTT)

    monkeypatch.setattr(config, "STT_ENGINE", "whisper")
    # whisper.cpp is the post-MLX core (2026-06-10): when the server binary + a ggml
    # model are provisioned it wins outright; otherwise MLX, then Moonshine.
    if stt._best_whispercpp_model() and __import__("os").path.exists(config.WHISPERCPP_BIN):
        assert isinstance(stt._build_default_stt(), stt.WhisperCppSTT)
    elif importlib.util.find_spec("mlx_whisper") is None:
        assert isinstance(stt._build_default_stt(), stt.MoonshineSTT)
    else:
        # MLX runs in a KILLABLE worker (SubprocessSTT) so a Metal hang can't deafen
        # the mic — not in-process MLXWhisperSTT.
        assert isinstance(stt._build_default_stt(), stt.SubprocessSTT)


def test_subprocess_stt_recovers_from_a_hung_worker(monkeypatch):
    """A wedged worker (MLX/Metal deadlock) must NOT block the mic loop: transcribe
    returns "" after the timeout and kills the worker so the next call respawns fresh.
    This is the fix for the live 'hears once then goes deaf' bug."""
    import utah.voice.stt as stt

    s = stt.SubprocessSTT()
    killed = {"n": 0}

    class _FakeProc:
        stdin = type("_I", (), {"write": lambda self, x: None, "flush": lambda self: None})()

        def poll(self):
            return None  # "alive"

    s._proc = _FakeProc()
    monkeypatch.setattr(s, "_readline", lambda timeout: None)  # never answers = hung
    monkeypatch.setattr(s, "_kill", lambda: killed.__setitem__("n", killed["n"] + 1))

    assert s.transcribe("/x.wav") == ""  # mic-side gets "" instead of hanging forever
    assert killed["n"] == 1  # the wedged worker was killed (next call respawns)


def test_whisper_drops_silence_hallucination_keeps_speech(monkeypatch):
    """A high-no_speech segment (Whisper's 'Thank you.' on a near-silent/fragment
    clip, measured 0.505) is dropped → "" (no phantom command); real speech
    (no_speech 0.136) is kept."""
    import sys
    import types

    import utah.voice.stt as stt
    from utah import config

    monkeypatch.setattr(config, "STT_MAX_NO_SPEECH", 0.4)

    def faked(result):
        monkeypatch.setitem(
            sys.modules, "mlx_whisper",
            types.SimpleNamespace(transcribe=lambda wav, path_or_hf_repo=None: result),
        )

    faked({"text": " Thank you.",
           "segments": [{"text": " Thank you.", "no_speech_prob": 0.505}]})
    assert stt.MLXWhisperSTT("m").transcribe("/x.wav") == ""  # hallucination rejected

    faked({"text": " What's going on?",
           "segments": [{"text": " What's going on?", "no_speech_prob": 0.136}]})
    assert stt.MLXWhisperSTT("m").transcribe("/x.wav") == "What's going on?"  # speech kept

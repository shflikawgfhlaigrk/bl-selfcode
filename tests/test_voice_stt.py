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


def test_flag_dead_skips_engine_then_recovers_after_cooldown(monkeypatch):
    """The 2026-06-18 silent-deaf root cause: the STT engine is chosen ONCE at boot and
    never re-evaluated, so when its deps die mid-run (mlx_whisper went unimportable after
    a healthy boot) every command transcribes to "" forever with no recovery. flag_dead
    must (a) clear the memoized engine so get_stt rebuilds, (b) make re-selection SKIP the
    dead engine and fall through to the next AVAILABLE one, and (c) let it recover once the
    cooldown lapses (a transient wedge, not a structural death)."""
    import importlib.util as iu

    import utah.voice.stt as stt
    from utah import config

    # Both whisper.cpp AND mlx "available" so there is a real fallback to fall to.
    monkeypatch.setattr(config, "STT_ENGINE", "whisper")
    monkeypatch.setattr(stt.os.path, "exists", lambda p: True)
    monkeypatch.setattr(stt, "_best_whispercpp_model", lambda: "/m/ggml-small.en.bin")
    monkeypatch.setattr(iu, "find_spec",
                        lambda name: object() if name == "mlx_whisper" else None)
    now = {"t": 1000.0}
    monkeypatch.setattr(stt.time, "monotonic", lambda: now["t"])
    stt._dead_until.clear()
    stt.set_stt(None)

    assert isinstance(stt._build_default_stt(), stt.WhisperCppSTT)  # primary wins

    stt.set_stt(stt.WhisperCppSTT(model="/m"))   # pretend this is the live engine
    stt.flag_dead("WhisperCppSTT")   # the loop flags by class name (type(engine).__name__)                  # the loop saw loud speech -> "" repeatedly
    assert stt._stt is None                       # cache cleared → next get_stt rebuilds

    # re-selection SKIPS the dead whisper.cpp and lands on the next available engine
    assert isinstance(stt._build_default_stt(), stt.SubprocessSTT)

    now["t"] += stt._DEAD_COOLDOWN_S + 1           # cooldown lapses
    assert isinstance(stt._build_default_stt(), stt.WhisperCppSTT)  # eligible again
    stt._dead_until.clear()


def test_all_engines_dead_still_returns_one_never_silent(monkeypatch):
    """If every available engine is flagged dead we still return the first constructible
    one (best effort) and log loudly — the loop keeps trying, never silently returns
    None/crashes the mic thread."""
    import importlib.util as iu

    import utah.voice.stt as stt
    from utah import config

    monkeypatch.setattr(config, "STT_ENGINE", "whisper")
    monkeypatch.setattr(stt.os.path, "exists", lambda p: True)
    monkeypatch.setattr(stt, "_best_whispercpp_model", lambda: "/m/ggml.bin")
    monkeypatch.setattr(iu, "find_spec", lambda name: None)  # only whisper.cpp available
    monkeypatch.setattr(stt.time, "monotonic", lambda: 5000.0)
    stt._dead_until.clear()

    stt.flag_dead("WhisperCppSTT")   # the loop flags by class name (type(engine).__name__)
    assert isinstance(stt._build_default_stt(), stt.WhisperCppSTT)  # returned anyway
    stt._dead_until.clear()


def test_apple_stt_resolution(monkeypatch):
    import utah.voice.stt as stt
    from utah import config

    monkeypatch.setattr(config, "STT_ENGINE", "apple")
    assert isinstance(stt._build_default_stt(), stt.AppleSTT)


def test_apple_stt_transcribes_via_cli(monkeypatch, tmp_path):
    import utah.voice.stt as stt

    bin_path = tmp_path / "apple_stt"
    bin_path.write_text("#!/bin/sh\necho 'Hello world'\n")
    bin_path.chmod(0o755)

    eng = stt.AppleSTT(bin_path=str(bin_path))
    assert eng.transcribe("/x.wav") == "Hello world"


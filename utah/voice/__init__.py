"""Utah's voice stack — the always-on mic pipeline and its TTS/state surface.

The capture path (``loop``) runs: openWakeWord arms (``oww``) → Silero VAD
segments (``vad``) → Moonshine/Whisper STT (``stt``/``stt_worker``) → wake/text
gate (``wake``) → the SAME brain pipeline the chat box uses (``agent``) → Piper
TTS (``tts``), with barge-in (``barge``) so a real interruption cuts playback.
``state`` is the heartbeat file the deck's ``/voice`` route reads; ``liveness``
is the raw-signal deaf-mic detector; ``macapp`` builds the signed
``com.utah.voice`` bundle that gives the launchd child a TCC mic grant.

Submodules are loaded LAZILY (PEP 562): the web layer imports
``utah.voice.state`` on every ``/voice`` request and must not pay for numpy/
onnx/audio imports it never uses. ``from utah.voice import tts`` and
``utah.voice.tts`` both work; only the touched module is imported.
"""
from __future__ import annotations

import importlib

__all__ = [
    "agent",
    "barge",
    "barge_control",
    "liveness",
    "loop",
    "macapp",
    "oww",
    "state",
    "stt",
    "stt_worker",
    "tts",
    "vad",
    "wake",
]


def __getattr__(name: str):
    """Lazy submodule access — ``utah.voice.state`` without importing the rest."""
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)

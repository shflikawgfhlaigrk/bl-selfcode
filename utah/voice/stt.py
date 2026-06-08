"""STT boundary — speech (a WAV file) -> text. Default = Moonshine ONNX
(very-low-latency, on-device). MLX Whisper is the swappable fallback (same
boundary; ``set_stt`` to switch). Injectable for tests; degrades to "" on any
error so the voice loop never crashes on a bad clip.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

from utah import config

# ── Offline model resolution (deaf-window fix) ──────────────────────────────
# Moonshine STT runs on the mic-muted critical path. moonshine_onnx resolves its
# weights through huggingface_hub, which fires a network HEAD to huggingface.co on
# EVERY transcribe unless told to stay local — so a network blip is a multi-second
# deaf window and a full outage is dead voice. This MUST be enforced in code, not
# only via a launchd plist: `kickstart -k` never re-reads plist env, and the shipped
# Sovereign buyer package has no launchd at all. setdefault so a deliberate cold
# download (HF_HUB_OFFLINE=0) still wins. Runs before the lazy `import moonshine_onnx`.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

log = logging.getLogger("utah.voice.stt")


class STT(Protocol):
    def transcribe(self, wav_path: str) -> str: ...


class MoonshineSTT:
    """Moonshine ONNX — the default. Very low latency; model auto-downloads once."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or config.STT_MODEL

    def transcribe(self, wav_path: str) -> str:
        import moonshine_onnx

        out = moonshine_onnx.transcribe(wav_path, self._model)
        if isinstance(out, (list, tuple)):
            return " ".join(str(x) for x in out).strip()
        return str(out).strip()


class MLXWhisperSTT:
    """On-device MLX Whisper (swappable fallback). Model auto-downloads once."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or config.WHISPER_MODEL

    def transcribe(self, wav_path: str) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(wav_path, path_or_hf_repo=self._model)
        return (result.get("text") or "").strip()


_stt: STT | None = None


def get_stt() -> STT:
    global _stt
    if _stt is None:
        _stt = MoonshineSTT()
    return _stt


def set_stt(stt: STT | None) -> None:
    global _stt
    _stt = stt


def transcribe(wav_path: str) -> str:
    """Transcribe a WAV to text; "" on any failure (logged, never raised)."""
    try:
        return get_stt().transcribe(wav_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("STT failed (%s): %s", wav_path, exc)
        return ""


__all__ = ["STT", "MoonshineSTT", "MLXWhisperSTT", "get_stt", "set_stt", "transcribe"]

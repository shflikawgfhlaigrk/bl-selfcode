"""STT boundary — speech (a WAV file) -> text. Default = MLX Whisper (on-device,
auto-downloads its model on first use). Moonshine drops into this SAME boundary
later (swap the engine; nothing else changes). Injectable for tests; degrades to
"" on any error so the voice loop never crashes on a bad clip.
"""
from __future__ import annotations

import logging
from typing import Protocol

from utah import config

log = logging.getLogger("utah.voice.stt")


class STT(Protocol):
    def transcribe(self, wav_path: str) -> str: ...


class MLXWhisperSTT:
    """On-device MLX Whisper. The model auto-downloads on first transcribe."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or config.STT_MODEL

    def transcribe(self, wav_path: str) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(wav_path, path_or_hf_repo=self._model)
        return (result.get("text") or "").strip()


_stt: STT | None = None


def get_stt() -> STT:
    global _stt
    if _stt is None:
        _stt = MLXWhisperSTT()
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


__all__ = ["STT", "MLXWhisperSTT", "get_stt", "set_stt", "transcribe"]

"""TTS boundary — speak text aloud (Piper). Injectable; degrades (logs, no-op) if
the engine/model/audio device is missing so the voice loop never crashes. F5-TTS/
StyleTTS2 (natural voice) drop into this same boundary later.
"""
from __future__ import annotations

import logging
import threading
import wave

from utah import config

log = logging.getLogger("utah.voice.tts")


class TTS:
    def speak(self, text: str) -> None: ...  # pragma: no cover
    def synth_wav(self, text: str, path: str) -> None: ...  # pragma: no cover


class PiperTTS:
    """Piper voice (lazy-loaded). ``speak`` plays via sounddevice; ``synth_wav``
    writes a WAV (used by the offline round-trip proof)."""

    def __init__(self, model_path: str | None = None) -> None:
        self._model_path = model_path or config.PIPER_MODEL
        self._voice = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._voice is None:
                from piper import PiperVoice

                self._voice = PiperVoice.load(self._model_path, self._model_path + ".json")
            return self._voice

    def synth_wav(self, text: str, path: str) -> None:
        voice = self._load()
        with wave.open(path, "wb") as wf:
            voice.synthesize_wav(text, wf)

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        voice = self._load()
        import numpy as np
        import sounddevice as sd

        chunks = [c.audio_int16_array for c in voice.synthesize(text)]
        if not chunks:
            return
        sd.play(np.concatenate(chunks), voice.config.sample_rate)
        sd.wait()


_tts: TTS | None = None


def get_tts() -> TTS:
    global _tts
    if _tts is None:
        _tts = PiperTTS()
    return _tts


def set_tts(tts: TTS | None) -> None:
    global _tts
    _tts = tts


def speak(text: str) -> None:
    """Speak text aloud; never raises (logs + no-ops on any failure)."""
    try:
        get_tts().speak(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("TTS speak failed: %s", exc)


__all__ = ["TTS", "PiperTTS", "get_tts", "set_tts", "speak"]

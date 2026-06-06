"""TTS boundary — speak text aloud (Piper). Injectable; degrades (logs, no-op) if
the engine/model/audio device is missing so the voice loop never crashes. F5-TTS/
StyleTTS2 (natural voice) drop into this same boundary later.

``speak`` synthesizes the PROVEN-clean WAV (the same ``synthesize_wav`` bytes the
round-trip proof checks) and plays it through the macOS reference player (``afplay``).
The old ``sd.play(int16_array)`` path was the static the live test surfaced: a
module-global stream that's fragile on dtype, on output-device selection (it followed
the PortAudio default, not the system output), and — fatally — on concurrency, since
voice and the chat-speak thread share that one stream and corrupt each other. afplay
fed a clean WAV, under a global lock, removes all three failure modes at once.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import wave

from utah import config

log = logging.getLogger("utah.voice.tts")

# Serialize ALL playback (voice loop + chat-speak thread): two players running at
# once overlap into garble. One voice at a time, process-wide.
_PLAY_LOCK = threading.Lock()


def _afplay(path: str) -> None:
    """Play a WAV via the macOS reference player — honours the system default output
    device and handles the sample rate itself (no dtype/rate fragility)."""
    subprocess.run(["afplay", path], check=True)


class TTS:
    def speak(self, text: str) -> None: ...  # pragma: no cover
    def synth_wav(self, text: str, path: str) -> None: ...  # pragma: no cover


class PiperTTS:
    """Piper voice (lazy-loaded). ``synth_wav`` writes a WAV; ``speak`` synthesizes
    that same clean WAV and plays it via ``player`` (default: macOS ``afplay``).
    ``player`` is injectable so tests prove the wiring without blasting audio."""

    def __init__(self, model_path: str | None = None, player=None) -> None:
        self._model_path = model_path or config.PIPER_MODEL
        self._voice = None
        self._lock = threading.Lock()
        self._player = player or _afplay

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
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="utah_tts_")
        os.close(fd)
        try:
            self.synth_wav(text, path)       # PROVEN-clean bytes (round-trip path)
            with _PLAY_LOCK:                 # one voice at a time, process-wide
                self._player(path)           # macOS reference player
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


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

"""Voice-activity detection — neural (Silero), so the mic captures real SPEECH and
ignores music / room noise. The old energy VAD could not tell music from speech, so a
room with audio produced 15s garbage blobs that the STT turned into hallucinated lyrics
and the wake word never fired. Silero scores P(speech) per 32ms frame (speech ~0.9,
music/noise ~0.01); the pure :class:`Segmenter` turns that into clean speech segments.

The model is an injectable boundary (:func:`set_vad`) so tests never load the ONNX model;
the :class:`Segmenter` is pure and fully unit-tested.
"""
from __future__ import annotations

import logging
from collections import deque

log = logging.getLogger("utah.voice.vad")

SAMPLE_RATE = 16_000
FRAME = 512                 # Silero's required window @ 16kHz = 32 ms
FRAME_BYTES = FRAME * 2     # int16
SPEECH_THRESHOLD = 0.5      # speech ~0.9, music/noise ~0.01 (measured) — clean margin


class VAD:
    def prob(self, frame: bytes) -> float: ...   # pragma: no cover
    def reset(self) -> None: ...                  # pragma: no cover


class SileroVAD:
    """Silero VAD (ONNX, lazy-loaded). ``prob`` returns P(speech) for one 512-sample
    (32 ms) int16 PCM frame; ``reset`` clears the model's recurrent state between
    utterances so one segment's tail never bleeds into the next."""

    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            from silero_vad import load_silero_vad

            self._model = load_silero_vad(onnx=True)
        return self._model

    def prob(self, frame: bytes) -> float:
        import numpy as np
        import torch

        model = self._load()
        a = np.frombuffer(frame, dtype="int16").astype("float32") / 32768.0
        if a.shape[0] != FRAME:  # Silero needs exactly FRAME samples
            if a.shape[0] < FRAME:
                a = np.pad(a, (0, FRAME - a.shape[0]))
            else:
                a = a[:FRAME]
        return float(model(torch.from_numpy(a), SAMPLE_RATE).item())

    def reset(self) -> None:
        if self._model is not None and hasattr(self._model, "reset_states"):
            self._model.reset_states()


class Segmenter:
    """Pure onset/offset segmenter over per-frame speech booleans.

    Starts capturing after ``onset`` consecutive speech frames (so a lone noise spike
    never starts a turn), keeps a short ``preroll`` so the first phoneme isn't clipped,
    and ends the segment after ``offset`` consecutive non-speech frames — or at the
    ``max_frames`` hard cap. ``feed(frame, is_speech)`` returns the completed segment
    bytes once, then None until the next segment. Frame size is the caller's (512)."""

    def __init__(self, onset: int = 3, offset: int = 20, max_frames: int = 300,
                 preroll: int = 6) -> None:
        self.onset = onset
        self.offset = offset
        self.max_bytes = max_frames * FRAME_BYTES
        self._pre: deque[bytes] = deque(maxlen=preroll)
        self._buf = bytearray()
        self._capturing = False
        self._run_speech = 0
        self._run_silence = 0

    def reset(self) -> None:
        self._pre.clear()
        self._buf = bytearray()
        self._capturing = False
        self._run_speech = 0
        self._run_silence = 0

    def arm(self) -> None:
        """Start capturing immediately (after an audio wake hit) using preroll."""
        if not self._capturing:
            self._capturing = True
            self._buf = bytearray(b"".join(self._pre))
            self._run_silence = 0
            self._run_speech = self.onset

    def feed(self, frame: bytes, is_speech: bool) -> bytes | None:
        if not self._capturing:
            self._pre.append(frame)
            self._run_speech = self._run_speech + 1 if is_speech else 0
            if self._run_speech >= self.onset:
                self._capturing = True
                self._buf = bytearray(b"".join(self._pre))
                self._run_silence = 0
            return None
        self._buf += frame
        self._run_silence = 0 if is_speech else self._run_silence + 1
        if self._run_silence >= self.offset or len(self._buf) >= self.max_bytes:
            seg = bytes(self._buf)
            self.reset()
            return seg
        return None


_vad: VAD | None = None


def get_vad() -> VAD:
    global _vad
    if _vad is None:
        _vad = SileroVAD()
    return _vad


def set_vad(vad: VAD | None) -> None:
    global _vad
    _vad = vad


__all__ = ["VAD", "SileroVAD", "Segmenter", "get_vad", "set_vad",
           "FRAME", "FRAME_BYTES", "SAMPLE_RATE", "SPEECH_THRESHOLD"]

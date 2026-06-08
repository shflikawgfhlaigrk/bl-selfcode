"""Audio-level wake arming via openWakeWord.

Stage A of the two-stage wake path: ONNX scores "hey ace" on the live mic and
*arms* command capture. It never opens a brain turn by itself (the broadband
``hey_ace.onnx`` false-fired on TV/coughs when OR-gated with STT — see ace audit).
Stage B is :func:`utah.voice.wake.resolve_command` on the STT transcript.

Injectable ``model_factory`` keeps unit tests native-dep-free.
"""
from __future__ import annotations

import importlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from utah import config

log = logging.getLogger("utah.voice.oww")

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 1_280  # 80 ms @ 16 kHz — openWakeWord native chunk


class _ModelLike(Protocol):
    def predict(self, chunk: np.ndarray) -> dict[str, float]:  # pragma: no cover
        ...


@dataclass(frozen=True)
class Detection:
    keyword: str
    confidence: float
    sample_index: int


def _import_openwakeword():  # type: ignore[no-untyped-def]
    try:
        return importlib.import_module("openwakeword.model")
    except Exception as exc:  # noqa: BLE001
        log.debug("openwakeword import failed: %s", exc)
        return None


def _bootstrap_oww_resources() -> bool:
    """One-time fetch of melspectrogram/embedding ONNX (not bundled in pip wheel)."""
    try:
        import openwakeword
        import openwakeword.utils as ou

        target = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        need = [target / "melspectrogram.onnx", target / "embedding_model.onnx"]
        if all(p.is_file() for p in need):
            return True
        target.mkdir(parents=True, exist_ok=True)
        for feature in openwakeword.FEATURE_MODELS.values():
            for url in (feature["download_url"], feature["download_url"].replace(".tflite", ".onnx")):
                dest = target / url.rsplit("/", 1)[-1]
                if not dest.is_file():
                    ou.download_file(url, str(target))
        return all(p.is_file() for p in need)
    except Exception as exc:  # noqa: BLE001
        log.warning("openwakeword resource download failed: %s", exc)
        return False


def _ensure_wake_model(path: str) -> str | None:
    """Return a loadable model path, seeding ~/.utah from legacy ~/.ace if needed."""
    p = Path(path).expanduser()
    if p.is_file():
        return str(p)
    legacy = Path.home() / ".ace" / "models" / "wake" / p.name
    if legacy.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(legacy, p)
            log.info("seeded wake model %s from %s", p, legacy)
            return str(p)
        except OSError as exc:
            log.warning("could not seed wake model: %s", exc)
    # last resort: any hey_ace variant in legacy tree
    wake_dir = Path.home() / ".ace" / "models" / "wake"
    for name in ("hey_ace_v3.onnx", "hey_ace.onnx"):
        src = wake_dir / name
        if src.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, p)
                log.info("seeded wake model %s from %s", p, src)
                return str(p)
            except OSError:
                pass
    return None


class OpenWakeWord:
    def __init__(
        self,
        *,
        model_path: str | None = None,
        threshold: float | None = None,
        refractory_chunks: int = 6,
        model_factory: Callable[[], _ModelLike] | None = None,
    ) -> None:
        self._model_path = model_path or config.WAKE_MODEL
        self.threshold = float(threshold if threshold is not None else config.WAKE_THRESHOLD)
        self.refractory_chunks = int(refractory_chunks)
        self._model_factory = model_factory
        self._model: _ModelLike | None = None
        self._buf = np.zeros(0, dtype=np.int16)
        self._chunks_seen = 0
        self._refractory_until = -1

    def load(self) -> bool:
        if self._model is not None:
            return True
        if self._model_factory is not None:
            self._model = self._model_factory()
            return True
        resolved = _ensure_wake_model(self._model_path)
        if not resolved:
            log.warning("wake model missing at %s — audio arming disabled", self._model_path)
            return False
        mod = _import_openwakeword()
        if mod is None:
            log.warning("openwakeword not installed — audio arming disabled")
            return False
        _bootstrap_oww_resources()
        try:
            self._model = mod.Model(  # type: ignore[attr-defined]
                wakeword_models=[resolved],
                inference_framework="onnx",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("openwakeword load failed: %s — audio arming disabled", exc)
            return False
        log.info("audio wake armed (model=%s threshold=%.2f)", resolved, self.threshold)
        return True

    @property
    def available(self) -> bool:
        return self._model is not None

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.int16)
        self._chunks_seen = 0
        self._refractory_until = -1
        if self._model is not None and hasattr(self._model, "reset"):
            try:
                self._model.reset()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    def feed(self, frame: bytes) -> list[Detection]:
        """Score one 32 ms int16 frame; returns 0+ detections when a chunk completes."""
        if self._model is None:
            return []
        arr = np.frombuffer(frame, dtype=np.int16)
        self._buf = np.concatenate([self._buf, arr]) if self._buf.size else arr
        hits: list[Detection] = []
        while self._buf.size >= CHUNK_SAMPLES:
            chunk = self._buf[:CHUNK_SAMPLES]
            self._buf = self._buf[CHUNK_SAMPLES:]
            idx = self._chunks_seen * CHUNK_SAMPLES
            self._chunks_seen += 1
            scores = self._model.predict(chunk) or {}
            chunk_hits = [
                Detection(keyword=k, confidence=float(c), sample_index=idx)
                for k, c in scores.items()
                if float(c) >= self.threshold
            ]
            if not chunk_hits:
                continue
            chunk_idx = self._chunks_seen - 1
            if chunk_idx < self._refractory_until:
                continue
            best = max(chunk_hits, key=lambda d: d.confidence)
            self._refractory_until = chunk_idx + 1 + self.refractory_chunks
            hits.append(best)
        return hits


_oww: OpenWakeWord | None = None


def get_oww() -> OpenWakeWord | None:
    """Lazy singleton; ``None`` when model/deps unavailable (text-only fallback)."""
    global _oww
    if _oww is None:
        oww = OpenWakeWord()
        if not oww.load():
            return None
        _oww = oww
    return _oww


def set_oww(oww: OpenWakeWord | None) -> None:
    global _oww
    _oww = oww


__all__ = [
    "CHUNK_SAMPLES",
    "Detection",
    "OpenWakeWord",
    "SAMPLE_RATE",
    "get_oww",
    "set_oww",
]

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
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from utah import config

log = logging.getLogger("utah.voice.oww")

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 1_280  # 80 ms @ 16 kHz — openWakeWord native chunk

#: Bound for the one-time openWakeWord resource fetch. ``ou.download_file`` takes
#: no timeout parameter, so the fetch runs under the socket default timeout (set
#: around the downloads, restored after) — a hung CDN must not wedge the voice
#: loop's startup forever.
_FETCH_TIMEOUT_S = float(os.environ.get("UTAH_OWW_FETCH_TIMEOUT_S", "30"))


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
    """One-time fetch of melspectrogram/embedding ONNX (not bundled in pip wheel).

    Honest + bounded: ``True`` only when the required files actually exist on
    disk afterwards; one dead URL never aborts the remaining downloads; every
    download runs under the socket default timeout (restored after)."""
    try:
        import openwakeword
        import openwakeword.utils as ou

        target = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        need = [target / "melspectrogram.onnx", target / "embedding_model.onnx"]
        if all(p.is_file() for p in need):
            return True
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # download_file may create it itself; the final existence check decides.
            log.debug("oww resource dir mkdir failed: %s", exc)
        prev = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_FETCH_TIMEOUT_S)
        try:
            for feature in openwakeword.FEATURE_MODELS.values():
                for url in (feature["download_url"],
                            feature["download_url"].replace(".tflite", ".onnx")):
                    dest = target / url.rsplit("/", 1)[-1]
                    if dest.is_file():
                        continue
                    try:
                        ou.download_file(url, str(target))
                    except Exception as exc:  # noqa: BLE001 — one dead URL must not abort the rest
                        log.warning("openwakeword resource %s failed: %s", url, exc)
        finally:
            socket.setdefaulttimeout(prev)
        ok = all(p.is_file() for p in need)
        if not ok:
            log.warning("openwakeword resources incomplete under %s — audio arming "
                        "will stay disabled until the fetch succeeds", target)
        return ok
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
            try:
                self._model = self._model_factory()
            except Exception as exc:  # noqa: BLE001 — a blown factory must not kill voice startup
                log.warning("wake model factory failed: %s — audio arming disabled", exc)
                return False
            return self._model is not None
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
        if len(frame) % 2:
            # torn frame from a glitching stream — drop the dangling byte; raising
            # here would bubble into run()'s retry loop and bounce the whole mic.
            frame = frame[:-1]
        if not frame:
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
            # Surface near-wake scores below threshold at DEBUG — invaluable for tuning the
            # threshold to a given voice/mic without guessing (2026-06-19: this is how the
            # 0.82 threshold was found to be silently rejecting Michael's 0.71-0.74 "hey ace").
            if scores and log.isEnabledFor(logging.DEBUG):
                _bk = max(scores, key=lambda k: scores[k])
                if float(scores[_bk]) > 0.3:
                    log.debug("oww raw score: %s=%.2f (threshold=%.2f)",
                              _bk, float(scores[_bk]), self.threshold)
            chunk_hits = []
            for k, c in scores.items():
                try:
                    conf = float(c)
                except (TypeError, ValueError):
                    continue   # a misbehaving model's garbage score is not a detection
                if conf >= self.threshold:   # NaN fails this comparison by design
                    chunk_hits.append(
                        Detection(keyword=k, confidence=conf, sample_index=idx))
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

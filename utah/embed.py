"""Embeddings — an injectable boundary. Real model in prod, fakes in tests.

Production: fastembed (onnxruntime, no torch) BGE-small-en-v1.5, 384-dim, free.
The embedder is process-global and swappable via :func:`set_embedder`, so tests
(and any future model change) never touch callers. Every failure surfaces as
:class:`EmbedError` — callers degrade (sparse-only recall, skip the write),
they never crash the loop and never store an unembedded row.
"""
from __future__ import annotations

import math
import threading
from typing import Protocol

from utah import UtahError, config


class EmbedError(UtahError):
    """Embedding failed (model missing, model error, or bad output)."""


class Embedder(Protocol):
    """Anything that maps one text to one fixed-dimension float vector."""

    def embed(self, text: str) -> list[float]:  # pragma: no cover - protocol
        ...


class FastEmbedEmbedder:
    """Default production embedder: fastembed ONNX BGE-small (lazy-loaded)."""

    def __init__(self, model_name: str = config.EMBED_MODEL) -> None:
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding  # heavy: import lazily
                except ImportError as exc:
                    raise EmbedError(
                        f"fastembed is not installed (needed for {self._model_name})"
                    ) from exc
                try:
                    self._model = TextEmbedding(model_name=self._model_name)
                except Exception as exc:  # model download/init failure
                    raise EmbedError(
                        f"could not load embedding model {self._model_name}: {exc}"
                    ) from exc
            return self._model

    def embed(self, text: str) -> list[float]:
        model = self._load()
        try:
            vector = next(model.embed([text])).tolist()
        except StopIteration as exc:
            raise EmbedError("embedding model returned no vector") from exc
        except Exception as exc:
            raise EmbedError(f"embedding failed: {exc}") from exc
        return [float(x) for x in vector]


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """Return the process-global embedder, creating the default lazily."""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = FastEmbedEmbedder()
        return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Inject an embedder (tests / model swaps). ``None`` restores the default."""
    global _embedder
    with _embedder_lock:
        _embedder = embedder


def embed(text: str) -> list[float]:
    """Embed *text*, validating the result.

    Raises:
        EmbedError: empty input, model failure, wrong dimension, or
            non-finite values — a row is never stored with a bad vector.
    """
    if not text or not text.strip():
        raise EmbedError("cannot embed empty text")
    try:
        vector = get_embedder().embed(text)
    except EmbedError:
        raise
    except Exception as exc:  # any injected/odd embedder failure is still EmbedError
        raise EmbedError(f"embedder raised: {exc}") from exc
    if len(vector) != config.EMBED_DIM:
        raise EmbedError(
            f"embedder returned dim {len(vector)}, expected {config.EMBED_DIM}"
        )
    if not all(math.isfinite(x) for x in vector):
        raise EmbedError("embedder returned non-finite values")
    return vector

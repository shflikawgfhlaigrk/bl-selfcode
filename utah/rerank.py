"""Cross-encoder rerank — an injectable boundary that degrades, never breaks.

Production: fastembed ONNX ms-marco MiniLM (free, no torch). If the reranker
cannot run (model missing, model error), recall must still work, so
:func:`rerank` falls back to all-zero scores — ranking then rests on RRF order
(the sort in recall is stable) plus the entity boost. The fallback is logged,
never silent, and never raises into the recall path.
"""
from __future__ import annotations

import logging
import threading
from typing import Protocol, Sequence

from utah import config

log = logging.getLogger("utah.rerank")


class Reranker(Protocol):
    """Anything that scores (query, doc) relevance, one float per doc."""

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]:  # pragma: no cover
        ...


class FastEmbedReranker:
    """Default production reranker (lazy-loaded fastembed cross-encoder)."""

    def __init__(
        self,
        model_name: str = config.RERANK_MODEL,
        cache_dir: str = config.RERANK_CACHE_DIR,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                import os

                from fastembed.rerank.cross_encoder import TextCrossEncoder

                os.makedirs(self._cache_dir, exist_ok=True)  # durable, never temp
                self._model = TextCrossEncoder(
                    model_name=self._model_name, cache_dir=self._cache_dir
                )
            return self._model

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]:
        return [float(s) for s in self._load().rerank(query, list(docs))]


_reranker: Reranker | None = None
_reranker_lock = threading.Lock()


def get_reranker() -> Reranker:
    """Return the process-global reranker, creating the default lazily."""
    global _reranker
    with _reranker_lock:
        if _reranker is None:
            _reranker = FastEmbedReranker()
        return _reranker


def set_reranker(reranker: Reranker | None) -> None:
    """Inject a reranker (tests / model swaps). ``None`` restores the default."""
    global _reranker
    with _reranker_lock:
        _reranker = reranker


def rerank(query: str, docs: Sequence[str]) -> list[float]:
    """Relevance score per doc (higher = more relevant). Length == len(docs).

    Degrades to all-zero scores (with a logged warning) when the model is
    unavailable or returns a malformed result — recall must never die here.
    """
    if not docs:
        return []
    try:
        scores = get_reranker().rerank(query, docs)
    except Exception as exc:
        log.warning("reranker unavailable, falling back to fusion order: %s", exc)
        return [0.0] * len(docs)
    if len(scores) != len(docs):
        log.warning(
            "reranker returned %d scores for %d docs; falling back", len(scores), len(docs)
        )
        return [0.0] * len(docs)
    return [float(s) for s in scores]

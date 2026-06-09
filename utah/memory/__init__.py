"""Utah memory — grounded compounding memory on Postgres + pgvector.

Hybrid recall (dense HNSW + Postgres FTS, RRF-fused) -> cross-encoder rerank ->
entity boost -> no-fabrication answer gate. Writes pass one admission pipeline.

Layering:

* :mod:`utah.memory.logic` — pure decision functions, no I/O
* :mod:`utah.memory.store` — Postgres backend
* :mod:`utah.memory.pipeline` — store/recall/answer orchestration
"""
from utah.memory.exceptions import AdmissionDenied, MemoryUnavailable
from utah.memory.logic import (
    compute_decay,
    content_words,
    decide_write,
    entity_grounds,
    lexical_overlap,
    passes_gate,
    recall_pool,
    rrf_fuse,
    should_archive,
)
from utah.memory.pipeline import (
    answer,
    close,
    core_recall,
    decay,
    get_backend,
    init,
    list_entities,
    list_memories,
    recall,
    reset,
    set_backend,
    store,
)
from utah.memory.store import PostgresStore
from utah.memory.types import DenseRow, Neighbor, SparseRow, StoreBackend
from utah.objects import WriteAction, WriteDecision, WriteResult

__all__ = [
    "AdmissionDenied",
    "DenseRow",
    "MemoryUnavailable",
    "Neighbor",
    "PostgresStore",
    "SparseRow",
    "StoreBackend",
    "answer",
    "close",
    "compute_decay",
    "content_words",
    "core_recall",
    "decay",
    "decide_write",
    "entity_grounds",
    "get_backend",
    "init",
    "lexical_overlap",
    "list_entities",
    "list_memories",
    "passes_gate",
    "recall",
    "recall_pool",
    "reset",
    "rrf_fuse",
    "set_backend",
    "should_archive",
    "store",
    "WriteAction",
    "WriteDecision",
    "WriteResult",
]

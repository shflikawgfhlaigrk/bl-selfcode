"""Deterministic fakes for the injectable boundaries (embed, rerank, brain, store).

The FakeStore implements the full :class:`utah.memory.StoreBackend` protocol in
pure Python so the entire pipeline (admission, dedup, supersede, recall fusion,
decay) runs the SAME orchestration code as production — only the storage and
the models are swapped.
"""
from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from utah import config, entities
from utah.brain import BrainUnavailable
from utah.memory import (
    DenseRow,
    MemoryUnavailable,
    Neighbor,
    SparseRow,
    compute_decay,
    content_words,
    should_archive,
)

DIM = config.EMBED_DIM


# --- vector helpers -----------------------------------------------------------

def basis(i: int) -> list[float]:
    """Unit basis vector along axis *i*."""
    v = [0.0] * DIM
    v[i] = 1.0
    return v


def blend(a: Sequence[float], b: Sequence[float], cos: float) -> list[float]:
    """Unit vector with cosine *cos* to unit vector *a* (using orthogonal *b*)."""
    s = math.sqrt(max(0.0, 1.0 - cos * cos))
    return [cos * x + s * y for x, y in zip(a, b)]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def _hash_vector(text: str) -> list[float]:
    """Deterministic pseudo-random unit vector: same text -> same vector,
    different texts -> near-orthogonal (no accidental dedup/supersede)."""
    out: list[float] = []
    seed = text.encode("utf-8")
    counter = 0
    while len(out) < DIM:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for off in range(0, 32, 4):
            (u,) = struct.unpack(">I", digest[off : off + 4])
            out.append((u / 2**31) - 1.0)
        counter += 1
    out = out[:DIM]
    norm = math.sqrt(sum(x * x for x in out))
    return [x / norm for x in out]


# --- embedder -------------------------------------------------------------------

class FakeEmbedder:
    """Registered vectors for engineered similarities; hash vectors otherwise."""

    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}
        self.fail = False
        self.fail_texts: set[str] = set()
        self.calls: list[str] = []

    def register(self, text: str, vector: Sequence[float]) -> None:
        self.vectors[text.strip()] = list(vector)

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail or text.strip() in self.fail_texts:
            raise RuntimeError("fake embedder forced failure")
        key = text.strip()
        if key in self.vectors:
            return list(self.vectors[key])
        return _hash_vector(key)


# --- reranker -------------------------------------------------------------------

class ZeroReranker:
    """Neutral reranker: all zeros, so ranking rests on RRF order + boosts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]:
        self.calls.append((query, list(docs)))
        return [0.0] * len(docs)


class ScriptedReranker:
    """Scores docs via a provided function (or raises, to test degradation)."""

    def __init__(self, score_fn=None, error: Exception | None = None) -> None:
        self.score_fn = score_fn
        self.error = error

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]:
        if self.error is not None:
            raise self.error
        return [float(self.score_fn(query, d)) for d in docs]


# --- brain runner -----------------------------------------------------------------

class ScriptedRunner:
    """Fake for the CLI subprocess boundary.

    ``respond`` may be a string (always returned), an Exception instance
    (always raised), or a callable taking the prompt and returning a string.
    """

    def __init__(self, respond=None) -> None:
        self.respond = respond
        self.calls: list[tuple[list[str], int]] = []

    def __call__(self, argv: Sequence[str], timeout: int) -> str:
        self.calls.append((list(argv), timeout))
        if isinstance(self.respond, Exception):
            raise self.respond
        if callable(self.respond):
            return self.respond(argv[-1])
        return self.respond if self.respond is not None else ""

    @property
    def last_prompt(self) -> str:
        return self.calls[-1][0][-1]


def unavailable_runner(message: str = "fake brain down") -> ScriptedRunner:
    return ScriptedRunner(respond=BrainUnavailable(message))


class ScriptedStreamRunner:
    """Fake for the STREAMING CLI boundary: yields preset stdout lines.

    ``lines`` may be a ``list[str]`` (yielded in order), an ``Exception``
    instance (raised eagerly on call, mirroring a spawn failure), or a callable
    taking the prompt and returning a ``list[str]``. argv is captured eagerly so
    prompt/flag assertions work without fully draining the iterator.
    """

    def __init__(self, lines=None) -> None:
        self.lines = lines if lines is not None else []
        self.calls: list[tuple[list[str], int]] = []

    def __call__(self, argv: Sequence[str], timeout: int):
        self.calls.append((list(argv), timeout))
        if isinstance(self.lines, Exception):
            raise self.lines
        lines = self.lines(argv[-1]) if callable(self.lines) else self.lines
        return iter(lines)

    @property
    def last_prompt(self) -> str:
        return self.calls[-1][0][-1]

    @property
    def last_argv(self) -> list[str]:
        return self.calls[-1][0]


# --- store ------------------------------------------------------------------------

@dataclass
class Row:
    """In-memory mirror of one memory table row."""

    id: int
    content: str
    source: str
    tags: list[str]
    confidence: float
    embedding: list[float]
    reinforcement: int = 1
    superseded_by: int | None = None
    archived: bool = False
    decay_score: float = 1.0
    last_used: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FakeStore:
    """Pure-Python StoreBackend with an injectable clock (``now``)."""

    def __init__(self) -> None:
        self.rows: dict[int, Row] = {}
        self.entity_ids: dict[str, int] = {}
        self.links: set[tuple[int, int]] = set()
        self.next_id = 1
        self.next_ent_id = 1
        self.now = datetime(2026, 6, 6, tzinfo=timezone.utc)
        self.fail = False
        self.schema_inits = 0
        self.closed = False

    # helpers -----------------------------------------------------------------
    def _check(self) -> None:
        if self.fail:
            raise MemoryUnavailable("fake store down")

    def _live(self) -> list[Row]:
        return [
            r for r in self.rows.values() if r.superseded_by is None and not r.archived
        ]

    def live_contents(self) -> list[str]:
        return [r.content for r in self._live()]

    def get(self, mem_id: int) -> Row:
        return self.rows[mem_id]

    # protocol -----------------------------------------------------------------
    def init_schema(self) -> None:
        self._check()
        self.schema_inits += 1

    def reset_schema(self) -> None:
        self._check()
        self.rows.clear()
        self.entity_ids.clear()
        self.links.clear()
        self.next_id = 1
        self.next_ent_id = 1

    def nearest(self, embedding: Sequence[float], limit: int) -> list[Neighbor]:
        self._check()
        scored = sorted(
            ((r, cosine(embedding, r.embedding)) for r in self._live() if r.embedding),
            key=lambda t: t[1],
            reverse=True,
        )
        return [Neighbor(r.id, r.content, s) for r, s in scored[:limit]]

    def dense_search(self, embedding: Sequence[float], limit: int) -> list[DenseRow]:
        self._check()
        scored = sorted(
            ((r, cosine(embedding, r.embedding)) for r in self._live() if r.embedding),
            key=lambda t: t[1],
            reverse=True,
        )
        return [DenseRow(r.id, r.content, r.source, s) for r, s in scored[:limit]]

    def sparse_search(self, query: str, limit: int) -> list[SparseRow]:
        self._check()
        words = content_words(query)
        if not words:
            return []
        scored = []
        for r in self._live():
            haystack = r.content.lower()
            n = sum(1 for w in words if w in haystack)
            if n:
                scored.append((r, n))
        scored.sort(key=lambda t: (-t[1], t[0].id))
        return [SparseRow(r.id, r.content, r.source) for r, _ in scored[:limit]]

    def reinforce(self, mem_id: int) -> None:
        self._check()
        row = self.rows[mem_id]
        row.reinforcement += 1
        row.last_used = self.now

    def insert(
        self,
        content: str,
        source: str,
        tags: Sequence[str],
        confidence: float,
        embedding: Sequence[float],
        entity_names: Sequence[str],
        supersede_ids: Sequence[int],
    ) -> int:
        self._check()
        mem_id = self.next_id
        self.next_id += 1
        self.rows[mem_id] = Row(
            id=mem_id,
            content=content,
            source=source,
            tags=list(tags),
            confidence=confidence,
            embedding=list(embedding),
            last_used=self.now,
            ts=self.now,
        )
        for sid in supersede_ids:
            old = self.rows[sid]
            if old.superseded_by is None:
                old.superseded_by = mem_id
                old.archived = True
        for name in entity_names:
            norm = entities.normalize(name)
            if norm not in self.entity_ids:
                self.entity_ids[norm] = self.next_ent_id
                self.next_ent_id += 1
            self.links.add((mem_id, self.entity_ids[norm]))
        return mem_id

    def entity_names(self, mem_ids: Sequence[int]) -> dict[int, set[str]]:
        self._check()
        wanted = set(mem_ids)
        reverse = {v: k for k, v in self.entity_ids.items()}
        out: dict[int, set[str]] = {}
        for mem_id, ent_id in self.links:
            if mem_id in wanted:
                out.setdefault(mem_id, set()).add(reverse[ent_id])
        return out

    def touch(self, mem_ids: Sequence[int]) -> None:
        self._check()
        for mem_id in mem_ids:
            row = self.rows[mem_id]
            row.reinforcement += 1
            row.last_used = self.now

    def apply_decay(self) -> int:
        self._check()
        archived = 0
        for row in self.rows.values():
            if row.superseded_by is not None:
                continue
            age = (self.now - row.ts).total_seconds()
            row.decay_score = compute_decay(age, row.reinforcement, row.confidence)
            if not row.archived and should_archive(
                row.decay_score, age, row.source, superseded=False
            ):
                row.archived = True
                archived += 1
        return archived

    def unpromoted_turns(self, limit: int) -> list[tuple[int, str]]:
        self._check()
        turns = [
            r for r in self._live() if r.source == "turn" and "promoted" not in r.tags
        ]
        turns.sort(key=lambda r: (r.ts, r.id))
        return [(r.id, r.content) for r in turns[:limit]]

    def mark_promoted(self, mem_id: int) -> None:
        self._check()
        row = self.rows[mem_id]
        if "promoted" not in row.tags:
            row.tags.append("promoted")

    def core_rows(self) -> list[tuple[int, str, str]]:
        self._check()
        return [(r.id, r.content, r.source) for r in self._live() if r.source == "core"]

    def list_memories(self, limit: int = 50, offset: int = 0) -> list[dict]:
        live = sorted(self._live(), key=lambda r: (r.ts, r.id), reverse=True)
        return [
            {"id": r.id, "content": r.content, "source": r.source,
             "confidence": round(r.confidence, 2), "reinforcement": r.reinforcement,
             "decay": round(r.decay_score, 2), "ts": r.ts.strftime("%Y-%m-%d %H:%M")}
            for r in live[offset:offset + limit]
        ]

    def list_entities(self, limit: int = 100) -> list[dict]:
        from collections import Counter

        reverse = {v: k for k, v in self.entity_ids.items()}
        counts = Counter(ent_id for _, ent_id in self.links)
        items = [{"name": reverse[eid], "mentions": n} for eid, n in counts.items()]
        items.sort(key=lambda e: (-e["mentions"], e["name"]))
        return items[:limit]

    def close(self) -> None:
        self.closed = True


# --- failure log -----------------------------------------------------------------

class FakeFailureStore:
    """In-memory FailureStore for tests. ``fail=True`` makes insert raise so we
    can prove record()/record_silent() never propagate (de-silencing must never
    itself fail)."""

    def __init__(self) -> None:
        self.rows: list = []
        self.fail = False
        self.schema_inits = 0
        self._seq = 0

    def init_schema(self) -> None:
        self.schema_inits += 1

    def insert(self, source: str, kind: str, detail: str) -> None:
        if self.fail:
            raise RuntimeError("fake failure store down")
        self._seq += 1
        self.rows.append((self._seq, source, kind, detail))

    def recent(self, limit: int) -> list:
        from utah.failures import FailureRow
        out = [FailureRow(source=s, kind=k, detail=d) for _, s, k, d in self.rows]
        out.reverse()  # most recent first
        return out[:limit]

    def count(self) -> int:
        return len(self.rows)

    def close(self) -> None:
        pass

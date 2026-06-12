"""Memory types — the storage boundary and recall row shapes."""
from __future__ import annotations

from typing import NamedTuple, Protocol, Sequence, runtime_checkable

# NOTE: field ORDER in these rows is load-bearing — they are constructed POSITIONALLY
# from SQL SELECT lists in store.py and consumed by name in pipeline.py. Reordering a
# field silently swaps content/source in every recall (test_memory_types pins this).


class Neighbor(NamedTuple):
    """A live nearest-neighbour row considered for dedup/supersede."""

    id: int
    content: str
    sim: float
    source: str = ""


class DenseRow(NamedTuple):
    """One dense-lane recall row (pgvector cosine): SELECT id, content, source, sim."""

    id: int
    content: str
    source: str
    sim: float


class SparseRow(NamedTuple):
    """One sparse-lane recall row (Postgres FTS, rank-ordered — no similarity score)."""

    id: int
    content: str
    source: str


@runtime_checkable
class StoreBackend(Protocol):
    """Storage boundary. Postgres in prod; a fake in unit tests.

    ``@runtime_checkable`` so a wiring bug (handing the pipeline a non-store) can be
    caught with ``isinstance`` at the boundary instead of an AttributeError mid-recall.
    """

    def init_schema(self) -> None: ...
    def reset_schema(self) -> None: ...
    def nearest(self, embedding: Sequence[float], limit: int) -> list[Neighbor]: ...
    def reinforce(self, mem_id: int) -> None: ...
    def insert(
        self,
        content: str,
        source: str,
        tags: Sequence[str],
        confidence: float,
        embedding: Sequence[float],
        entity_names: Sequence[str],
        supersede_ids: Sequence[int],
    ) -> int: ...
    def dense_search(self, embedding: Sequence[float], limit: int) -> list[DenseRow]: ...
    def curated_search(
        self, embedding: Sequence[float], limit: int, sources: Sequence[str]
    ) -> list[DenseRow]: ...
    def sparse_search(self, query: str, limit: int) -> list[SparseRow]: ...
    def entity_names(self, mem_ids: Sequence[int]) -> dict[int, set[str]]: ...
    def touch(self, mem_ids: Sequence[int]) -> None: ...
    def apply_decay(self) -> int: ...
    def unpromoted_turns(self, limit: int) -> list[tuple[int, str]]: ...
    def mark_promoted(self, mem_id: int) -> None: ...
    def core_rows(self) -> list[tuple[int, str, str]]: ...
    def close(self) -> None: ...

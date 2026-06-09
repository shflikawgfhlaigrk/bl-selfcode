"""Memory types — the storage boundary and recall row shapes."""
from __future__ import annotations

from typing import NamedTuple, Protocol, Sequence


class Neighbor(NamedTuple):
    """A live nearest-neighbour row considered for dedup/supersede."""

    id: int
    content: str
    sim: float
    source: str = ""


class DenseRow(NamedTuple):
    id: int
    content: str
    source: str
    sim: float


class SparseRow(NamedTuple):
    id: int
    content: str
    source: str


class StoreBackend(Protocol):
    """Storage boundary. Postgres in prod; a fake in unit tests."""

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

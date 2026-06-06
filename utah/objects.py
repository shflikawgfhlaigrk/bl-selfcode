"""Utah core types — msgspec Structs: immutable, slotted, one model wire <-> db.

Per the objects audit (6-objects.md): enums not strings, frozen not commented,
no ``dict[str, Any]`` bags on contracts. Drift becomes a type error, not a
runtime crash.
"""
from __future__ import annotations

import enum

import msgspec


class ReplySource(enum.Enum):
    """Where the text of a :class:`Reply` came from (provenance, surfaced)."""

    MEMORY = "memory"            # answered straight from admitted memory
    BRAIN = "brain"              # reasoned by the Claude CLI over recalled context
    UNAVAILABLE = "unavailable"  # honest failure: no confident memory, brain down


class WriteAction(enum.Enum):
    """What the admission pipeline did with a write."""

    INSERTED = "inserted"        # new row admitted (possibly superseding old rows)
    REINFORCED = "reinforced"    # near-duplicate: existing row strengthened


class Hit(msgspec.Struct, frozen=True):
    """One recalled memory row.

    ``score`` is the final ranking score (cross-encoder + entity boost);
    ``sim`` is the raw dense cosine similarity to the query (0.0 when the row
    only surfaced through the sparse lane) — the answer gate reads ``sim``.
    """

    id: int
    content: str
    source: str
    score: float
    sim: float = 0.0


class Reply(msgspec.Struct, frozen=True):
    """The result of one ``tell()`` turn."""

    text: str
    source: ReplySource
    hits: list[Hit] = []


class WriteDecision(msgspec.Struct, frozen=True):
    """Pure decision produced by :func:`utah.memory.decide_write` (no I/O)."""

    action: WriteAction
    reinforce_id: int | None = None   # set when action is REINFORCED
    supersede_ids: list[int] = []     # rows the new fact supersedes (INSERTED)


class WriteResult(msgspec.Struct, frozen=True):
    """Outcome of an admitted write."""

    id: int
    action: WriteAction
    superseded: list[int] = []


class ConsolidationReport(msgspec.Struct, frozen=True):
    """What one sleep-time consolidation pass did."""

    turns_seen: int
    facts_promoted: int
    facts_skipped: int
    brain_failures: int
    archived: int

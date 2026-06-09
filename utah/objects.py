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
    SOCIAL = "social"            # a deterministic social pleasantry (no model, instant)
    CAPABILITY = "capability"    # a deterministic grounded capability (weather, brief)
    LOCAL = "local"              # a free resident local model (L1, in front of the brain)
    BRAIN = "brain"              # reasoned by the Claude CLI over recalled context
    LEARNED = "learned"          # cold miss → researched the web, grounded, then answered
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


# --------------------------------------------------------------------------
# Product/revenue row contracts (B4). The brain/memory path was fully typed;
# product rows moved as loose dict/JSON, so a wrong key was a silent runtime
# bug instead of a type error. These Structs are the ONE canonical shape each
# row has — leads.py produces it, outreach.py consumes it, ledger.py persists
# it — with tolerant ``from_mapping`` (ignore extras) + ``as_dict`` adapters so
# the still-dict storage layer (jsonb / psycopg rows) interoperates cleanly.
# --------------------------------------------------------------------------


class _RowStruct(msgspec.Struct, frozen=True):
    """Shared adapters for the product-row contracts."""

    @classmethod
    def from_mapping(cls, m) -> "_RowStruct":
        """Build from a dict, keeping only known fields (extras ignored, never crash)."""
        fields = set(cls.__struct_fields__)
        return cls(**{k: v for k, v in dict(m or {}).items() if k in fields})

    def as_dict(self) -> dict:
        """Plain dict (for jsonb storage / the dict-shaped consumers that remain)."""
        return msgspec.structs.asdict(self)


class Lead(_RowStruct, frozen=True):
    """A scraped no-website SMB lead — the leads.py → outreach.py contract.

    ``contact`` is the jsonb bag ``{phone?, email?, address?}`` (kept a dict to match
    the column); everything else is a typed top-level field."""

    name: str
    kind: str = ""
    contact: dict = {}
    source: str = "osm"
    region: str = ""
    id: int | None = None


class Fire(_RowStruct, frozen=True):
    """One trading-engine fire row — never fabricated (``synthetic`` flags board demos),
    ``pnl``/``outcome`` stay ``None`` until a real broker closes it."""

    engine: str
    direction: str
    entry: float | None = None
    synthetic: bool = False
    symbol: str = ""
    pnl: float | None = None
    outcome: str | None = None
    id: int | None = None


class OutreachRow(_RowStruct, frozen=True):
    """One suppression-ledger row — UNIQUE(recipient, campaign) = never contact twice."""

    recipient: str
    campaign: str
    channel: str = "email"
    id: int | None = None

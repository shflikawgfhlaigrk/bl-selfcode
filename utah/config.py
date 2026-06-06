"""Utah config — one place, env-overridable at the deploy seams only.

Doctrine: free-everything except the Claude CLI (the one allowed paid lane).
Connection/command settings are env-overridable because they differ per machine;
the decision thresholds are doctrine constants — they are part of the spec, have
direct unit tests, and are NOT runtime-tunable (changing them is a spec change).
"""
from __future__ import annotations

import os

# --- deploy seams (env-overridable) -----------------------------------------
#: Postgres DSN. Production (Michael's Mac): socket at /tmp, cluster on :5433.
DB_DSN: str = os.environ.get("UTAH_DSN", "host=/tmp port=5433 dbname=utah")

#: The brain command — Claude CLI on PATH (subscription; the one paid lane).
BRAIN_CMD: str = os.environ.get("UTAH_BRAIN", "claude")

#: Arguments for one-shot print mode.
BRAIN_ARGS: tuple[str, ...] = ("-p",)

#: Arguments for one-shot STREAMING print mode (newline-delimited stream-json).
#: ``--include-partial-messages`` emits incremental token deltas; ``--verbose``
#: is required for stream-json under ``-p``; ``--no-session-persistence`` keeps
#: each turn a clean one-shot. Parsed by ``brain.decode_stream``.
BRAIN_STREAM_ARGS: tuple[str, ...] = (
    "-p",
    "--output-format",
    "stream-json",
    "--include-partial-messages",
    "--no-session-persistence",
    "--verbose",
)

#: Seconds to wait for one brain turn before declaring it unavailable.
BRAIN_TIMEOUT: int = int(os.environ.get("UTAH_BRAIN_TIMEOUT", "120"))

#: Seconds to wait when opening a Postgres connection.
DB_CONNECT_TIMEOUT: int = int(os.environ.get("UTAH_DB_CONNECT_TIMEOUT", "5"))

# --- embedding ---------------------------------------------------------------
#: Free, no-torch: fastembed (onnxruntime) BGE-small, 384-dim.
EMBED_MODEL: str = "BAAI/bge-small-en-v1.5"
EMBED_DIM: int = 384

#: Cross-encoder rerank model (fastembed ONNX, free).
RERANK_MODEL: str = "Xenova/ms-marco-MiniLM-L-6-v2"

#: Durable cache for the fastembed reranker model. MUST live under ~/.utah
#: (where every Utah model lives) — fastembed's default is macOS temp
#: (/var/folders/.../T) which gets purged, silently reverting rerank to
#: degraded all-zero scores. Env-overridable for other machines.
RERANK_CACHE_DIR: str = os.environ.get(
    "UTAH_RERANK_CACHE", os.path.expanduser("~/.utah/models/fastembed")
)

# --- memory decision rules (doctrine constants; each has a unit test) --------
#: Near-identical (cosine) -> reinforce the existing row instead of inserting.
DEDUP_SIM: float = 0.995

#: Paraphrase-level similarity -> the newer fact supersedes the older one
#: unconditionally (no shared entity required).
SUPERSEDE_SIM: float = 0.90

#: Same-entity attribute change ("Michael lives in X" -> "Michael lives in Y"):
#: supersede when this similarity is reached AND the rows share an entity.
#: This is the Newman-bug path — same subject, changed value, cosine < 0.90.
SUPERSEDE_ENT: float = 0.78

#: How many nearest live rows are scanned for dedup/supersede on every write.
#: Top-1 was the Newman bug: the contradicting fact is not always the single
#: nearest neighbour, so we scan a window and supersede every match.
SUPERSEDE_SCAN: int = 8

#: Sources allowed through the admission gate. Anything else (backfill,
#: synthetic, scraped) is denied structurally — confabulation dies here.
ALLOWED_SOURCES: frozenset[str] = frozenset(
    {"user", "turn", "fact", "consolidation", "sensor"}
)

#: Admission: reject degenerate content beyond this many characters
#: (callers must chunk; a memory row is an atomic fact or one exchange).
MAX_CONTENT_CHARS: int = 10_000

# --- recall fusion / rerank ---------------------------------------------------
#: Reciprocal-rank-fusion constant (the standard k=60).
RRF_K: int = 60

#: Number of hits returned by recall.
RECALL_K: int = 5

#: Candidate pool per lane before fusion/rerank (multiplier on k, floor 20).
RECALL_POOL_FACTOR: int = 4
RECALL_POOL_MIN: int = 20

#: Additive bonus to the rerank score when query and memory share an entity
#: (the GraphRAG boost).
ENTITY_BOOST: float = 0.5

# --- no-fabrication answer gate ----------------------------------------------
#: Answer straight from memory ONLY when BOTH hold; otherwise fall to the brain
#: (which itself says "I don't know" when the context doesn't support it).
ANSWER_MIN_SIM: float = 0.45      # dense cosine similarity of the best hit
ANSWER_MIN_OVERLAP: float = 0.50  # fraction of question content-words in the hit

# --- decay zones ---------------------------------------------------------------
#: decay_score = W_RECENCY * exp(-age / DECAY_HALFLIFE)
#:             + W_FREQUENCY * min(1, ln(1 + reinforcement) / 3)
#:             + W_CONFIDENCE * confidence
DECAY_W_RECENCY: float = 0.5
DECAY_W_FREQUENCY: float = 0.3
DECAY_W_CONFIDENCE: float = 0.2
DECAY_HALFLIFE_SECONDS: float = 2_592_000.0  # ~30 days

#: Archive (reversible zone, never delete) when decay_score falls below this.
#: 0.25, not the prototype's 0.15: with these weights an unreinforced
#: confidence-0.5 turn floors at ~0.169, so 0.15 was mathematically
#: unreachable — the prototype could never archive anything.
DECAY_ARCHIVE_BELOW: float = 0.25

#: Grace period: never archive rows younger than this many days.
DECAY_MIN_AGE_DAYS: int = 7

#: Sources that never decay-archive (durable, provenance-marked facts).
DECAY_PROTECTED_SOURCES: frozenset[str] = frozenset({"fact", "consolidation"})

# --- brain prompt budget --------------------------------------------------------
#: Hard cap on context characters passed to the CLI (argv size safety).
BRAIN_CONTEXT_MAX_CHARS: int = 8_000

#: Max facts accepted from one extraction pass.
MAX_FACTS_PER_TURN: int = 8

#: Max characters per extracted fact.
MAX_FACT_CHARS: int = 500

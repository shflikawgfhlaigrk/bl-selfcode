"""Utah memory — grounded compounding memory on Postgres + pgvector.

Hybrid recall (dense HNSW + Postgres FTS, RRF-fused) -> cross-encoder rerank ->
entity boost -> no-fabrication answer gate. Writes pass one admission pipeline:
embed + allowed source (gate), near-duplicate reinforces instead of inserting,
contradictions supersede (paraphrase path OR shared-entity attribute-change
path — the Newman fix), nothing is ever deleted (superseded_by pointer +
archived flag), decay zones fade stale rows reversibly.

Layering:

* **Pure decision logic** (``decide_write``, ``rrf_fuse``, ``compute_decay``,
  ``passes_gate``, ``lexical_overlap``) — no I/O, directly unit-tested.
* **StoreBackend protocol** — the storage boundary. :class:`PostgresStore` is
  production; tests run the identical pipeline on an in-memory fake.
* **Module API** (``store``/``recall``/``answer``/``decay``/``init``) — the
  orchestration every caller (live turns AND consolidation: no side door) uses.

Every storage failure surfaces as :class:`MemoryUnavailable`; policy rejections
surface as :class:`AdmissionDenied`. SQL is parameterized only; the
supersede + insert invariant is applied in a single transaction.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from typing import NamedTuple, Protocol, Sequence

from utah import UtahError, config, entities
from utah.embed import EmbedError, embed
from utah.objects import Hit, WriteAction, WriteDecision, WriteResult
from utah.rerank import rerank

log = logging.getLogger("utah.memory")


class MemoryUnavailable(UtahError):
    """The memory store could not be reached or a statement failed."""


class AdmissionDenied(UtahError):
    """The admission gate rejected a write (policy, not infrastructure)."""


# --------------------------------------------------------------------------
# Pure decision logic (no I/O — each rule has a direct unit test)
# --------------------------------------------------------------------------

_STOP = frozenset(
    "the a an of to in is are was were be and or for on at with it this that "
    "what who whom how why when where do does did i you he she they we me my "
    "your his her our their if".split()
)


class Neighbor(NamedTuple):
    """A live nearest-neighbour row considered for dedup/supersede."""

    id: int
    content: str
    sim: float  # cosine similarity to the incoming content
    source: str = ""  # provenance — 'core' rows are authoritative and never superseded


class DenseRow(NamedTuple):
    id: int
    content: str
    source: str
    sim: float


class SparseRow(NamedTuple):
    id: int
    content: str
    source: str


def content_words(text: str) -> list[str]:
    """Lowercased content words of *text* (stopwords and 1-char tokens out)."""
    return [
        w
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in _STOP and len(w) > 1
    ]


def lexical_overlap(query: str, content: str) -> float:
    """Fraction of the query's content words present (substring) in *content*.

    Substring containment is deliberate: "live" matches "lives". Returns 0.0
    when the query has no content words (an all-stopword query can never pass
    the answer gate on overlap alone).
    """
    words = content_words(query)
    if not words:
        return 0.0
    haystack = content.lower()
    return sum(1 for w in words if w in haystack) / len(words)


def passes_gate(sim: float, overlap: float) -> bool:
    """No-fabrication answer gate: BOTH thresholds must hold."""
    return sim >= config.ANSWER_MIN_SIM and overlap >= config.ANSWER_MIN_OVERLAP


def entity_grounds(query: str, content: str) -> bool | None:
    """Does the hit's entity actually appear in the query? Returns:

    * ``True``  — a proper-noun entity from *content* is named in *query*;
    * ``False`` — *content* is about a DIFFERENT entity than the query asks about
      (so it must NOT be served as the answer, even with a passing lexical overlap);
    * ``None``  — *content* has no extractable entity, so this signal can't decide
      and the caller falls back to the lexical gate alone.

    This kills the wrong-entity answer bug: a "Mount Everest" fact has no entity
    present in "how tall is mount kilimanjaro", so generic-word overlap ("tall",
    "mount", "metres") no longer lets it answer a question about a different subject.
    Entities are extracted from the (capitalized) stored *content* — user queries are
    lowercase, so query-side extraction can't see them; we match the hit's entity
    tokens against the query's tokens instead.
    """
    hit_entities = entities.normalized_set(content)
    if not hit_entities:
        return None
    q_tokens = set(re.findall(r"[a-z0-9]+", query.casefold()))
    return any(set(ent.split()).issubset(q_tokens) for ent in hit_entities)


def rrf_fuse(
    rankings: Sequence[Sequence[int]], k: int = config.RRF_K
) -> dict[int, float]:
    """Reciprocal-rank fusion: score(id) = sum over lanes of 1 / (k + rank).

    *rank* is 1-based within each lane. ``k`` is the standard RRF constant
    (60), explicit and overridable for tests.
    """
    scores: dict[int, float] = {}
    for lane in rankings:
        for rank, item in enumerate(lane, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def decide_write(content: str, neighbors: Sequence[Neighbor]) -> WriteDecision:
    """Deterministic write decision against the nearest live rows.

    Rules (in order):

    1. **Dedup-reinforce:** if any neighbour is near-identical
       (sim >= ``DEDUP_SIM`` or exact case-folded text match) -> REINFORCE it.
    2. **Insert + supersede:** otherwise INSERT; every neighbour that
       contradicts is superseded —
       a. *paraphrase path:* sim >= ``SUPERSEDE_SIM`` (no entity needed), or
       b. *entity path (Newman fix):* sim >= ``SUPERSEDE_ENT`` AND the two
          texts share a normalized entity (same subject, changed attribute).

    The whole neighbour window is scanned (not just top-1 — that was the
    Newman bug) and ALL matching rows are superseded. Entities are extracted
    from the texts directly, so the rule also fires for rows whose graph links
    were never written.
    """
    norm_new = " ".join(content.split()).casefold()
    for nb in neighbors:
        if nb.sim >= config.DEDUP_SIM or " ".join(nb.content.split()).casefold() == norm_new:
            return WriteDecision(action=WriteAction.REINFORCED, reinforce_id=nb.id)

    new_ents = entities.normalized_set(content)
    supersede: list[int] = []
    for nb in neighbors:
        if nb.source in config.NEVER_SUPERSEDE_SOURCES:
            continue  # authoritative ground truth (the creed + the curated library) — a
            #            later fact/turn must NEVER collapse it, or the always-on identity
            #            rots and distinct reference principles get eaten as "duplicates"
        if nb.sim >= config.SUPERSEDE_SIM:
            supersede.append(nb.id)
        elif nb.sim >= config.SUPERSEDE_ENT and new_ents & entities.normalized_set(nb.content):
            supersede.append(nb.id)
    return WriteDecision(action=WriteAction.INSERTED, supersede_ids=supersede)


def compute_decay(age_seconds: float, reinforcement: int, confidence: float) -> float:
    """Decay score: recency x frequency x confidence (mirrors the SQL exactly).

    score = W_RECENCY    * exp(-age / halflife)
          + W_FREQUENCY  * min(1, ln(1 + reinforcement) / 3)
          + W_CONFIDENCE * confidence
    """
    recency = math.exp(-max(age_seconds, 0.0) / config.DECAY_HALFLIFE_SECONDS)
    frequency = min(1.0, math.log(1 + max(reinforcement, 0)) / 3.0)
    return (
        config.DECAY_W_RECENCY * recency
        + config.DECAY_W_FREQUENCY * frequency
        + config.DECAY_W_CONFIDENCE * confidence
    )


def should_archive(
    decay_score: float, age_seconds: float, source: str, superseded: bool
) -> bool:
    """Archive rule for the decay pass (reversible; never a delete).

    Superseded rows are already out of recall; protected sources
    (fact/consolidation) never fade; younger than the grace period never fades.
    """
    if superseded or source in config.DECAY_PROTECTED_SOURCES:
        return False
    if age_seconds < config.DECAY_MIN_AGE_DAYS * 86_400:
        return False
    return decay_score < config.DECAY_ARCHIVE_BELOW


def recall_pool(k: int) -> int:
    """Candidate pool size per lane before fusion/rerank."""
    return max(k * config.RECALL_POOL_FACTOR, config.RECALL_POOL_MIN)


# --------------------------------------------------------------------------
# Storage boundary
# --------------------------------------------------------------------------


class StoreBackend(Protocol):
    """The storage boundary. PostgresStore in prod; a fake in unit tests.

    Implementations must guarantee: ``insert`` applies the row insert, the
    supersede marks, and the entity links atomically (one transaction);
    "live" means ``superseded_by IS NULL AND NOT archived``.
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
    def curated_search(self, embedding: Sequence[float], limit: int,
                       sources: Sequence[str]) -> list[DenseRow]: ...
    def sparse_search(self, query: str, limit: int) -> list[SparseRow]: ...
    def entity_names(self, mem_ids: Sequence[int]) -> dict[int, set[str]]: ...
    def touch(self, mem_ids: Sequence[int]) -> None: ...
    def apply_decay(self) -> int: ...
    def unpromoted_turns(self, limit: int) -> list[tuple[int, str]]: ...
    def mark_promoted(self, mem_id: int) -> None: ...
    def core_rows(self) -> list[tuple[int, str, str]]: ...
    def close(self) -> None: ...


_DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS memory (
  id            bigserial PRIMARY KEY,
  content       text NOT NULL,
  source        text NOT NULL DEFAULT 'user',
  tags          text[] NOT NULL DEFAULT '{{}}',
  confidence    real NOT NULL DEFAULT 0.6,
  reinforcement int  NOT NULL DEFAULT 1,
  superseded_by bigint,
  archived      boolean NOT NULL DEFAULT false,
  decay_score   real NOT NULL DEFAULT 1.0,
  embedding     vector({config.EMBED_DIM}),
  fts           tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  last_used     timestamptz NOT NULL DEFAULT now(),
  ts            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS memory_hnsw ON memory USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS memory_fts  ON memory USING gin (fts);
CREATE TABLE IF NOT EXISTS entity (
  id       bigserial PRIMARY KEY,
  name     text UNIQUE NOT NULL,
  mentions int NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS mem_entity (
  mem_id bigint NOT NULL,
  ent_id bigint NOT NULL,
  PRIMARY KEY (mem_id, ent_id)
);
"""


class PostgresStore:
    """Production backend: Postgres + pgvector over one managed connection.

    The connection is created lazily, reused (no per-query connects, no leaks),
    re-established transparently after a drop, and every psycopg error is
    wrapped in :class:`MemoryUnavailable`. All SQL is parameterized. A lock
    serializes access (the brain loop is single-threaded; the lock is cheap
    insurance for future callers).
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn if dsn is not None else config.DB_DSN
        self._conn = None
        self._lock = threading.RLock()

    # -- connection management ------------------------------------------------

    def _connect(self):
        import psycopg
        from pgvector.psycopg import register_vector

        # autocommit=True is the psycopg3 pattern for explicit transaction
        # blocks: conn.transaction() then issues real BEGIN/COMMIT. With
        # autocommit=False, register_vector's catalog lookups would open an
        # implicit transaction and every later block would be a savepoint
        # inside it — nothing would ever commit.
        conn = psycopg.connect(
            self._dsn, autocommit=True, connect_timeout=config.DB_CONNECT_TIMEOUT
        )
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            # First boot: the vector type does not exist yet. Create it, retry.
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
        return conn

    def _get_conn(self):
        import psycopg

        if self._conn is None or self._conn.closed:
            try:
                self._conn = self._connect()
            except psycopg.Error as exc:
                self._conn = None
                raise MemoryUnavailable(f"cannot connect to memory store: {exc}") from exc
        return self._conn

    def _tx(self):
        """Context manager: one transaction on the managed connection."""
        return _PgTransaction(self)

    def close(self) -> None:
        """Close the managed connection (idempotent)."""
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    @staticmethod
    def _vec(embedding: Sequence[float]):
        try:
            from pgvector import Vector
        except ImportError:  # older pgvector-python layouts
            from pgvector.utils import Vector

        return Vector(list(embedding))

    # -- schema ----------------------------------------------------------------

    def init_schema(self) -> None:
        with self._tx() as conn:
            conn.execute(_DDL)

    def reset_schema(self) -> None:
        """Dev-only clean slate (drops everything, then re-creates).

        PRODUCTION-SAFETY GUARD: refuses unless ``UTAH_ALLOW_RESET=1``. This DROPs the
        memory/entity tables — a stray call (a misrouted test, an accidental --reset) once
        wiped 9k migrated facts. The wipe now cannot happen without an explicit opt-in.
        """
        import os

        if os.environ.get("UTAH_ALLOW_RESET") != "1":
            raise MemoryUnavailable(
                "reset_schema refused: this DROPs all memory. Set UTAH_ALLOW_RESET=1 "
                "to intentionally wipe (production-safety guard)."
            )
        with self._tx() as conn:
            conn.execute("DROP TABLE IF EXISTS mem_entity, entity, memory CASCADE")
            conn.execute(_DDL)

    # -- reads -------------------------------------------------------------------

    def nearest(self, embedding: Sequence[float], limit: int) -> list[Neighbor]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, 1 - (embedding <=> %s) AS sim, source FROM memory "
                "WHERE superseded_by IS NULL AND NOT archived AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s",
                (self._vec(embedding), self._vec(embedding), limit),
            ).fetchall()
        return [Neighbor(int(r[0]), r[1], float(r[2]), r[3]) for r in rows]

    def dense_search(self, embedding: Sequence[float], limit: int) -> list[DenseRow]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, source, 1 - (embedding <=> %s) AS sim FROM memory "
                "WHERE superseded_by IS NULL AND NOT archived AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s",
                (self._vec(embedding), self._vec(embedding), limit),
            ).fetchall()
        return [DenseRow(int(r[0]), r[1], r[2], float(r[3])) for r in rows]

    def curated_search(self, embedding: Sequence[float], limit: int,
                       sources: Sequence[str]) -> list[DenseRow]:
        """Top-*limit* nearest live rows restricted to *sources* (the curated lane).
        Identical to :meth:`dense_search` but source-filtered, so high-value rows
        (identity + the books) always get a fair shot at the reranker."""
        if not sources:
            return []
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, source, 1 - (embedding <=> %s) AS sim FROM memory "
                "WHERE superseded_by IS NULL AND NOT archived AND embedding IS NOT NULL "
                "AND source = ANY(%s) "
                "ORDER BY embedding <=> %s LIMIT %s",
                (self._vec(embedding), list(sources), self._vec(embedding), limit),
            ).fetchall()
        return [DenseRow(int(r[0]), r[1], r[2], float(r[3])) for r in rows]

    def sparse_search(self, query: str, limit: int) -> list[SparseRow]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, source FROM memory "
                "WHERE superseded_by IS NULL AND NOT archived "
                "AND fts @@ websearch_to_tsquery('english', %s) "
                "ORDER BY ts_rank(fts, websearch_to_tsquery('english', %s)) DESC "
                "LIMIT %s",
                (query, query, limit),
            ).fetchall()
        return [SparseRow(int(r[0]), r[1], r[2]) for r in rows]

    def entity_names(self, mem_ids: Sequence[int]) -> dict[int, set[str]]:
        if not mem_ids:
            return {}
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT m.mem_id, e.name FROM mem_entity m "
                "JOIN entity e ON e.id = m.ent_id WHERE m.mem_id = ANY(%s)",
                (list(mem_ids),),
            ).fetchall()
        out: dict[int, set[str]] = {}
        for mem_id, name in rows:
            out.setdefault(int(mem_id), set()).add(entities.normalize(name))
        return out

    def unpromoted_turns(self, limit: int) -> list[tuple[int, str]]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content FROM memory "
                "WHERE source = 'turn' AND NOT ('promoted' = ANY(tags)) "
                "AND superseded_by IS NULL AND NOT archived "
                "ORDER BY ts ASC LIMIT %s",
                (limit,),
            ).fetchall()
        return [(int(r[0]), r[1]) for r in rows]

    # -- writes -------------------------------------------------------------------

    def reinforce(self, mem_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE memory SET reinforcement = reinforcement + 1, "
                "last_used = now() WHERE id = %s",
                (mem_id,),
            )

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
        with self._tx() as conn:  # insert + supersede + links: ONE transaction
            mem_id = int(
                conn.execute(
                    "INSERT INTO memory (content, source, tags, confidence, embedding) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (content, source, list(tags), confidence, self._vec(embedding)),
                ).fetchone()[0]
            )
            if supersede_ids:
                conn.execute(
                    "UPDATE memory SET superseded_by = %s, archived = true "
                    "WHERE id = ANY(%s) AND superseded_by IS NULL",
                    (mem_id, list(supersede_ids)),
                )
            for name in entity_names:
                ent_id = conn.execute(
                    "INSERT INTO entity (name) VALUES (%s) "
                    "ON CONFLICT (name) DO UPDATE SET mentions = entity.mentions + 1 "
                    "RETURNING id",
                    (entities.normalize(name),),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO mem_entity (mem_id, ent_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (mem_id, ent_id),
                )
        return mem_id

    def touch(self, mem_ids: Sequence[int]) -> None:
        if not mem_ids:
            return
        with self._tx() as conn:
            conn.execute(
                "UPDATE memory SET reinforcement = reinforcement + 1, "
                "last_used = now() WHERE id = ANY(%s)",
                (list(mem_ids),),
            )

    def mark_promoted(self, mem_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE memory SET tags = array_append(tags, 'promoted') "
                "WHERE id = %s AND NOT ('promoted' = ANY(tags))",
                (mem_id,),
            )

    def core_rows(self) -> list[tuple[int, str, str]]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, source FROM memory "
                "WHERE source = 'core' AND superseded_by IS NULL AND NOT archived "
                "ORDER BY reinforcement DESC, ts ASC"
            ).fetchall()
        return [(int(r[0]), r[1], r[2]) for r in rows]

    def apply_decay(self) -> int:
        """Recompute decay_score for live rows; archive faded unprotected rows.

        Mirrors :func:`compute_decay` / :func:`should_archive` exactly.
        Returns the number of rows archived this pass.
        """
        with self._tx() as conn:
            conn.execute(
                "UPDATE memory SET decay_score = "
                "  %s * exp(-extract(epoch FROM now() - ts) / %s) "
                "+ %s * LEAST(1.0, ln(1 + reinforcement) / 3.0) "
                "+ %s * confidence "
                "WHERE superseded_by IS NULL",
                (
                    config.DECAY_W_RECENCY,
                    config.DECAY_HALFLIFE_SECONDS,
                    config.DECAY_W_FREQUENCY,
                    config.DECAY_W_CONFIDENCE,
                ),
            )
            cur = conn.execute(
                "UPDATE memory SET archived = true "
                "WHERE NOT archived AND superseded_by IS NULL "
                "AND NOT (source = ANY(%s)) AND decay_score < %s "
                "AND ts < now() - make_interval(days => %s)",
                (
                    list(config.DECAY_PROTECTED_SOURCES),
                    config.DECAY_ARCHIVE_BELOW,
                    config.DECAY_MIN_AGE_DAYS,
                ),
            )
            return cur.rowcount or 0

    def live_counts(self) -> dict:
        """Real gauges for the dashboard: total/live rows + entities (read-only)."""
        with self._tx() as conn:
            total = conn.execute("SELECT count(*) FROM memory").fetchone()[0]
            live = conn.execute(
                "SELECT count(*) FROM memory WHERE superseded_by IS NULL AND NOT archived"
            ).fetchone()[0]
            entities = conn.execute("SELECT count(*) FROM entity").fetchone()[0]
        return {"total": int(total), "live": int(live), "entities": int(entities)}

    def list_memories(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Live memory rows (newest first) behind the gauge — the deck drill-down."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, source, confidence, reinforcement, decay_score, "
                "to_char(ts, 'YYYY-MM-DD HH24:MI') "
                "FROM memory WHERE superseded_by IS NULL AND NOT archived "
                "ORDER BY ts DESC, id DESC LIMIT %s OFFSET %s",
                (limit, offset),
            ).fetchall()
        return [
            {"id": int(r[0]), "content": r[1], "source": r[2],
             "confidence": round(float(r[3]), 2), "reinforcement": int(r[4]),
             "decay": round(float(r[5]), 2), "ts": r[6]}
            for r in rows
        ]

    def list_entities(self, limit: int = 100) -> list[dict]:
        """Entities by live-link count — the deck drill-down for the entity gauge."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT e.name, count(me.mem_id) FROM entity e "
                "LEFT JOIN mem_entity me ON me.ent_id = e.id "
                "GROUP BY e.id, e.name ORDER BY count(me.mem_id) DESC, e.name LIMIT %s",
                (limit,),
            ).fetchall()
        return [{"name": r[0], "mentions": int(r[1])} for r in rows]


class _PgTransaction:
    """One locked transaction on a PostgresStore's managed connection.

    Wraps every psycopg error in :class:`MemoryUnavailable`; drops the cached
    connection on operational errors so the next call reconnects cleanly.
    """

    def __init__(self, store: PostgresStore) -> None:
        self._store = store
        self._tx = None

    def __enter__(self):
        import psycopg

        self._store._lock.acquire()
        try:
            conn = self._store._get_conn()
            self._tx = conn.transaction()
            self._tx.__enter__()
            return conn
        except MemoryUnavailable:
            self._store._lock.release()
            raise
        except psycopg.Error as exc:
            self._store._lock.release()
            raise MemoryUnavailable(f"memory store transaction failed: {exc}") from exc

    def __exit__(self, exc_type, exc, tb) -> bool:
        import psycopg

        try:
            try:
                self._tx.__exit__(exc_type, exc, tb)
            except psycopg.Error as tx_exc:
                self._store.close()  # commit/rollback failed: force reconnect
                raise MemoryUnavailable(f"memory store commit failed: {tx_exc}") from tx_exc
            if exc_type is not None and issubclass(exc_type, psycopg.Error):
                if isinstance(exc, psycopg.OperationalError):
                    self._store.close()  # force reconnect next time
                raise MemoryUnavailable(f"memory store error: {exc}") from exc
            return False
        finally:
            self._store._lock.release()


# --------------------------------------------------------------------------
# Module API — the one pipeline (live turns and consolidation both use it)
# --------------------------------------------------------------------------

_backend: StoreBackend | None = None
_backend_lock = threading.Lock()


def get_backend() -> StoreBackend:
    """Return the process-global backend, creating PostgresStore lazily."""
    global _backend
    with _backend_lock:
        if _backend is None:
            _backend = PostgresStore()
        return _backend


def set_backend(backend: StoreBackend | None) -> None:
    """Inject a backend (tests). ``None`` restores the default PostgresStore."""
    global _backend
    with _backend_lock:
        if _backend is not None and backend is not _backend:
            try:
                _backend.close()
            except Exception as exc:  # closing must never mask the swap
                log.warning("error closing previous backend: %s", exc)
        _backend = backend


def init() -> None:
    """Create the schema (idempotent; safe to run on every boot)."""
    get_backend().init_schema()


def reset() -> None:
    """Dev-only: drop and re-create the schema (destructive, explicit)."""
    get_backend().reset_schema()


def store(
    content: str,
    source: str = "user",
    tags: Sequence[str] | None = None,
    confidence: float = 0.6,
) -> WriteResult:
    """Admission-gated write: embed + source, de-dup, supersede on contradiction.

    Raises:
        AdmissionDenied: empty content, disallowed source, oversize content,
            or confidence out of [0, 1] — policy rejections, by design.
        EmbedError: the content could not be embedded (a row is never
            admitted without a vector — that is the gate).
        MemoryUnavailable: the store is down.
    """
    text = (content or "").strip()
    if not text:
        raise AdmissionDenied("admission denied: empty content")
    if len(text) > config.MAX_CONTENT_CHARS:
        raise AdmissionDenied(
            f"admission denied: content over {config.MAX_CONTENT_CHARS} chars (chunk it)"
        )
    if source not in config.ALLOWED_SOURCES:
        raise AdmissionDenied(
            f"admission denied: source {source!r} not in {sorted(config.ALLOWED_SOURCES)}"
        )
    if not 0.0 <= confidence <= 1.0:
        raise AdmissionDenied(f"admission denied: confidence {confidence} not in [0, 1]")

    vector = embed(text)  # EmbedError propagates: no vector, no admission
    backend = get_backend()
    neighbors = backend.nearest(vector, config.SUPERSEDE_SCAN)
    decision = decide_write(text, neighbors)
    if decision.action is WriteAction.REINFORCED:
        assert decision.reinforce_id is not None
        backend.reinforce(decision.reinforce_id)
        return WriteResult(id=decision.reinforce_id, action=WriteAction.REINFORCED)
    mem_id = backend.insert(
        content=text,
        source=source,
        tags=tuple(tags or ()),
        confidence=confidence,
        embedding=vector,
        entity_names=entities.extract(text),
        supersede_ids=decision.supersede_ids,
    )
    if decision.supersede_ids:
        log.info("memory %d supersedes %s", mem_id, decision.supersede_ids)
    return WriteResult(
        id=mem_id, action=WriteAction.INSERTED, superseded=list(decision.supersede_ids)
    )


def recall(query: str, k: int = config.RECALL_K) -> list[Hit]:
    """Hybrid recall: dense + sparse -> RRF(k=60) -> rerank -> entity boost.

    Superseded and archived rows are structurally excluded (in SQL). If the
    embedder is down, degrades to sparse-only (logged); if the reranker is
    down, ranking rests on RRF order + entity boost. Raises
    :class:`MemoryUnavailable` only when the store itself is unreachable.
    """
    query = (query or "").strip()
    if not query:
        return []
    backend = get_backend()
    pool = recall_pool(k)

    dense: list[DenseRow] = []
    curated: list[DenseRow] = []
    try:
        vector = embed(query)
        dense = backend.dense_search(vector, pool)
        # Curated lane: the nearest identity/library rows ALWAYS enter the pool (their
        # own RRF list), so they reach the reranker even when the general pool is full
        # of facts — the diagnosed miss (a relevant Law never reaching rerank).
        curated = backend.curated_search(vector, config.CURATED_LANE_K, tuple(config.CURATED_SOURCES))
    except EmbedError as exc:
        log.warning("dense lane down (embed failed), sparse-only recall: %s", exc)
    sparse = backend.sparse_search(query, pool)

    fused = rrf_fuse([[r.id for r in dense], [r.id for r in sparse], [r.id for r in curated]])
    if not fused:
        return []
    meta: dict[int, tuple[str, str]] = {r.id: (r.content, r.source) for r in dense}
    for r in sparse:
        meta.setdefault(r.id, (r.content, r.source))
    for r in curated:
        meta.setdefault(r.id, (r.content, r.source))
    sims: dict[int, float] = {r.id: r.sim for r in dense}
    for r in curated:
        sims.setdefault(r.id, r.sim)

    candidates = sorted(fused, key=lambda i: fused[i], reverse=True)[:pool]
    scores = rerank(query, [meta[i][0] for i in candidates])  # degrades to zeros

    query_ents = entities.normalized_set(query)
    boosts: dict[int, float] = {}
    if query_ents:
        mem_ents = backend.entity_names(candidates)
        for cid in candidates:
            if query_ents & mem_ents.get(cid, set()):
                boosts[cid] = config.ENTITY_BOOST

    ranked = sorted(  # stable: ties keep RRF order
        zip(candidates, scores),
        key=lambda pair: (
            pair[1]
            + boosts.get(pair[0], 0.0)                                   # entity (GraphRAG) boost
            + config.SOURCE_BOOST.get(meta[pair[0]][1], 0.0)             # source-authority prior
        ),
        reverse=True,
    )
    top = ranked[:k]

    try:
        backend.touch([i for i, _ in top])  # frequency/recency signal
    except MemoryUnavailable as exc:
        log.warning("recall touch failed (non-fatal): %s", exc)

    return [
        Hit(
            id=i,
            content=meta[i][0],
            source=meta[i][1],
            score=round(float(s) + boosts.get(i, 0.0), 4),
            sim=round(sims.get(i, 0.0), 4),
        )
        for i, s in top
    ]


def core_recall() -> list[Hit]:
    """Return all live ``source='core'`` rows — identity/standing facts always
    injected ahead of RAG hits so the brain never loses Michael's ground truth.

    Returns [] (never raises) — the brain loop must never break because core
    facts are temporarily unreadable.
    """
    try:
        rows = get_backend().core_rows()
        return [Hit(id=int(r[0]), content=r[1], source=r[2], score=1.0, sim=1.0) for r in rows]
    except Exception as exc:
        log.warning("core_recall failed (degrading to empty): %s", exc)
        return []


def answer(query: str, k: int = config.RECALL_K) -> tuple[str | None, list[Hit]]:
    """No-fabrication gate: answer from memory only when confidently grounded.

    Returns ``(text, hits)`` when the best hit is semantically close
    (``sim >= ANSWER_MIN_SIM``), lexically on-topic (``overlap >=
    ANSWER_MIN_OVERLAP``), AND not about a different entity than the query
    (:func:`entity_grounds`); otherwise ``(None, hits)`` and the caller falls to the
    brain with the hits as context. The entity guard is what stops a confident-looking
    but wrong-subject fact (an Everest fact for a Kilimanjaro question) being served.
    """
    hits = recall(query, k)
    if not hits:
        return None, []
    best = hits[0]
    if not passes_gate(best.sim, lexical_overlap(query, best.content)):
        return None, hits
    if entity_grounds(query, best.content) is False:
        return None, hits  # best hit is about a DIFFERENT entity → don't answer it
    return best.content, hits


def list_memories(limit: int = 50, offset: int = 0) -> list[dict]:
    """Live memory rows behind the gauge (deck drill-down). Raises MemoryUnavailable."""
    return get_backend().list_memories(limit, offset)


def list_entities(limit: int = 100) -> list[dict]:
    """Entities behind the gauge (deck drill-down). Raises MemoryUnavailable."""
    return get_backend().list_entities(limit)


def decay() -> int:
    """Run the decay pass: rescore live rows, archive faded unprotected rows.

    Never deletes (zones are reversible). Returns rows archived this pass.
    """
    return get_backend().apply_decay()


def close() -> None:
    """Close the active backend (idempotent; safe at shutdown)."""
    global _backend
    with _backend_lock:
        if _backend is not None:
            _backend.close()
            _backend = None


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
    "decay",
    "decide_write",
    "get_backend",
    "init",
    "lexical_overlap",
    "passes_gate",
    "recall",
    "recall_pool",
    "reset",
    "rrf_fuse",
    "set_backend",
    "should_archive",
    "store",
]

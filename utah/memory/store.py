"""Postgres + pgvector store — production memory backend."""
from __future__ import annotations

import logging
import os
from typing import Sequence

log = logging.getLogger("utah.memory.store")

from utah import config, entities
from utah.memory.exceptions import MemoryUnavailable
from utah.memory.types import DenseRow, Neighbor, SparseRow

#: Ceiling for the deck drill-down queries (list_memories/list_entities). The daemon
#: handlers clamp user input already; this is defense-in-depth so a hostile or buggy
#: limit can never reach Postgres unbounded. Env-tunable, never config-coupled.
_LIST_MAX = int(os.environ.get("UTAH_MEMORY_LIST_MAX", "1000"))

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
    """Production backend: Postgres + pgvector over a bounded connection POOL.

    Each transaction borrows its own connection from a process-global, per-DSN pool
    (:mod:`utah.db_pool`), so concurrent in-daemon memory ops actually run concurrently
    instead of serializing behind one connection + RLock (B6). A dropped connection is
    recycled transparently on checkout; every psycopg error is wrapped in
    :class:`MemoryUnavailable`; all SQL is parameterized. Connections are configured once
    (autocommit + pgvector) — autocommit=True is the psycopg3 pattern for explicit
    ``conn.transaction()`` blocks (real BEGIN/COMMIT, not nested savepoints).
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn if dsn is not None else config.DB_DSN

    # -- connection management ------------------------------------------------

    def _pool(self):
        from utah import db_pool

        return db_pool.vector_pool(self._dsn)

    def _tx(self):
        """Context manager: one transaction on a pooled connection."""
        return _PgTransaction(self)

    def close(self) -> None:
        """Close the shared pgvector pool for this store's DSN (idempotent)."""
        from utah import db_pool

        db_pool.close_pool(self._dsn, vector=True)

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
        vec = self._vec(embedding)
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, 1 - (embedding <=> %s) AS sim, source FROM memory "
                "WHERE superseded_by IS NULL AND NOT archived AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s",
                (vec, vec, limit),
            ).fetchall()
        return [Neighbor(int(r[0]), r[1], float(r[2]), r[3]) for r in rows]

    def dense_search(self, embedding: Sequence[float], limit: int) -> list[DenseRow]:
        vec = self._vec(embedding)
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, source, 1 - (embedding <=> %s) AS sim FROM memory "
                "WHERE superseded_by IS NULL AND NOT archived AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s",
                (vec, vec, limit),
            ).fetchall()
        return [DenseRow(int(r[0]), r[1], r[2], float(r[3])) for r in rows]

    def curated_search(self, embedding: Sequence[float], limit: int,
                       sources: Sequence[str]) -> list[DenseRow]:
        """Top-*limit* nearest live rows restricted to *sources* (the curated lane).
        Identical to :meth:`dense_search` but source-filtered, so high-value rows
        (identity + the books) always get a fair shot at the reranker."""
        if not sources:
            return []
        vec = self._vec(embedding)
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, content, source, 1 - (embedding <=> %s) AS sim FROM memory "
                "WHERE superseded_by IS NULL AND NOT archived AND embedding IS NOT NULL "
                "AND source = ANY(%s) "
                "ORDER BY embedding <=> %s LIMIT %s",
                (vec, list(sources), vec, limit),
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
            names: list[str] = []
            seen: set[str] = set()
            for raw in entity_names:
                norm = entities.normalize(raw)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                names.append(norm)
            if names:
                ent_rows = conn.execute(
                    "INSERT INTO entity (name) SELECT unnest(%s::text[]) "
                    "ON CONFLICT (name) DO UPDATE SET mentions = entity.mentions + 1 "
                    "RETURNING id",
                    (names,),
                ).fetchall()
                ent_ids = [int(r[0]) for r in ent_rows]
                conn.execute(
                    "INSERT INTO mem_entity (mem_id, ent_id) "
                    "SELECT %s, unnest(%s::bigint[]) ON CONFLICT DO NOTHING",
                    (mem_id, ent_ids),
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
        """Live memory rows (newest first) behind the gauge — the deck drill-down.

        ``limit``/``offset`` are clamped to ``[0, _LIST_MAX]`` / ``>= 0`` here, not just in
        the daemon handlers — defense-in-depth so a hostile value can never reach Postgres
        unbounded through some future caller."""
        limit = max(0, min(int(limit), _LIST_MAX))
        offset = max(0, int(offset))
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
        """Entities by live-link count — the deck drill-down for the entity gauge.
        ``limit`` is clamped to ``[0, _LIST_MAX]`` (same defense as :meth:`list_memories`)."""
        limit = max(0, min(int(limit), _LIST_MAX))
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT e.name, count(me.mem_id) FROM entity e "
                "LEFT JOIN mem_entity me ON me.ent_id = e.id "
                "GROUP BY e.id, e.name ORDER BY count(me.mem_id) DESC, e.name LIMIT %s",
                (limit,),
            ).fetchall()
        return [{"name": r[0], "mentions": int(r[1])} for r in rows]


class _PgTransaction:
    """One transaction on a connection borrowed from the store's pool.

    Each instance checks out its OWN connection, so concurrent transactions on different
    threads never serialize (the B6 fix). Wraps every psycopg/pool error in
    :class:`MemoryUnavailable`; the connection is always returned to the pool, and the
    pool's ``check`` recycles a dropped connection on the next checkout.
    """

    def __init__(self, store: PostgresStore) -> None:
        self._store = store
        self._pool = None
        self._conn = None
        self._tx = None

    def __enter__(self):
        import psycopg

        try:
            self._pool = self._store._pool()
            self._conn = self._pool.getconn(timeout=config.DB_POOL_TIMEOUT)
        except MemoryUnavailable:
            raise
        except Exception as exc:  # PoolTimeout, connect failure, pool closed, …
            raise MemoryUnavailable(f"cannot get a memory connection: {exc}") from exc
        try:
            self._tx = self._conn.transaction()
            self._tx.__enter__()
            return self._conn
        except psycopg.Error as exc:
            self._pool.putconn(self._conn)   # return it; the pool's check recycles a bad one
            self._conn = None
            raise MemoryUnavailable(f"memory store transaction failed: {exc}") from exc

    def __exit__(self, exc_type, exc, tb) -> bool:
        import psycopg

        try:
            try:
                self._tx.__exit__(exc_type, exc, tb)
            except psycopg.Error as tx_exc:
                raise MemoryUnavailable(f"memory store commit failed: {tx_exc}") from tx_exc
            if exc_type is not None and issubclass(exc_type, psycopg.Error):
                raise MemoryUnavailable(f"memory store error: {exc}") from exc
            return False
        finally:
            if self._conn is not None and self._pool is not None:
                try:
                    self._pool.putconn(self._conn)
                except Exception as put_exc:  # noqa: BLE001 — never raise on caller path
                    log.debug("putconn failed for memory store connection: %s", put_exc)
            self._conn = None


# --------------------------------------------------------------------------

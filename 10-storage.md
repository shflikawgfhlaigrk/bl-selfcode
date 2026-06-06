# 10 — STORAGE audit (data layer)

> The database/persistence layer. Dirt (`~/.ace/*.db`) + web SOTA. Utah's choice
> (**Postgres + pgvector**) is **verified live on this Mac** (see §5).

## 1. What we had
- **One `ace.db` (191 MB)** doing everything (memory, FTS, vectors, runs, events,
  metrics, scheduler) + side DBs: `embed_cache.db` (77 MB), `market_data.db`
  (206 MB), `predictions.db`, `scheduler.db`, etc. — all **SQLite**.
- WAL mode (single-writer + many readers); sqlite-vec (vectors), FTS5, LanceDB
  (197 MB embed cache, inert), DuckDB present but idle.
- Hand-rolled float32 vector blobs (`struct`).

## 2. Why we did it
SQLite is embedded, zero-ops, atomic, the most-deployed DB on earth — the obvious
"move fast" choice for a single-user local app. One file = simple. WAL for
concurrency. It was the right *starting* call for a light workload.

## 3. What we didn't think about
- **Utah isn't light — it does a *ton* daily** (leads/probate/marketing/trading/
  memory/telemetry, many concurrent writers). **SQLite is single-writer** →
  `database is locked` storms (Ace's exact failure with ~58 writers on one file).
- **sqlite-vec brute-force + load-fails** (fd leak); **LanceDB** 197 MB for nothing.
- **No real concurrency, no MVCC, no network/replication path** → can't scale with
  the daily volume or a future multi-device move.
- Vectors/FTS/JSON spread across libraries instead of one engine.

## 4. What we're gonna change
- **PostgreSQL + pgvector as the PRIMARY store** — concurrent multi-writer **MVCC**
  (no single-writer ceiling), with **pgvector HNSW (dense) + `tsvector` FTS (sparse)
  + JSONB + `LISTEN/NOTIFY`** all in one engine. **DuckDB** = OLAP analytics tier
  (reads Postgres/Parquet, zero-copy). SQLite drops to, at most, tiny local config.
- **Durability:** Postgres WAL (group commit, crash-safe, archive/replication-ready)
  vs SQLite's single-writer WAL — real durability at volume.
- **Schema (sketch):** `memory(id, content, tags, source, confidence, provenance,
  block_label, superseded_by, embedding vector(384|1024), ts)` + `tsvector` gen
  col + **HNSW index** on `embedding`; `leads`, `probate`, `outreach_ledger`
  (UNIQUE for never-twice), `trades`, `events`, `crystals`, `world_model`. One
  `msgspec.Struct` ↔ row mapping (Objects audit).
- **Isolated:** Utah's own cluster `~/.utah/pgdata` (port 5433), never Ace's data.

## 5. How it helps — VERIFIED on this Mac
- **pgvector 0.8.2 smoke test PASSED** (isolated Utah cluster `:5433`, `utah` db,
  `CREATE EXTENSION vector`, real `<->` distance query returned correct). The core
  storage bet is proven, not theoretical.
| | Ace | Utah |
|---|---|---|
| writers | single (lock storms) | **MVCC, concurrent** |
| vectors | sqlite-vec brute-force, fd-leak | **pgvector HNSW** (native, verified) |
| engines | SQLite + Lance + DuckDB(idle) | **Postgres (hot) + DuckDB (OLAP)** |
| FTS/JSON | scattered | one engine (tsvector + JSONB) |
| scale | single-file ceiling | scales with daily volume; multi-device path |

Sources: pgvector https://github.com/pgvector/pgvector · Postgres-for-everything (concurrency/JSONB/LISTEN-NOTIFY) — standard; SQLite vs server tradeoff per the IPC/data web research.

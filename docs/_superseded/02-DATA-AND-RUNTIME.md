# 02 — Data & runtime: storage tiers, RAG, gbrain, MLX, config

The question that decides this whole layer: **what data is this, and who writes
it?** Ace's central mistake was forcing one SQLite file (`ace.db`) to be config
store + transactional log + analytics warehouse + vector index + message bus,
hammered by 51 processes + the test suite — which produced lock contention, test
pollution, and a 191 MB file with five `_*_bak_*` tables sitting inside it.

Utah uses the right tool per data shape. The discrimination rule:

| Engine | Shape it wins at | Writers | Utah uses it for |
|---|---|---|---|
| **SQLite (WAL)** | Embedded, single-writer, hot metadata, low-latency point reads/writes | **one** per file | `state.db`: agent run log, escalations, scheduler state, memory metadata, KV, crystals |
| **Postgres** | Concurrent OLTP, multi-writer, transactional integrity, network access | many | `utah` db: the outreach sent-ledger + suppression (must be transactional), anything multiple workers write at once |
| **DuckDB** | OLAP — columnar, vectorized scans over millions of rows | batch/append | `analytics.duckdb`: trade/signal history, lead analytics, backtests, any "scan-and-aggregate" |

### Why this split (the senior reasoning)

- **SQLite is not a multi-process message bus.** It's an embedded library with a
  *single writer*. Ace used it as shared mutable state across 51 processes → the
  `database is locked` storms. Utah: **one writer per SQLite file**, everyone
  else reads. Cross-process coordination goes over the **bus**, not the DB.
- **WAL (Write-Ahead Logging) — what it is and why it's on.** In WAL mode,
  writes append to a `-wal` sidecar and readers read the last committed snapshot:
  **readers never block the writer, the writer never blocks readers**, and a
  crash mid-write rolls back cleanly. The cost: the `-wal` file grows until a
  *checkpoint* folds it back into the main DB. Rule: **WAL on for all SQLite +
  a periodic `PRAGMA wal_checkpoint(TRUNCATE)`** (Ace's `ace.db-wal` was a healthy
  2.8 MB; the danger is an un-checkpointed WAL ballooning). This is the "RAG WAL
  difference" — same WAL mechanism applies to the vector/FTS DB; keep it on a
  *separate* SQLite file from `state.db` so heavy embed writes don't fight hot
  metadata reads.
- **Postgres earns its place only where there's genuine concurrency.** The
  outreach pipeline, once multiple workers send/suppress in parallel, needs real
  transactions and row locks — SQLite would serialize or corrupt. The
  sent-ledger's "never send twice" guarantee is a transactional invariant →
  Postgres with a unique constraint, not a SQLite best-effort.
- **DuckDB is the analytics tier.** "How did signals perform across regimes,"
  "lead conversion by metro," backtests — these scan huge ranges. DuckDB is
  columnar + vectorized and reads Parquet/Arrow directly; doing this in SQLite
  row-by-row is what made trade-history queries slow. **Never** point DuckDB at
  the hot transactional path; it's read-mostly OLAP.

---

## 2. RAG / memory (grounded, no fabrication)

The confabulation incidents were **not** the vector store's fault — they were
*admission* (backfill injected synthetic rows) and *recall* (weak guards served
code/instructions/stale rows as answers). Utah keeps the storage and rebuilds the
guards as a clean, layered contract.

**Storage:**
- **FTS5 (BM25)** for lexical recall — proven, fast, the workhorse.
- **HNSW vector index** for semantic recall — `sqlite-vec`/ruvector HNSW on a
  *separate* SQLite file (`memory.db`, WAL on). HNSW > brute-force for speed at
  scale.
- **Embeddings: MLX local** (BGE/jina class). Local-first, no API.

**The no-fabrication contract (layered, ported from Sovereign's clean
`overlap_score` + Ace's hard-won guards):**
1. **Admission gate** — a row may enter only via (a) a sensor/tool observing it,
   (b) organic chat→memory consolidation, or (c) explicit "remember this." **No
   backfill. No synthetic events. No model summaries written backward.**
2. **Source discipline** — every row carries `source`, `confidence`,
   `reinforcement_count`, `provenance`. No anonymous guesses.
3. **Recall guard** — overlap score (query content-words present in the answer ≥
   threshold) + poison filter (skip code/instruction/known-stale rows) +
   live-data short-circuit (questions about "now/today/price/weather" skip memory
   and hit the live sensor path). `answer()` returns **None** when unsure — "I
   don't know" beats a confident wrong answer.
4. **Approval tier (verification ladder)** — SENSOR → MEMORY → CROSS-REFERENCE →
   HUMAN. Only HUMAN-tier facts may trigger irreversible actions.
5. **Decay, never delete** — rows flip active→archived→expired by a recency +
   frequency + confidence score; archived rows are excluded from default recall
   but recoverable. (Ace defined this and never turned it on; Utah runs it.)

## 3. gbrain consolidation (one brain, not two)

Ace ran a separate `com.ace.brain` (gbrain) knowledge service *alongside*
`semantic_memory` — two stores, used for different lookups (gbrain for historical
trade/page recall). That's a second source of truth waiting to drift. **Utah has
exactly one memory service.** Migration:
- Stand up Utah's grounded store first (above).
- One-time **export gbrain's durable pages/facts → import through Utah's
  admission gate** (so they get source + confidence stamped, not blind-copied).
- Fold gbrain's genuinely useful *capabilities* (graph traversal, recall ranking)
  into the one service as features, not a second daemon.
- Do **not** run a separate brain process in Utah. One store, one writer, one
  truth.

## 4. MLX & the inference stack (keep, but simplify)

MLX local inference is the most genuinely *tuned-on-this-Mac* asset Ace has — keep
it, change the lane policy.

**Keep (port-clean):**
- The L0–L4 tier model and the **L1 fast voice path** (Qwen-14B class, warm at
  boot) — chosen by real latency probing; expensive to re-derive.
- The MLX client wrapper: warm-start hook, LRU tier eviction (10-min idle TTL on
  big tiers), **2 GiB Metal cache cap** (this is what stopped the 161 GB
  OOM-to-jetsam crash), per-generate cache clear.
- The **thermal ratchet** (pmset → step tier down under thermal pressure) —
  learned through crashes; load-bearing on this laptop.
- The voice pipeline state machine + pre-roll ring buffer + **Piper-only TTS**
  with markdown/reasoning-tag stripping (Kokoro/XTTS are gone — don't bring them
  back).
- whisper.cpp STT.

**Change / discard:**
- **Discard the Anthropic *API* lane entirely.** Utah is local-first; the only
  paid escalation is the **Claude CLI subprocess** (subscription, no per-token
  surprise). All API toggles gone, not just defaulted off — this kills the
  "silent API spend on local timeout" failure mode at the source.
- **Unify the model loaders.** Ace had main tiers + sub-tiers + speculative-draft
  + classifier as four loaders; collapse to one loader + optional adapters +
  optional speculative draft.
- **Retrain the complexity classifier** on Utah's own corpus (it was fit to
  Ace's prompts).

## 5. Config & objects (one typed source of truth)

Ace scattered config across `config.yaml`, `toggles.json`,
`essential_agents.json`, `probate_feeds.json`, `phone_alerts.yaml`,
`credentials.json` → drift and "which file wins?" confusion.

Utah: **one `~/.utah/config.yaml`**, loaded through a **typed/validated schema
(pydantic)** at boot — identity, brain/CLI, memory, tiers, dashboard, voice,
agents_dir, feature flags. Invalid config fails loudly at startup, not silently
at runtime. **Secrets live only in the macOS Keychain** (service prefix
`com.utah.secret`), never on disk, never in the repo. Frozen runtime objects
(contracts, schemas) are typed and versioned; you extend the schema, you never
mutate a frozen field.

---

## 6. WIN, restated as the runtime spine of speed

(Full mechanism in `01-FOUNDATION.md` §5.) In runtime terms: the
`com.utah.win` process owns `win.sock`, ingests producer windows (feed/voice/
telemetry), and fans them to subscribers zero-copy. The daemon's control plane
(`utah.sock`, JSON-RPC) and the data plane (`win.sock`, binary) are physically
separate so neither can stall the other. This is the structural reason Utah is
fast *and* stable where Ace was neither: the bursty numeric traffic that used to
clog the one JSON socket now has its own binary lane.

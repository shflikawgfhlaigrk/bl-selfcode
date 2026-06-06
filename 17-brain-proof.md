# 17 — BRAIN PROOF (step 1 built, wired, proven better than gbrain + Ace)

> Scope: **the brain only** (memory + reasoning). No processes/daemons/IPC/PIDs/
> RPCs — those are later substrate. This doc is the proof that Utah's brain is
> fully built, internally wired, and beats both reference brains on the things
> that actually broke Ace. Every Utah claim is a live result from this machine;
> every Ace claim is from Ace's own incident log; every gbrain claim is from its
> own docs.

## What the brain is (the whole of step 1)

`recall → ground → reason → remember`, on Postgres + pgvector, free-everything
except the Claude CLI reasoning lane. Nine modules, ~700 LOC core, one pipeline
for live turns AND consolidation (no side door):

- **embed** — fastembed BGE-small (ONNX, no torch), 384-dim; validates dim +
  finite values → a row is never stored with a bad vector.
- **rerank** — fastembed ms-marco cross-encoder; degrades to RRF order if down.
- **memory** — pure decision logic (`decide_write`/`rrf_fuse`/`compute_decay`/
  `passes_gate`) + `StoreBackend` protocol + `PostgresStore` (one managed conn,
  reconnects, no leaks). Hybrid dense+FTS → RRF(60) → cross-encoder → entity
  boost; admission gate (`ALLOWED_SOURCES`, size, confidence); dedup-reinforce;
  supersede (paraphrase OR shared-entity "Newman" path, whole-window scan);
  decay/archive (reversible, never delete).
- **brain** — `claude -p`, structured failure (`BrainUnavailable`), `extract_facts`
  returns `None` on brain-down so consolidation **retries, never drops a turn**.
- **entities / objects / consolidate / core** — graph extraction, immutable
  msgspec types, sleep-time promotion, the `tell()` orchestrator.

## Proof (live, this machine, 2026-06-06)

| Layer | Evidence | Result |
|---|---|---|
| Pure logic + full pipeline on fakes | `pytest tests/` | **123 passed**, 0 failed |
| Real Postgres + pgvector store (schema, SQL, supersede atomicity, FTS, decay parity) | `tests/test_integration_pg.py` on disposable `utah_test` | **11 passed** |
| Real models + real pgvector end-to-end (store→dedup→supersede→recall→rerank→no-fab→decay) | `/tmp/utah_e2e.py` | **9/9 passed**, 0.4 s |
| Embedder | real BGE-small load + embed | 384-dim, 0.51 s cold / 3 ms warm |
| Reranker | real ms-marco cross-encoder | relevant doc +7.6 vs −11.3 |
| Reasoning lane | `claude -p` grounded + refusal | grounds from context; **"I don't know"** on unsupported |
| Substrate | pgvector | **0.8.2**, HNSW + FTS GIN indexes live on `:5433` |
| Zero-injection | probe leak removed | live `utah` left clean |

**Total: 134/134 tests green with real Postgres; full loop proven with real
models; reasoning lane grounds and refuses to fabricate.**

## Why it beats Ace's brain (Ace's own incident log = the evidence)

| Axis | Ace's brain (documented) | Utah's brain (proven) |
|---|---|---|
| Vector ANN | sqlite-vec `vec_semantic` "fails to load every attempt" → ANN **down**, recall degraded to FTS-only; ~1007-fd-leak → Errno-24 crash loop | pgvector **HNSW**, proven up; one managed connection, no leaks |
| Hybrid + RRF | built but **OFF** by default; BM25 lane silently dead → RRF never ran in prod | hybrid **ON**, RRF fusion proven, cross-encoder rerank on top |
| No-fabrication | 5 reactive chat-regexes; backfill (110 rows), synthetic-event, Kokoro re-poison all got **in** | **structural**: `ALLOWED_SOURCES` denies backfill/synthetic/scraped at admission; sim+overlap answer gate proven to return `None` (not a guess) |
| Contradiction | supersede was top-1 only ("Newman bug") | whole-window scan, paraphrase **and** entity path — proven |
| Consolidation | threshold-only; turns dropped | `extract_facts→None` on brain-down ⇒ retry, never drop |
| Stale-on-live | guarded by hardcoded `_LIVE_CUES` regex (per-incident) | structural: no admitted live fact to serve → gate returns `None` ("P&L today" → None, proven) |

## Why it beats gbrain for this purpose (gbrain's own docs = the evidence)

Same retrieval core (hybrid HNSW+FTS→RRF→cross-encoder→entity boost→no-fab),
but: gbrain is 133K LOC, multi-tenant (OAuth/admin-SPA/job-queue), and its
**default embeddings are paid** (OpenAI/Voyage, per-token). Utah is ~700 LOC,
**$0 per token** (fastembed ONNX), single-owner, and reasons with the **frontier
Claude CLI** instead of a configured chat model. gbrain's no-fabrication is
prompt-level in its `think` pipeline; Utah's is **structural** (admission +
gate). For "Ace, finally fixed, on a baseline that works," Utah's brain is the
better fit and stronger on the exact axes Ace failed.

## Deliberately NOT in the brain (later, by design — not gaps)

Compound-phase / out of scope for step 1: brain-assisted entity extraction
(today's is the cheap regex path — code says so), multi-hop graph traversal,
timeline/takes/calibration, multi-source federation, recency-weighted ranking
(decay is archival today). **Substrate (daemons/IPC/PIDs/RPCs/cron/service
wiring) is explicitly later** — the brain is a standalone, proven library.

## Open flags (your call)

1. Live `utah` holds 12 demo/probe rows (seed facts + a consolidation demo +
   rows an earlier probe agent wrote: "Ava", "Tesla Model 3", a 30-day-month
   turn). None are real conversation data; wipe to clean seed on request.
2. Minor (interface-layer, not brain-core): `claude -p` sometimes thinks aloud
   in its answer ("...wait, that directly answers it..."). Output-cleanup
   belongs to the voice/chat surface, not the brain.

# 9 — RAG audit (retrieval & recall)

> Embeddings, vector + lexical search, fusion, reranking, and the no-fabrication
> grounding. Dirt (`semantic_memory.py`, `memory_first.py`, `rag.py`) + web SOTA.

## 1. What we had
- **Embeddings:** **BGE-small-en-v1.5 (384-dim)** via `LocalClient.embed` (MLX);
  bge-large (1024-dim) for `knowledge_ingest`.
- **Vector store:** sqlite-vec `vec_semantic` (**brute-force; fails to load →
  fd leak**). **Lexical:** FTS5 `semantic_fts` (BM25).
- **Hybrid EXISTS but is OFF:** `hybrid_search()` + `rrf_fuse()`
  (`semantic_memory.py:549`) **gated by `memory.hybrid_enabled=False`** → falls back
  to dense-only `search()`; and a **dead import silently no-op'd the BM25 lane** →
  RRF never ran in production.
- **Recall guards** (`memory_first.py`): `_LIVE_CUES` short-circuit, the
  **LoRA-poison residence short-circuit** (Austin/Newnan/Adelaide), `overlap≥0.6`,
  `q_key`, poison filter.
- **Two RAGs:** `semantic_memory` + **gbrain/Dory** (hybrid + decay).
- Live: **10,457** memory rows / 10,457 FTS / 443 entities (~4.5k embedded).

## 2. Why we did it
Local-first: BGE on MLX, sqlite-vec for embedded ANN, FTS5 for keywords,
memory-first recall to cut latency and stop the model from confabulating. Hybrid +
RRF were *built* but left OFF (conservative). The recall guards were added
reactively, each after a real confabulation bite.

## 3. What we didn't think about
- **sqlite-vec is brute-force + load-fails** → no real HNSW, and the fd-leak crash.
- **Hybrid was off by default + the BM25 lane was silently dead** → recall was
  dense-only/degraded; **RRF never executed in prod.**
- **No reranker** → precision capped on a growing corpus.
- **Confabulation got INTO RAG** (110 backfill rows, LoRA residence, synthetic
  events) — admission wasn't gated; guards were **5 Ace-chat-specific regexes**,
  reactive not structural.
- **Two RAGs** (semantic_memory + gbrain) → drift, double truth.
- Heavy **torch/sentence-transformers** footprint just for embeddings.

## 4. What we're gonna change
- **One hybrid engine, ON by default:** **pgvector HNSW (dense) + Postgres
  FTS/`tsvector` (sparse) → RRF (k≈60)** — real HNSW, no brute-force/fd-leak, in
  the primary DB (scales with daily volume).
- **Cross-encoder reranker** (local/MLX) as a second pass, engaging as the corpus
  grows (>~50k chunks) — the 2026 production pattern.
- **No-fabrication as a generic contract** (not chat regexes): **admission gate**
  (sensor / organic consolidation / explicit "remember" only — **no backfill, no
  synthetic**) + **source/confidence/provenance** on every row + **recall guard**
  (overlap + live-data short-circuit + poison filter) → returns **"I don't know"**
  below threshold.
- **GraphRAG:** entity graph wired into consolidation + **entity-boosted rerank**.
- **One memory service** (fold gbrain/Dory) with decay + sleep-time consolidation;
  embeddings via MLX (evaluate dropping torch; Apple FM entity-extraction feeds the
  graph).

## 5. How it helps
| | Ace | Utah |
|---|---|---|
| ANN | sqlite-vec brute-force, load-fails | **pgvector HNSW** (reliable, real) |
| hybrid | built but **OFF** + dead BM25 lane | **dense+BM25 → RRF, ON** |
| rerank | none | cross-encoder second pass |
| fabrication | reactive regex guards; backfill got in | **structural** admission + guard + provenance |
| graph | stranded | entity-boosted recall |
| services | 2 RAGs (drift) | **1** (pgvector, dense+sparse+graph) |

Net: reliable HNSW (no crash), hybrid that actually fuses, reranked precision,
confabulation killed at admission (not whack-a-mole), one engine that scales with
the volume.

Sources: hybrid + rerank + RRF 2026 https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026 · BM25+HNSW+RRF https://ashutoshkumars1ngh.medium.com/hybrid-search-done-right-fixing-rag-retrieval-failures-using-bm25-hnsw-reciprocal-rank-fusion-a73596652d22 · production hybrid+rerank https://appscale.blog/en/blog/hybrid-search-and-reranking-production-rag-bm25-dense-cross-encoder-2026 · advanced RAG (graph) https://myengineeringpath.dev/genai-engineer/advanced-rag/

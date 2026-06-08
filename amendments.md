# AMENDMENTS & RECONCILIATION (2026-06-06) — read for audit consistency

## A. Capabilities, not agents (structure is an implementation choice)

The Baseline Feature Catalog lists **capabilities — what Utah must DO** — not an
agent roster. **Ace's 73 agents are NOT a template; that sprawl is exactly what
Part III kills.** Each capability is implemented as whatever is simplest and
fewest-moving-parts:

- **a tool the frontier agentic harness calls on demand** (most "agents" collapse
  to this),
- **an MCP** (external/standardized capabilities),
- **a cron** (the daily pipelines: leads / probate / marketing / trading-brief /
  memory-consolidation / morning-brief),
- **a sensor pusher** into the world-model (weather/calendar/mail/location/market),
- or **a plain module/function**.

Consolidate ruthlessly. The **only** hard requirement: *everything Michael wants
it to do, it does — as a fully autonomous partner.* Unit count is not a goal;
capability coverage + autonomy is. (So Catalog §5's "73 agents" = a capability
inventory to cover, **not** a build target for number of resident units.)

## B. Apple Intelligence in the speech / inference path (researched 2026-06-06)

All on-device, free, native, zero-dependency — strong local-first fit (macOS 26).

- **STT — Apple `SpeechAnalyzer` / `SpeechTranscriber` (macOS 26+):** runs on the
  Neural Engine; **~55% faster than Whisper Large-V3-Turbo** (34-min file in ~45s),
  **no model download**, free, native. Limits: macOS-26-only (fine — this Mac),
  single-language per recording, no custom vocabulary. **→ DEFAULT STT candidate
  for Utah.** Keep **Moonshine / Parakeet** as the ultra-low-latency *streaming*
  alternative; benchmark both on this Mac at the gate.
- **Cheap structured LLM — Apple `Foundation Models` framework (macOS 26+):**
  on-device **~3B**, free, no keys, no internet, Swift-native, with **guided
  (structured) generation, tool-calling, and entity-extraction / content-tagging
  adapters**. Great for **intent routing, classification, summarization, and
  entity extraction (feeds memory)** — offloading those from MLX. **NOT** world
  knowledge / heavy reasoning. **→ Use as a free fast/structured tier alongside
  MLX.** The agentic-harness brain stays **Claude/frontier**; MLX keeps the strong
  local tier.
- **TTS — Apple `AVSpeechSynthesizer` + Personal Voice (built-in clone):** native,
  free; naturalness likely below F5/StyleTTS2. **→ Personal Voice = a zero-dep
  clone fallback;** F5/StyleTTS2 stay the natural-voice picks for Utah (personal).
- **Honest caveats:** macOS-26 binding; the 3B model's reasoning ceiling (never the
  harness brain); single-language STT.
- Sources: SpeechAnalyzer speed https://www.macrumors.com/2025/06/18/apple-transcription-api-faster-than-whisper/ · WhisperKit vs SpeechAnalyzer https://vocai.net/blog/whisperkit-vs-speechanalyzer-2026/ · Foundation Models docs https://developer.apple.com/documentation/FoundationModels · Apple FM update https://machinelearning.apple.com/research/apple-foundation-models-2025-updates

## C. Reconciliation index (these supersede earlier text; the doc reads as)

For the auditors — earlier sections predate the 2026-06-06 ground rules. Apply:

1. **Storage:** every "SQLite primary / memory = SQLite+FTS5+HNSW / SQLite(WAL)
   hot store" (Part II BOM rows, §2 data-stores, §4.5 quantified, web-audit §A,
   Part IV §IV.4 memory) → **PostgreSQL + pgvector as the primary store**
   (concurrent multi-writer OLTP at volume). **DuckDB** = OLAP analytics; **SQLite**
   = at most tiny local config, not the hot path. usearch/hnswlib insight is
   absorbed by **pgvector's HNSW**.
2. **TTS license:** F5-TTS CC-BY-NC restriction applies to **Sovereign (sold)**,
   **not Utah (personal)** — Utah may use F5-TTS.
3. **"Agents":** everywhere read as **capabilities**; structure = implementation
   choice (tool/MCP/cron/function), consolidated; bar = fully autonomous partner.
4. **Roster (Catalog §5, 73 agents):** capability inventory to COVER, not a unit-
   count build target.
5. **Frontend:** one web surface (Svelte/React), SwiftUI HQ killed — unchanged.
6. **Inference lanes:** add Apple SpeechAnalyzer (STT) + Foundation Models (free
   structured tier) to the MLX + Claude-CLI(harness) stack; Anthropic API lane
   stays deleted.

Everything else in Parts I–IV + web-audit + ground rules stands. With this index
the spec is internally consistent: **Postgres+pgvector primary, capabilities-not-
agents, Apple-Intelligence speech option, frontier agentic harness as the
interface, Utah personal / Sovereign sold.**

---

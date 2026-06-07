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

#: ``--tools ""`` disables ALL built-in tools and ``--strict-mcp-config`` (with no
#: ``--mcp-config``) loads no MCP servers — so the brain runs as a pure LLM that
#: ANSWERS, not as the Claude Code agent. Without these, ``claude -p`` goes
#: agentic: it explores files / runs bash ("the paths") instead of answering, and
#: a turn times out narrating tool steps. Subscription auth is unaffected.
BRAIN_NO_AGENT: tuple[str, ...] = ("--tools", "", "--strict-mcp-config")

#: Arguments for one-shot print mode.
BRAIN_ARGS: tuple[str, ...] = ("-p", *BRAIN_NO_AGENT)

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
    *BRAIN_NO_AGENT,
)

#: Seconds to wait for one brain turn before declaring it unavailable.
BRAIN_TIMEOUT: int = int(os.environ.get("UTAH_BRAIN_TIMEOUT", "120"))

#: Seconds to wait when opening a Postgres connection.
DB_CONNECT_TIMEOUT: int = int(os.environ.get("UTAH_DB_CONNECT_TIMEOUT", "5"))

# --- L1: the local lane (free, resident Ollama models in FRONT of the brain) --
#: Ollama HTTP endpoint. The local tier is free; only the brain (Claude CLI) is paid.
OLLAMA_URL: str = os.environ.get("UTAH_OLLAMA_URL", "http://127.0.0.1:11434")
#: Quick tier — a fast instruct model (sub-second). Answers most quick things.
LOCAL_QUICK_MODEL: str = os.environ.get("UTAH_LOCAL_QUICK", "llama3.2:3b")
#: Heavy tier — the "20-gig" resident reasoner (native thinking), still free.
LOCAL_HEAVY_MODEL: str = os.environ.get("UTAH_LOCAL_HEAVY", "deepseek-r1:32b")
#: keep_alive pins both models resident ("always ready", at ~10 procs not ~120).
LOCAL_KEEP_ALIVE: str = os.environ.get("UTAH_LOCAL_KEEP_ALIVE", "30m")
#: Seconds to wait for one local turn before declaring it unavailable (-> escalate).
LOCAL_TIMEOUT: int = int(os.environ.get("UTAH_LOCAL_TIMEOUT", "90"))
#: Skip the local tier and escalate straight to the brain when the machine's 1-min
#: load average PER CORE is at/above this. A CPU-starved Ollama call (esp. the 32B
#: reasoner) would just burn LOCAL_TIMEOUT and fail, so escalate NOW instead of
#: wasting it. 0 disables the guard (always attempt the local tier).
LOCAL_SKIP_LOAD_PER_CORE: float = float(os.environ.get("UTAH_LOCAL_SKIP_LOAD", "2.5"))
#: Answer-token caps (the heavy reasoner needs room for its thinking + answer).
LOCAL_QUICK_MAX_TOKENS: int = int(os.environ.get("UTAH_LOCAL_QUICK_MAX", "512"))
LOCAL_HEAVY_MAX_TOKENS: int = int(os.environ.get("UTAH_LOCAL_HEAVY_MAX", "1024"))

# --- router doctrine (the cheapest tier that can answer; misses escalate) -----
#: At/above this word count, a non-capability query leans to the heavy local tier.
ROUTER_HEAVY_MIN_WORDS: int = 18

# --- learn-on-miss (find → understand → remember, then answer) ----------------
#: When the brain REFUSES a world-knowledge question ("I don't know."), go learn
#: it: the researcher searches the web, fetches, extracts GROUNDED facts into
#: memory through the admission gate, then the brain re-reasons over the fresh
#: recall. This keeps no-fabrication intact (the brain still only answers from
#: CONTEXT — we just populate the context with real fetched facts first) AND lets
#: Utah compound: the next identical question is an instant memory recall. Scoped
#: to factual-recall turns so personal/agentic misses stay fast. 0 disables it.
LEARN_ON_MISS: bool = os.environ.get("UTAH_LEARN_ON_MISS", "1") not in ("0", "", "false", "no")
#: How many web sources the learn-on-miss pass fetches (in PARALLEL, no per-source
#: brain extraction). Kept small so a cold miss costs a few seconds, not a crawl;
#: subsequent asks are free recall.
LEARN_ON_MISS_SOURCES: int = int(os.environ.get("UTAH_LEARN_ON_MISS_SOURCES", "3"))

# --- clock capability (time/date — a model cannot know the current instant) ----
#: Michael's timezone (Gulf Shores, AL = Central). Invalid → system-local fallback.
TIMEZONE: str = os.environ.get("UTAH_TIMEZONE", "America/Chicago")

# --- weather capability (R-weather: free, grounded, cached) -------------------
#: Default location (Gulf Shores, AL); env-overridable per machine.
WEATHER_LAT: float = float(os.environ.get("UTAH_WEATHER_LAT", "30.2460"))
WEATHER_LON: float = float(os.environ.get("UTAH_WEATHER_LON", "-87.7008"))
WEATHER_LABEL: str = os.environ.get("UTAH_WEATHER_LABEL", "Gulf Shores, AL")
#: Cache TTL — the spec's "cache <2h". Stale-on-fetch-failure is served, marked.
WEATHER_CACHE_SECONDS: int = int(os.environ.get("UTAH_WEATHER_CACHE", "7200"))
WEATHER_CACHE_PATH: str = os.environ.get(
    "UTAH_WEATHER_CACHE_PATH", os.path.expanduser("~/.utah/cache/weather.json")
)

# --- embedding ---------------------------------------------------------------
#: Free, no-torch: fastembed (onnxruntime) BGE-small, 384-dim.
EMBED_MODEL: str = "BAAI/bge-small-en-v1.5"
EMBED_DIM: int = 384

#: Cross-encoder rerank model (fastembed ONNX, free).
RERANK_MODEL: str = "Xenova/ms-marco-MiniLM-L-6-v2"

# --- voice (wake "ace" -> Moonshine STT -> brain -> Piper TTS) ---------------
#: Default STT = Moonshine ONNX (very-low-latency, on-device, auto-downloads).
STT_MODEL: str = os.environ.get("UTAH_STT", "moonshine/base")
#: MLX Whisper model — the swappable fallback STT (set engine via stt.set_stt).
WHISPER_MODEL: str = os.environ.get("UTAH_WHISPER", "mlx-community/whisper-base.en-mlx")
#: Piper TTS voice model (the .json config sits next to it).
PIPER_MODEL: str = os.environ.get(
    "UTAH_PIPER", os.path.expanduser("~/.utah/models/piper/en_GB-cori-high.onnx")
)

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
#: ``core`` = always-injected identity/creed; ``knowledge`` = curated reference
#: corpus (the books) — recalled on demand, but never superseded or decayed.
ALLOWED_SOURCES: frozenset[str] = frozenset(
    {"user", "turn", "fact", "consolidation", "sensor", "core", "knowledge"}
)

#: Authoritative sources a later write must NEVER supersede — curated ground truth
#: (the identity creed and the reference library). A distinct fact about an evolving
#: attribute still supersedes another fact (the Newman path); it just can't collapse
#: these. Without this, loading the 13 "Think and Grow Rich" principles collapsed to 3.
NEVER_SUPERSEDE_SOURCES: frozenset[str] = frozenset({"core", "knowledge"})

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

#: Curated sources (the identity creed + the reference library) are ~150 high-value
#: rows competing against thousands of facts. Two mechanisms keep them reachable:
#: a guaranteed retrieval lane and a small ranking prior.
CURATED_SOURCES: frozenset[str] = frozenset({"core", "knowledge"})

#: Top-N nearest curated rows are ALWAYS merged into the candidate pool (as their own
#: RRF lane), so they reach the reranker even when the general pool is swamped by the
#: fact pile — the diagnosed root cause (a relevant Law never even reached rerank).
CURATED_LANE_K: int = 5

#: Additive ranking prior per source — a source-authority prior on the rerank score.
#: Bounded (≈ENTITY_BOOST scale) so a STRONG match in ANY source still wins outright;
#: it only tips the LOW-confidence regime (vague query, nothing scores well) toward
#: curated wisdom over low-value migrated facts. It shifts rerank ORDER only, never a
#: hit's ``sim``, so the no-fabrication answer gate (which reads sim) is unaffected.
SOURCE_BOOST: dict[str, float] = {"core": 1.5, "knowledge": 1.0}

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
DECAY_PROTECTED_SOURCES: frozenset[str] = frozenset(
    {"fact", "consolidation", "core", "knowledge"}
)

# --- brain prompt budget --------------------------------------------------------
#: Hard cap on context characters passed to the CLI (argv size safety).
BRAIN_CONTEXT_MAX_CHARS: int = 8_000

#: Max facts accepted from one extraction pass.
MAX_FACTS_PER_TURN: int = 8

#: Max characters per extracted fact.
MAX_FACT_CHARS: int = 500

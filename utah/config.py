"""Utah config — one place, env-overridable at the deploy seams only.

Doctrine: free-everything except the Claude CLI (the one allowed paid lane).
Connection/command settings are env-overridable because they differ per machine;
the decision thresholds are doctrine constants — they are part of the spec, have
direct unit tests, and are NOT runtime-tunable (changing them is a spec change).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# --- deploy seams (env-overridable) -----------------------------------------
#: Canonical owner Gmail — the ONLY send/login identity. Ace historically typo'd
#: ``mthburnsbarber`` (stray ``h``); normalize every ingest path to this.
OWNER_EMAIL: str = os.environ.get("UTAH_OWNER_EMAIL", "mtuburnsbarber@gmail.com")
_OWNER_TYPO_EMAIL: str = "mthburnsbarber@gmail.com"


#: Postgres DSN. Production (Michael's Mac): socket at /tmp, cluster on :5433.
DB_DSN: str = os.environ.get("UTAH_DSN", "host=/tmp port=5433 dbname=utah")

#: The brain command — Claude CLI on PATH (subscription; the one paid lane).
BRAIN_CMD: str = os.environ.get("UTAH_BRAIN", "claude")


def normalize_owner_email(email: str | None) -> str:
    """Map the known ``mthburnsbarber`` typo → :data:`OWNER_EMAIL`."""
    e = (email or "").strip()
    if e.lower() == _OWNER_TYPO_EMAIL.lower():
        return OWNER_EMAIL
    return e

#: CAN-SPAM placeholder until Michael provides a real postal address.
CANSPAM_PLACEHOLDER: str = (
    "[CAN-SPAM physical address — Michael's business input, required to send]"
)

UTAH_HOME: Path = Path(os.environ.get("UTAH_HOME", os.path.expanduser("~/.utah")))
BUSINESS_CREDS: Path = UTAH_HOME / "secrets" / "business.json"


#: A complete US postal address carries a 5-digit ZIP (optionally ZIP+4).
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def _canspam_is_real(addr: str) -> bool:
    a = (addr or "").strip()
    if not a:
        return False
    low = a.lower()
    return "[can-spam" not in low and "replace" not in low and a != CANSPAM_PLACEHOLDER


def _canspam_is_complete(addr: str) -> bool:
    """CAN-SPAM requires a *valid physical postal address*, not just any string. A bare
    street ("28 Dogwood Rd") is non-compliant; a complete one carries a ZIP. We gate the
    SEND on completeness so a volume cold-email run can never ship a non-compliant footer."""
    return _canspam_is_real(addr) and bool(_ZIP_RE.search(addr))


def canspam_address() -> str:
    """Real CAN-SPAM postal address: ``UTAH_CANSPAM_ADDRESS`` env, then ``business.json``."""
    env = os.environ.get("UTAH_CANSPAM_ADDRESS", "").strip()
    if _canspam_is_real(env):
        return env
    try:
        data = json.loads(BUSINESS_CREDS.read_text(encoding="utf-8"))
        addr = (data.get("physical_address") or "").strip()
        if _canspam_is_real(addr):
            return addr
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass
    return CANSPAM_PLACEHOLDER


def canspam_configured() -> bool:
    """True only when a COMPLETE postal address is set (env or business.json) — a bare
    street is non-compliant and must not unlock volume sending."""
    return _canspam_is_complete(canspam_address())


# --- Outreach cadence doctrine (Michael, 2026-06-09) ------------------------
#: Structural no-fabrication proof (audit TIER3): after the brain answers, verify each
#: salient numeric claim traces to a span in the CONTEXT. ADVISORY by default (records an
#: ``unsupported_claim`` audit event + an attribution score); set strict to DOWNGRADE an
#: answer with an unsupported number to "I don't know." — no-fab proven, not just prompted.
BRAIN_ATTRIBUTION_STRICT: bool = os.environ.get("UTAH_ATTRIBUTION_STRICT", "0") == "1"


#: Cold outreach SENDS only inside the local-clock business window, enforced in the
#: SEND PATH (not just the cron schedule) so a manual kickstart or a misconfigured
#: plist can NEVER text/email a prospect at 5am again. ``START`` inclusive, ``END``
#: inclusive — a run kicked at exactly 17:00 (5pm close) still sends.
OUTREACH_HOUR_START: int = int(os.environ.get("UTAH_OUTREACH_HOUR_START", "8"))
OUTREACH_HOUR_END: int = int(os.environ.get("UTAH_OUTREACH_HOUR_END", "17"))
#: Per-run (== per-hour, the cron fires hourly) send cap. 50/hour × 10 business
#: hours (08:00–17:00) = 500/day, Michael's floor. Raise only with deliverability headroom.
OUTREACH_PER_HOUR: int = int(os.environ.get("UTAH_OUTREACH_DAILY", "50"))


def within_business_hours(hour: int | None = None) -> bool:
    """True iff *hour* (0–23, local) is inside the outreach send window. With no arg,
    reads the live local clock — the single gate the send path consults so off-hours
    sends are structurally impossible."""
    if hour is None:
        import datetime
        hour = datetime.datetime.now().hour
    return OUTREACH_HOUR_START <= hour <= OUTREACH_HOUR_END

#: System prompt that makes the brain a pure reasoning engine. ``--tools ""`` +
#: ``--strict-mcp-config`` disable the TOOLS, but NOT the Claude Code agent SYSTEM
#: PROMPT — so on a code/file-shaped question the model still believes it is a coding
#: agent and narrates "LSP isn't installed, let me read the file directly" (or emits
#: an ``<invoke name="Read">`` block straight into the answer), because the tools it
#: wants are gone. Appending this overrides that agent identity. PROVEN to eliminate
#: the narration AND tool-call emission in both one-shot and streaming paths while the
#: no-fabrication contract still holds (empty-context world fact still → "I don't know.").
BRAIN_SYSTEM_PROMPT: str = (
    "You are a pure text reasoning engine, NOT an agent or coding assistant. You have "
    "NO tools, NO file access, NO LSP, NO shell, NO ability to read, grep, open, or run "
    "anything. Never narrate reading/grepping/opening files and never emit a tool call. "
    "Answer ONLY from the CONTEXT in the user message — you cannot verify anything against "
    "a live filesystem and must not say you will."
)

#: ``--tools ""`` disables ALL built-in tools, ``--strict-mcp-config`` loads no MCP
#: servers, and ``--append-system-prompt`` overrides the agent identity (see above) —
#: together the brain runs as a pure LLM that ANSWERS, not the Claude Code agent. Used by
#: one-shot, streaming, AND sica_autonomy's brain path (one source of truth).
#:
#: ``--setting-sources project`` is a LATENCY fix, not a capability cut: the CLI otherwise
#: loads the user's 18 plugins + 65 skills + global CLAUDE.md + pyright-LSP + auto-memory
#: on EVERY spawn — pure cold-start tax the pure-reasoning brain never uses. Measured: it
#: ~halves time-to-first-token (chat 6.5s→2.9s, voice 3.2s→1.7s). It loads ONLY project
#: ``.claude`` (the daemon's cwd is ProjectUtah, which has none → loads nothing) and never
#: the ``user`` source, so it can't pick up ~/.claude. The model is UNCHANGED —
#: ``claude-opus-4-8[1m]`` with or without it (verified); do NOT add ``--model`` (the
#: ``opus`` alias drops the [1m] 1M-context variant). Brain is not reduced; only its
#: startup is.
BRAIN_NO_AGENT: tuple[str, ...] = (
    "--append-system-prompt", BRAIN_SYSTEM_PROMPT, "--tools", "", "--strict-mcp-config",
    "--setting-sources", "project",
)

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

#: Milliseconds one Postgres STATEMENT may run on the ad-hoc lanes (exports, deck
#: reads, migration rollback). A connect timeout alone leaves the query unbounded —
#: a lock on leads/memory hangs the caller forever. Generous (5 min, matching the
#: psql export lane) because these are analytic reads, never the daemon hot path;
#: the shared daemon pool is deliberately NOT given a global statement timeout.
DB_STATEMENT_TIMEOUT_MS: int = int(os.environ.get("UTAH_DB_STATEMENT_TIMEOUT_MS", "300000"))

#: Connection-pool sizing for the long-lived daemon stores (memory, failures). The
#: store was a single connection + RLock — every in-daemon memory op serialized, negating
#: the worker pool's parallelism and the very reason Postgres was chosen (B6). A bounded
#: pool lets concurrent recalls/writes actually run concurrently. Kept small (Postgres
#: default max_connections is 100, and the cron processes use their own connections).
DB_POOL_MAX: int = int(os.environ.get("UTAH_DB_POOL_MAX", "8"))
#: Seconds to wait for a free pooled connection before degrading to MemoryUnavailable.
DB_POOL_TIMEOUT: float = float(os.environ.get("UTAH_DB_POOL_TIMEOUT", "10"))

#: Cron load governor (B2). The in-daemon governor only sheds the daemon's OWN in-flight
#: work; the dozen+ launchd crons bypassed it and could co-spike load (the AceOS killer).
#: A HEAVY cron (a scraper / self-coder) defers its run when 1-min load/core exceeds this,
#: and serializes behind a cross-process file lock so two heavy crons never run at once.
#: Light senders (outreach/marketer/brief) are exempt — a revenue send must never be
#: skipped for moderate load. 2.5 matches the local-tier defer threshold.
CRON_MAX_LOAD_PER_CORE: float = float(os.environ.get("UTAH_CRON_MAX_LOAD", "2.5"))

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
#: Context-window size for local models. Ollama otherwise defaults to the model's MAX
#: (131072 for llama3.2/deepseek), allocating a giant KV cache — llama3.2:3b ballooned
#: to ~17GB resident on an unused 128K window. Ace caps input at BRAIN_CONTEXT_MAX_CHARS
#: (~2K tokens) + a <=1024-token answer, so 8K is ample and the model loads at a few GB.
LOCAL_NUM_CTX: int = int(os.environ.get("UTAH_LOCAL_NUM_CTX", "8192"))

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

# --- voice (wake "ace" -> Whisper STT -> brain -> Piper TTS) -----------------
#: STT engine: "whisper" (MLX Whisper small.en — DEFAULT) or "moonshine".
#: WHY whisper is default: Moonshine base mis-transcribed REAL mic speech into
#: garbage live ("What's going on?" -> "Blun."), and a garbage transcript then
#: tripped the slow learn-on-miss web loop (~58s) — voice felt broken. MLX
#: Whisper small.en reads the SAME clip correctly AND faster (124ms vs 470ms
#: warm). Moonshine stays as the portable ONNX fallback when MLX is unavailable.
STT_ENGINE: str = os.environ.get("UTAH_STT_ENGINE", "whisper")
#: Moonshine ONNX model (used when STT_ENGINE=moonshine or as the MLX fallback).
STT_MODEL: str = os.environ.get("UTAH_STT", "moonshine/base")
#: MLX Whisper model — the DEFAULT STT (accurate + fast on Apple Silicon). base.en
#: was too weak on real mic audio (gave "on"); small.en transcribes it correctly.
WHISPER_MODEL: str = os.environ.get("UTAH_WHISPER", "mlx-community/whisper-small.en-mlx")
#: Reject a Whisper segment whose no_speech_prob exceeds this — kills the classic
#: silence-hallucination ("Thank you." / "Thanks for watching") on a near-silent or
#: fragment clip (measured no_speech 0.50) while keeping real speech (0.14). A
#: rejected clip yields "" = no command, not a phantom voice turn.
STT_MAX_NO_SPEECH: float = float(os.environ.get("UTAH_STT_MAX_NO_SPEECH", "0.4"))
#: Hard wall on a single transcribe in the STT worker. MLX Whisper (Metal) can
#: DEADLOCK on the GPU under contention; isolated in a worker subprocess, a hang is
#: recovered by KILLING the worker after this budget — the mic loop gets "" and keeps
#: listening (never deaf), and the next call respawns a fresh worker. Healthy <1s.
STT_HANG_TIMEOUT_S: float = float(os.environ.get("UTAH_STT_HANG_TIMEOUT", "8.0"))
#: STT respawn circuit-breaker (load-storm fix 2026-06-10). After a worker hang, skip
#: re-spawning for this cooldown (a fresh MLX/Metal compile under load just deadlocks
#: again and each respawn spawns a Metal compiler — the amplifier). Multiplied when the
#: host is overloaded (load/core over the threshold).
STT_RESPAWN_COOLDOWN_S: float = float(os.environ.get("UTAH_STT_RESPAWN_COOLDOWN", "20.0"))
STT_RESPAWN_OVERLOAD_MULT: float = float(os.environ.get("UTAH_STT_RESPAWN_OVERLOAD_MULT", "6.0"))
STT_RESPAWN_MAX_LOAD: float = float(os.environ.get("UTAH_STT_RESPAWN_MAX_LOAD", "2.5"))
#: Seconds to wait for the STT worker to load its model on boot (MLX compiles on the
#: first transcribe). An overrun means a wedged worker → respawn.
STT_WORKER_BOOT_S: float = float(os.environ.get("UTAH_STT_WORKER_BOOT", "30.0"))
#: whisper.cpp server STT — the post-MLX core (2026-06-10: MLX/Metal-Python deadlocks
#: under load were the entire recurring voice-failure class). When the binary + a ggml
#: model exist, the default engine is the whisper.cpp server: C++ Metal, model loaded
#: once, one local HTTP call per utterance with a hard timeout.
WHISPERCPP_BIN: str = os.environ.get("UTAH_WHISPERCPP_BIN", "/opt/homebrew/bin/whisper-server")
WHISPERCPP_MODEL: str = os.path.expanduser(os.environ.get(
    "UTAH_WHISPERCPP_MODEL", "~/.utah/models/whisper/ggml-small.en-q5_1.bin"))
WHISPERCPP_PORT: int = int(os.environ.get("UTAH_WHISPERCPP_PORT", "8090"))
#: B15: a DEAF-but-alive voice loop (mic device delivering pure zeros while the process
#: runs) is restarted by the supervisor after the mic has been silent (zeros) this long —
#: reopening the audio stream recovers a wedged CoreAudio handle. Longer than the 30s
#: mic_silent ALERT so Michael is paged first, then it self-heals if still deaf.
VOICE_DEAF_RESTART_S: float = float(os.environ.get("UTAH_VOICE_DEAF_RESTART", "75.0"))
#: Piper TTS voice model (the .json config sits next to it).
PIPER_MODEL: str = os.environ.get(
    "UTAH_PIPER", os.path.expanduser("~/.utah/models/piper/en_GB-cori-high.onnx")
)
#: openWakeWord ONNX for audio-level "hey ace" arming (Stage A — never opens a turn alone).
#: v3 is the trained keyword model; falls back to legacy ~/.ace path when missing.
WAKE_MODEL: str = os.environ.get(
    "UTAH_WAKE_MODEL",
    os.path.expanduser("~/.utah/models/wake/hey_ace_v3.onnx"),
)
#: Confidence to arm command capture. ONNX arms only — STT/text still resolves the command.
#: WAKE CONFIDENCE BAND (2026-06-10: a single 0.82 threshold was simultaneously too HIGH
#: — soft real "Ace" missed — and too LOW — TV/room speech at 0.83 armed, then any
#: transcript became a command, so Ace answered the television). Two thresholds fix both:
#:   • ARM floor (0.68): low enough that a quiet "Ace" arms capture (fewer MISSES).
#:   • TRUST (0.90): only AT/above this does an audio wake accept a transcript that has no
#:     literal "ace" token as a command. In the 0.68–0.90 band, Stage B requires a real
#:     standalone "ace"/"utah" in the transcript — so room speech without it is dropped
#:     (fewer FALSE FIRES). Paired with the loud-audio storm guard in loop.py.
WAKE_THRESHOLD: float = float(os.environ.get("UTAH_WAKE_THRESHOLD", "0.68"))
WAKE_TRUST_THRESHOLD: float = float(os.environ.get("UTAH_WAKE_TRUST_THRESHOLD", "0.90"))
#: Seconds after an audio wake hit to capture the command utterance (STT runs once).
WAKE_ARM_S: float = float(os.environ.get("UTAH_WAKE_ARM_S", "8.0"))
#: Force-capture window after openWakeWord fires (bypasses Silero on noisy mics).
VOICE_FORCE_CAPTURE: bool = os.environ.get("UTAH_VOICE_FORCE_CAPTURE", "1") == "1"
VOICE_FORCE_CAPTURE_S: float = float(os.environ.get("UTAH_VOICE_FORCE_CAPTURE_S", "5.5"))
VOICE_FORCE_CAPTURE_DELAY: float = float(os.environ.get("UTAH_VOICE_FORCE_CAPTURE_DELAY", "0.5"))
#: Skip armed segments whose RMS is below this (silence after false wake).
VOICE_SILENCE_RMS: float = float(os.environ.get("UTAH_VOICE_SILENCE_RMS", "0.008"))
#: VAD trailing-silence frames before STT (32 ms/frame). Lower = faster end-of-utterance.
VAD_OFFSET: int = int(os.environ.get("UTAH_VAD_OFFSET", "12"))          # was 20 (~640 ms)
#: End-of-*command* silence after an audio wake. Was 6 (~192 ms) — shorter than the
#: natural pause between "ace" and the command, so the armed segment ended on that gap
#: and captured only the 0.2 s wake tail (command='' → no answer). The post-wake gap is
#: now bridged by VAD_ARM_GRACE; this only ends the turn after the command is spoken.
VAD_OFFSET_ARMED: int = int(os.environ.get("UTAH_VAD_OFFSET_ARMED", "20"))  # ~640 ms end-of-command
#: After an audio wake, hold capture this many frames (32 ms/frame) waiting for the
#: command's speech to begin before treating it as a bare "ace". Bridges the pause
#: between the wake word and the command so "ace … what's the weather" is captured whole.
VAD_ARM_GRACE: int = int(os.environ.get("UTAH_VAD_ARM_GRACE", "63"))    # ~2.0 s

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
    {"user", "turn", "fact", "consolidation", "sensor", "core", "knowledge", "code"}
)

#: Authoritative sources a later write must NEVER supersede — curated ground truth
#: (the identity creed and the reference library). A distinct fact about an evolving
#: attribute still supersedes another fact (the Newman path); it just can't collapse
#: these. Without this, loading the 13 "Think and Grow Rich" principles collapsed to 3.
NEVER_SUPERSEDE_SOURCES: frozenset[str] = frozenset({"core", "knowledge", "code"})

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
#: a guaranteed retrieval lane and a small ranking prior. ``code`` was REMOVED from this
#: shared lane (its ~1300 rows crowded the ~150 core/knowledge out of the 5 slots) and
#: given its OWN lane below.
CURATED_SOURCES: frozenset[str] = frozenset({"core", "knowledge"})

#: Top-N nearest curated rows are ALWAYS merged into the candidate pool (as their own
#: RRF lane), so they reach the reranker even when the general pool is swamped by the
#: fact pile — the diagnosed root cause (a relevant Law never even reached rerank).
CURATED_LANE_K: int = 5

#: Indexed source code (``source='code'``, ~1300 chunks) gets its OWN guaranteed lane so a
#: code/self question surfaces the relevant FUNCTION even when chatty 'turn' rows fill the
#: general pool — the diagnosed miss where the brain refused to name its own functions
#: because their chunks never reached the reranker. Separate from the curated lane so code's
#: volume never crowds core/knowledge (and vice-versa).
CODE_SOURCES: tuple[str, ...] = ("code",)
CODE_LANE_K: int = 6

#: Additive ranking prior per source — a source-authority prior on the rerank score.
#: Bounded by ENTITY_BOOST scale (≤0.5) so a STRONG match in ANY source still wins
#: outright; it only tips the LOW-confidence regime (vague query, nothing scores well)
#: toward curated wisdom over low-value migrated facts. It shifts rerank ORDER only,
#: never a hit's ``sim``, so the no-fabrication answer gate (which reads sim) is
#: unaffected. NOTE: the reranker emits logits whose relevant-vs-irrelevant spread is
#: only ~1–2; the prior was 1.5 (same scale), which could lift an *irrelevant* curated
#: row over a *relevant* fact. Kept ≤ENTITY_BOOST so it can only break genuine ties.
SOURCE_BOOST: dict[str, float] = {"core": 0.5, "knowledge": 0.3, "code": 0.2}

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

# --- phone push (Pushover) + alert taxonomy ------------------------------------
#: Master switch for phone push. Off => the transport gates to a no-op (honest,
#: never fakes). Creds themselves live OUTSIDE the repo at
#: ``~/.utah/secrets/pushover.json`` (Michael's input) — absent creds also gate.
PUSHOVER_ENABLED: bool = os.environ.get("UTAH_PUSHOVER", "1") != "0"

#: Default recipient: ``user`` (Michael's personal devices) or ``group`` (delivery
#: group → all member phones). The per-call ``target`` and the creds' ``default_target``
#: both override this.
PUSHOVER_DEFAULT_TARGET: str = os.environ.get("UTAH_PUSHOVER_TARGET", "user")

#: Tailnet URL of the Utah deck — the tap-through target carried by brief pushes.
#: Served by ``tailscale serve`` (com.utah.tailserve): tailnet :8765 → Utah :8766.
DECK_TAILNET_URL: str = os.environ.get(
    "UTAH_DECK_URL", "http://michaels-macbook-pro.tailb44439.ts.net:8765/")

#: Which alert streams may page the phone (Michael's choice: all four).
ALERT_STREAMS_ENABLED: frozenset[str] = frozenset(
    {"critical", "trade", "brief", "leads_probate"})

#: Pushover priority per stream. 2=emergency (retry+ack, bypass quiet hours),
#: 1=high (bypass quiet hours), 0=normal (respects quiet hours), -1=low (silent).
ALERT_PRIORITY: dict[str, int] = {
    "critical": 2,
    "trade": 1,
    "brief": 0,
    "leads_probate": -1,
}

#: Failure ``kind`` values that page the phone as CRITICAL. Curated on purpose: the
#: ``dependency_unavailable`` Postgres-restart storm and ``browser/render_failed`` /
#: ``brain/unavailable`` noise are EXCLUDED — only genuine breakage pages.
CRITICAL_FAILURE_KINDS: frozenset[str] = frozenset(
    {"daemon_unreachable", "load_critical", "postgres_down", "process_died",
     "engine_death", "critical", "supervisor_down"})  # supervisor_down pages on first sight (B1′)

#: Quiet hours (local clock). Streams without bypass are suppressed in this window.
QUIET_HOURS_START: str = os.environ.get("UTAH_QUIET_START", "22:30")
QUIET_HOURS_END: str = os.environ.get("UTAH_QUIET_END", "06:30")

#: Per-key dedup window (seconds): storm suppression for repeated identical alerts.
ALERT_DEDUP_SECONDS: int = int(os.environ.get("UTAH_ALERT_DEDUP", "1800"))

#: Trade-stream page controls (2026-06-10 "hundreds of trade notifications"): a fire's
#: engine+symbol+direction key re-pages at most every TRADE_ALERT_DEDUP_SECONDS, and the
#: phone gets at most TRADE_ALERTS_PER_HOUR trade pages per rolling hour, period — capped
#: fires still land in the ledger and on the deck's Engine Lab.
TRADE_ALERT_DEDUP_SECONDS: int = int(os.environ.get("UTAH_TRADE_DEDUP", "7200"))
TRADE_ALERTS_PER_HOUR: int = int(os.environ.get("UTAH_TRADE_ALERTS_PER_HOUR", "6"))

#: SITE-12 — subscriber delivery of engine fires (utah/product/signals.py), the
#: Signals product's deliverable. Default ON: with an empty signals_subscribers table
#: the lane is an honest no-op (deliver_fire gates with "no active subscribers"), so
#: this flag exists only as a hard emergency OFF without touching the table.
SIGNALS_DELIVERY: bool = os.environ.get("UTAH_SIGNALS_DELIVERY", "1") not in (
    "0", "", "false", "no")

#: Emergency (priority 2) re-alert cadence / give-up window, in seconds.
PUSHOVER_EMERGENCY_RETRY: int = 60
PUSHOVER_EMERGENCY_EXPIRE: int = 3600

# --- Discord (community server mirrored on the deck) --------------------------
#: Public invite link surfaced on the deck (the "JOIN DISCORD" button) and by the
#: ``/discord`` redirect. Prefer setting ``invite_url`` in ~/.utah/secrets/discord.json
#: (the integration reads it there); this env override wins when set.
DISCORD_INVITE_URL: str = os.environ.get("UTAH_DISCORD_INVITE", "")

#: Which spine domains feed their Discord channel via webhook (channel name -> the
#: feed that posts to it). The producer looks up the webhook URL in
#: ~/.utah/secrets/discord_webhooks.json (written by ``discord.save_webhooks``).
DISCORD_FEED_CHANNELS: dict[str, str] = {
    "📈leads": "leads", "⚖️probate": "probate", "📨outreach": "outreach",
    "🔥fires": "fires", "🛡️audit-ledger": "audit", "✅merges": "selfcode",
    "📣announcements": "announce", "🚨alerts": "critical",
}

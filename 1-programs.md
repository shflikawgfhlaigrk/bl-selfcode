# 1 — PROGRAMS audit

> The software stack Utah runs on (languages, libs, models, binaries, services).
> Audited from the dirt (192,257 files, 136 venv pkgs, 68 GB models, Homebrew,
> Python 3.14) + web SOTA. Utah is **personal**; Sovereign (sold) inherits the
> commercial-license constraints flagged below.

## 1. What we had
- **Languages/runtime:** Python **3.14** (live venv), Swift (HQ), vanilla JS
  (dashboard), Node 25.9 (Playwright/MCP tooling), C/C++ (whisper.cpp, MLX-Metal).
  uv, no lockfile. No Rust/Go.
- **Data/vector:** one SQLite `ace.db` (191 MB) doing everything; FTS5;
  **sqlite-vec** (ANN); **LanceDB** (197 MB embed cache); DuckDB present, idle.
- **LLM:** MLX/mlx-lm (L0–L2) + Qwen 1.5B/14B/32B(×2) + Llama-3.1-8B; Ollama
  fallback; Anthropic **API lane**; Claude CLI (L3). No 70B on disk.
- **Voice:** openWakeWord, whisper.cpp (`ggml-base.en`), Silero/webrtc VAD,
  **smart-turn-v3.2**, Piper TTS. Kokoro & XTTS erased.
- **Web/serialize/sched:** FastAPI/Starlette, asyncio, websockets, JSON, APScheduler;
  WIN data plane coded for **cbor2** (not installed → inert); flatbuffers present.
- **MCP/integrations:** capstone gateway on the **official `mcp` SDK** (not FastMCP);
  gbrain (bun/TS, crash-looping); Gmail/OAuth, Pushover, Tailscale, Playwright;
  Resend/Stripe/Tradovate stubs.
- **Media:** Veo/Gemini (dead, 429); local ffmpeg+PIL reel renderer.
- **Frontend:** SwiftUI HQ (9,733 LOC) **and** vanilla-JS dashboard (5,563 LOC).

## 2. Why we did it
Local-first on Apple Silicon → MLX + on-device voice. SQLite because it's
embedded/zero-ops and "good enough" to start. One ace.db to move fast. Veo for
"can we generate video at all." Two frontends because native HQ + a web view both
seemed wanted. The API lane for a quality fallback. All defensible *starting*
choices for a single fast-moving builder.

## 3. What we didn't think about
- **sqlite-vec fails to load ~100%** here → leaked ~1,007 fds → **Errno-24 crash
  loop**; and it's brute-force (no HNSW).
- **One SQLite hammered by ~58 writers** → `database is locked`; **LanceDB** = 197 MB
  of dual-write for zero benefit at ~10k rows.
- **MLX unbounded prompt cache → 161 GB OOM**; 32B times out / 70B thrashes.
- **API lane = silent per-token spend** → "credit balance too low."
- **Kokoro/XTTS broke on py3.14** (erased); **Piper API change** zeroed brief audio.
- **Veo** is paid + its fallback emitted **fake URLs that passed the quality gate**.
- **cbor2 never installed** → the "fast" WIN plane never ran.
- **Two frontends** = double maintenance + codegen coupling; **gbrain** = a 2nd brain.
- **No lockfile** → version drift.

## 4. What we're gonna change
- **Python 3.13/3.14 + committed `uv.lock`.** Drop the API lane entirely (CLI only).
- **Storage → Postgres + pgvector primary** (concurrent OLTP at volume); **DuckDB**
  OLAP; **drop sqlite-vec + LanceDB**. (pgvector's HNSW absorbs usearch/hnswlib.)
- **LLM:** keep MLX (2 GiB Metal cap) + a warm 14B; **add Apple Foundation Models**
  (free on-device ~3B for classify/extract/intent); drop 32B/70B live tiers + the
  duplicate 32B (−17 GB); Claude = the agentic-harness brain.
- **Voice:** **Apple SpeechAnalyzer** STT (native, ~55% faster than Whisper) /
  Moonshine for streaming; Piper realtime + **F5-TTS/StyleTTS2** natural (F5 fine
  for personal Utah).
- **WIN binary plane on flatbuffers** (zero-copy, fail-loud), not cbor2.
- **Media:** kill Veo; ffmpeg floor + **local LTX-2 MLX** for real generation.
- **One web frontend** (Svelte/React + uPlot); kill SwiftUI HQ. **One memory
  service** (kill gbrain). Wire **Resend + sending domain**.

## 5. How it helps
- **~36 GB reclaimed** (−34 GB models, −0.8 GB deps, −295 MB Lance); **#1 crash
  cause removed** (sqlite-vec fd leak); OOM lineage gone.
- **STT ~3–6× faster** (Whisper 2–4 s → ~0.3–1 s) → sub-8 s voice.
- **No surprise spend** (API lane deleted); **−9,733 Swift LOC + a language** gone.
- One model across IPC/WIN/Postgres; a real (not inert) binary data plane.

Sources: vectorlite/usearch https://github.com/unum-cloud/usearch · MLX vs llama.cpp/Ollama/vLLM https://contracollective.com/blog/llama-cpp-vs-mlx-ollama-vllm-apple-silicon-2026 · Moonshine vs Whisper https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026 · TTS https://www.promptquorum.com/power-local-llm/local-tts-voice-cloning-piper-coqui-xtts · LTX-2 MLX https://github.com/dgrauet/ltx-2-mlx · Apple SpeechAnalyzer https://www.macrumors.com/2025/06/18/apple-transcription-api-faster-than-whisper/ · Apple Foundation Models https://developer.apple.com/documentation/FoundationModels

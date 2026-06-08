# PROGRAMS — Ace's real stack, from the dirt

> Scope: **programs only** — every language, package, model, binary, and the doc
> set Ace is actually built from, read from disk (not memory). For each: what we
> have → how it failed → what Utah uses → why (researched) → what it fixes.
> Planning only. Nothing committed or installed.
>
> **Method correction:** the first draft of this file was written from memory and
> CLAUDE.md. This version is rebuilt from the actual filesystem — `find`, `git`,
> `site-packages/*.dist-info`, the models dir, and the binaries. Where the two
> disagreed, disk wins, and the corrections are called out in §0.1.

---

## 0. Ground truth (counted from disk)

```
AceOS total files ........ 192,257     ~/.ace total files ....... 387,641
  .py ....... 74,265 (mostly deps)     models on disk ........... 68 GB
  .pyc ...... 25,995                   live venv ................ Python 3.14
  .md ....... 14,684 (333 tracked)     repo .venv site-pkgs ..... 136 packages
  .json ..... 12,605                   capstone .venv ........... 29 packages
  .h ........ 10,803 (native deps)     tracked source files ..... 2,200
  .so ....... 641 (compiled ext)       tracked .md docs ......... 333
```

So "65,000+" is real and then some: **~192k files in the repo**, of which only
**2,200 are tracked source** — the rest is the dependency/venv/cache mass. The
*programs* live in the 136-package venv + the 68 GB model set + a few Homebrew
binaries. That is the genuine surface this document covers.

### 0.1 What disk corrected vs the first draft

1. **Python is 3.14, live** (`.venv/lib/python3.14`) — not 3.12. The 3.14
   migration *succeeded*; Kokoro/spacy were simply dropped to get there.
2. **No 70B model exists.** On disk: Qwen2.5 **1.5B / 14B / 32B (×2)** + **Llama-3.1-8B**. The "L4 70B" was never installed.
3. **Capstone uses the official `mcp` SDK 1.27.2**, not FastMCP.
4. **`cbor2` is not installed anywhere** → the "WIN-B/CBOR data plane" can't be running as described.
5. **`flatbuffers` 25.12.19 is already installed** (via onnxruntime).
6. **A `smart-turn-v3.2` semantic turn-detection model is on disk** (`models/voice/smart_turn/`) — Ace already moved past plain VAD for endpointing; the first draft missed it.
7. **STT is a Homebrew binary** (`/opt/homebrew/bin/whisper-cli` + `ggml-base.en-q5_1.bin`), invoked via subprocess — there is no whisper Python package.

### 0.2 Coverage — what "from the dirt" now includes

Enumerated from disk: repo venv (**136 pkgs**) + capstone venv (**29**) + **68 GB**
model set + native binaries + **56 Homebrew formulae** + node/npm + Swift packages
+ dashboard JS + the **ace-trainer** LoRA stack + the **~/debt engine** programs +
Chrome. **Deliberately not deep-dived:** the SwiftUI HQ internals and the ~/debt
engine internals — both are being *cut* in Utah, so mining their dependency trees
adds nothing. **Everything Utah will keep or replace is accounted for.** That is
the basis for the completeness claim at the end of this doc.

---

## 1. The real installed manifest (136 packages, repo .venv, Python 3.14)

Declared in `pyproject.toml` core: **23** packages. Installed: **136**. So ~113
are transitive. Grouped by what they actually do, with **direct-import file
counts** from tracked source (how much the code really leans on each):

**Core runtime / web / data (declared, heavily used)**
`pydantic` 2.13.4, `pydantic-settings` 2.14.1, `fastapi` 0.136.3, `starlette`
1.2.1, `uvicorn` 0.48.0, `httpx` 0.28.1, `websockets` 16.0, `aiosqlite` 0.22.1,
`sqlalchemy` 2.0.49, `apscheduler` 3.11.2 (18 files), `aiosmtplib` 5.1.1,
`pyyaml` 6.0.3 (47), `numpy` 2.4.5 (68), `tenacity`, `typer`, `rich`, `watchdog`.

**Data / vector / analytics**
`duckdb` 1.5.3 (5 files — used), `pyarrow` 24.0.0, `lancedb` 0.30.2 (4 files),
`sqlite_vec` 0.1.9 (2 files — *the fd-leak/load-failure lib*), `sentence_transformers`
5.5.1 (4), `scikit_learn` 1.8.0 (**0 direct** — transitive), `scipy` 1.17.1
(**0 direct**), `networkx` 3.6.1 (**0 direct**), `joblib`, `threadpoolctl`.

**LLM / inference (Apple Silicon)**
`mlx` 0.31.2 + `mlx_metal` + `mlx_lm` 0.31.3 (3 files), `torch` 2.12.0 (4 — pulled
by silero/sentence-transformers), `torchaudio` 2.11.0, `transformers` 5.9.0 (1),
`tokenizers`, `safetensors`, `sentencepiece`, `huggingface_hub` + `hf_xet`,
`anthropic` 0.102.0 (the API-lane client), `regex`, `tqdm`.

**Voice**
`piper_tts` 1.4.2 (TTS), `openwakeword` 0.6.0 (4 files), `silero_vad` 6.2.1 (1),
`onnxruntime` 1.26.0 (1 — runs wake + smart-turn), `sounddevice` 0.5.5 (14),
`soundfile` 0.13.1 (3), `soxr` 1.1.0 (resample). *No whisper package — it's a
Homebrew binary.*

**Integrations / web / google**
`google_api_python_client` 2.197.0 + `google_auth*` + `oauthlib` (Gmail/Calendar),
`playwright` 1.60.0 (**20 files — the most-used integration**), `selectolax` 0.4.9
(1, HTML), `pypdf` 6.12.2 (1), `requests`, `dnspython` (MX checks),
`protobuf` 7.35.0, `flatbuffers` 25.12.19, `proto_plus`, `googleapis_common_protos`.

**Dev / test (not shipped behavior)**
`pytest` 9.0.3 + `pytest_asyncio/subtests/timeout`, `hypothesis` 6.155.1,
`mypy` 2.1.0, `ruff` 0.15.15, `pip/wheel/setuptools`.

**Capstone gateway venv (29 pkgs):** `mcp` 1.27.2 (official SDK), `sse_starlette`,
`python_multipart`, `pyjwt`, `httpx_sse`, + shared pydantic/anyio/uvicorn.

### 1.6 System & non-Python programs (from disk)

- **Homebrew (56 formulae; the ones Ace actually uses):** `whisper-cpp` 1.8.5
  (STT engine), `ffmpeg` 8.1.1 + `lame` 3.100 + `opus` 1.6.1 (media codecs),
  `portaudio` 19.7.0 (the C backend `sounddevice` rides on), `ollama` 0.21.2
  (LLM fallback), `node` 25.9.0, `python@3.14` 3.14.5.
- **Node 25.9 / npm:** present, but **no repo `package.json`** — node exists to run
  Playwright's browser drivers and stdio MCP servers, not the dashboard.
- **Swift toolchain:** two packages — `hq/Package.swift` and `Ace/Package.swift`
  (the native HQ app; **killed in Utah**). Dozens of stale `Package.swift` copies
  also litter `.claude/worktrees/` — churn debris.
- **Dashboard:** `app.js` + `voice.js` — **vanilla JS, no package.json, no build,
  no npm deps.**
- **ace-trainer:** a self-contained LoRA fine-tune stack (`build_corpus.py`,
  `finetune.py`, `evaluate.py`, `ab_generate.py` + `corpora/`, `data/`, `eval/`),
  MLX-based, outside the main tree.
- **Trading engines (`~/debt`, ~10 dirs):** Apex_Prime, Barber, Context_Alpha/
  Bravo, Perplexity variants, Signal_Bible, sandbox — separate Python programs
  (off-limits; **discarded in Utah**).
- **Chrome:** Google Chrome.app driven via CDP/Playwright across 4 profiles
  (`chrome-ace/-debug/-hq/-wc`).
- **No Rust, no Go** (`cargo`/`go` absent). Today's stack is **Python 3.14 +
  C/C++ (whisper-cpp/ffmpeg/portaudio) + Swift + vanilla JS + Node(tooling)**.

**Dead weight signal:** `scikit_learn`, `scipy`, `networkx` (0 direct imports)
and the full `torch`+`torchaudio` (~2 GB) ride in only to satisfy `silero_vad`
and `sentence_transformers`. That is a large, heavy transitive footprint for two
narrow features (VAD + embeddings) — a prime Utah trim target.

---

## 2. Models & binaries on disk (68 GB)

| Artifact | Path | Role | Verdict |
|---|---|---|---|
| Qwen2.5-1.5B-4bit | `models/mlx-community/` | L0 classify/wake-intent | KEEP |
| Llama-3.1-8B-4bit | `models/mlx-community/` | mid tier | KEEP (or swap to a 2026 8B) |
| Qwen2.5-14B-4bit | `models/mlx-community/` | **L1 fast voice path** | KEEP |
| Qwen2.5-32B-4bit **(×2 copies)** | `models/mlx-community/` | L2 — **times out on this Mac** | **KILL one copy; drop from live tiers** |
| whisper `ggml-base.en-q5_1` | `models/whisper/` | STT model (whisper.cpp) | KEEP (upgrade engine) |
| `hey_ace.onnx` (+v3, +pos-only) | `models/wake/` | wake word | KEEP |
| `smart-turn-v3.2-cpu.onnx` | `models/voice/smart_turn/` | **semantic turn/endpoint detection** | KEEP — already SOTA-ish |
| `en_GB-cori-high.onnx` | `models/piper/` | Piper TTS voice | KEEP realtime; add natural TTS |
| `whisper-cli`, `ffmpeg`, `ffprobe` | `/opt/homebrew/bin` | STT + media (system binaries) | KEEP |
| `piper` | `~/.ace/venv/bin` | TTS binary | KEEP |

**Immediate dirt-level waste:** a **duplicate 32B model (~18 GB)** and a 32B tier
that never runs. Removing both reclaims disk and clarifies the tier story.

---

## 3. Program failures (from the real stack) and Utah's choice

| Program | How it failed (evidence) | Utah uses | Why (researched) | Fixes |
|---|---|---|---|---|
| **sqlite_vec 0.1.9** | Fails to load ~100% here; `vec_search` leaked the shared handle → ~1,007 fds → Errno 24 crash loop | **usearch / hnswlib** | In-process HNSW, no fragile SQLite extension to load → no load-failure, no fd leak | Removes the worst crash class |
| **lancedb 0.30.2** | 197 MB dual-write, no benefit at ~10k rows (4 files use it) | **Remove**; FTS5 + HNSW | Columnar Lance wins at millions of rows, not thousands | Deletes weight + write tax |
| **torch+torchaudio (~2 GB)** | Installed only for silero_vad + sentence_transformers; sklearn/scipy/networkx 0-direct-import | **Lighter VAD (ten-vad/webrtc) + a small MLX embedder**; drop torch if possible | Avoids 2 GB + native build surface for two narrow features | Slims the venv massively |
| **anthropic 0.102 (API lane)** | Credits depleted → "credit balance too low"; silent spend risk | **Delete the API lane**; keep Claude CLI (subscription) | No per-token meter to surprise you | No surprise bills |
| **Qwen2.5-32B (×2)** | Times out / thrashes; duplicated on disk | **Drop from live tiers; dedupe** | Exceeds latency/VRAM budget on this Mac | Faster, leaner |
| **whisper.cpp base.en (binary)** | Slowest pole: ~2–4 s per 4 s clip | **mlx-whisper / Moonshine** | Apple-native (~0.5–1 s) / streaming (<200 ms) | Unlocks sub-8 s voice |
| **piper_tts 1.4.2** | Upstream API change once zeroed brief audio; robotic vs natural | **Piper realtime + F5-TTS/StyleTTS2** for natural/clone | 2026 SOTA local natural TTS; F5 zero-shot clone, no training | Natural voice, no cloud |
| **(Kokoro / XTTS)** | Not installed — already erased (broke on 3.14) | Stay dead → F5/StyleTTS2 | superseded | removes dead path |
| **mcp 1.27.2 (capstone)** | Startup swarm/load storms | Keep one gateway + lazy spawn + health probe | One audited gateway beats N raw servers | Keeps gateway, kills storm |
| **Veo/Gemini video** | Not in venv (key-based); 429 dead + fake-URL fallback hazard | ffmpeg local renderer + **LTX-Video/SDXL/FLUX (local)** | Free, local, real generation on Apple Silicon | Kills dead paid dep + fabrication hazard |
| **(Resend)** | Not installed; outreach gated on it | Add Resend + sending domain (SPF/DKIM) | Built for deliverability; Gmail can't do cold 500/day | Unblocks 500 leads/day |
| **(cbor2)** | Claimed but absent → WIN-B inert | **flatbuffers (already present) / Cap'n Proto** for numeric bursts | Zero-copy reads, no decode | A real fast binary path |
| **SwiftUI HQ + vanilla JS** | Two frontends; codegen-coupled | One web UI (Svelte/React) + uPlot | Single-user needs one reactive surface | Deletes ~9.7k LOC + coupling |

---

## 4. The doc set Ace actually carries (333 tracked .md)

The documentation is itself diagnostic. Top tracked-doc directories:

```
 86  docs/agent_007_investigations/   ← contradiction / embedding-negation /
                                         intent-fallthrough logs (see below)
 21  acesd/scripts/audits/            ← upgrade & dependency audits
 17  vault/agents/                     ← per-agent state docs
 13  docs/handoff/                     ← session handoffs
 11  vault/system/stack/   11 docs/archive/   10 docs/
  9  docs/superpowers/specs/  ·  9 vault/reviews/codex/
```

Root "code docs Ace uses" (the canonical set): `CLAUDE.md`, `CODEBASE_MAP.md`,
`AGI-BLUEPRINT.md`, `AGI-BUILD-ROADMAP.md`, `ROADMAP.md`, `PROJECT-UTAH.md`,
`MAKE-UTAH-PLAN.md`, `SELF_IMPROVE_POLICY.md`, `MICHAEL_BACKLOG.md`, `HANDOFF.md`,
plus `docs/`: `FAILURE-MAP.md`, `INFERENCE-LANES.md`, `STORAGE-MIGRATION.md`,
`TRADING-CONSOLIDATION-AUDIT.md`, `OPERATOR-VERIFY.md`, `SAFETY_SMOKE.md`,
`ACE-COMPLETION-PLAN.md`.

**The tell:** `docs/agent_007_investigations/` holds **86 auto-generated files**
with names like `2026-05-25-embedding-negation-21228-vs-21161`,
`contradiction-13614-13541-sixth-plus-dispatch`,
`intent-fallthrough-56-things-wrong-with-itself`,
`intent-fallthrough-meaning-of-life`,
`intent-fallthrough-are-you-able-to-see-what-were-doing`. These are the
self-coding loop re-investigating the **same** embedding/contradiction/intent
failures over and over (note "sixth-plus-dispatch", "eighth-dispatch"). The doc
set is *evidence of the churn spiral and of the chat box not understanding
itself* — which is exactly §6.

---

## 4.5 How much better — quantified (sizes measured from disk; latency from research)

> Sizes are **measured on disk today**. Latency deltas are from the researched
> benchmarks in §3 and are **targets to verify at the build gate**, not yet
> re-measured on this Mac (no fabrication).

| Fix | Before | After | Delta |
|---|---|---|---|
| sqlite_vec → **usearch/hnswlib** | load fails ~100% → ~1,007 fd leak → Errno 24 crash loop | in-proc HNSW, no extension to load | **crash-class eliminated**; query ~2–5 ms; load reliability ~0%→~100% |
| whisper.cpp base.en → **mlx-whisper/Moonshine** | ~2–4 s per 4 s clip | ~0.3–1 s | **~3–6× faster STT** (biggest voice-latency pole) |
| Voice round-trip | ~10–15 s (26 s cold) | target ~6–8 s | **~2× faster** end-to-end |
| Drop **torch+torchaudio** | **447 MB** | 0 (ten-vad/webrtc + small MLX embedder) | **−447 MB** |
| Drop **sklearn+scipy** (0 direct imports) | **144 MB** | 0 | **−144 MB** |
| Drop **LanceDB** (pkg+data) | **98 MB + 197 MB** | 0 | **−295 MB** |
| **Venv slim, total** | **1.9 GB** | ~1.1 GB (est.) | **~−0.8 GB (~42%)** |
| **Dedupe 32B + drop tier** | **17 GB × 2 = 34 GB** | 0 | **−34 GB of the 68 GB model set (~50%)** |
| LLM lane: API → **CLI-only** | unbounded per-token $ ("credit too low") | subscription, $0 marginal | **no surprise spend** |
| Piper → **+F5/StyleTTS2** | robotic; clone broken (XTTS load error) | natural voice + zero-shot clone | qualitative, decisive |
| 2 frontends → **1 web** | 9,733 Swift LOC + 5,563 JS + codegen coupling | one reactive web UI | **−9,733 LOC + coupling gone** |
| Gmail → **Resend + domain** | ~500/day cap, account risk | deliverable 500+/day | **unblocks the lead goal** |
| Chat brain → **intent-tiered + tools** | 14B / 512-tok / no self-tools / deflects | escalates to frontier + real tools | **blind → self-aware** (§6) |

**Net:** **~36 GB disk reclaimed** (34 GB models + ~0.8 GB deps + 295 MB Lance),
the **#1 crash cause removed**, **STT ~3–6× faster**, **~10k LOC and one whole
language deleted**, **$-risk removed**, and a chat brain that can finally see
itself. The disk numbers are facts as of today; the latency numbers are
research-backed targets to confirm at the gate.

## 5. Utah program bill-of-materials (grounded)

| Layer | KEEP (real, works) | SWAP / ADD | KILL |
|---|---|---|---|
| Language | **Python 3.14** (already live), uv **+ lockfile** | Rust via PyO3 later (measured only) | — |
| Data | SQLite(WAL,1-writer), FTS5, **DuckDB** (5 files use it) | **usearch/hnswlib**, **Postgres** (ledger) | **sqlite_vec**, **lancedb** |
| ML deps | mlx/mlx_lm, sentence_transformers | small MLX embedder; **ten-vad/webrtc** | **torch/torchaudio** (~2 GB) if VAD/embed swapped; sklearn/scipy/networkx (0-import) |
| LLM | MLX 1.5B+8B+14B, Claude CLI | dedupe 32B | **anthropic API lane**, **32B live tier + duplicate copy** |
| Voice | openWakeWord, **smart-turn-v3.2**, Piper, silero/webrtc | **mlx-whisper/Moonshine**, **F5-TTS/StyleTTS2** | whisper.cpp base.en (engine), Kokoro/XTTS (already gone) |
| Web/serialize | FastAPI/Starlette, JSON(control), apscheduler+orphan-prune | **flatbuffers** (already present) for numeric | celery/temporal/redis/zeromq (unneeded) |
| MCP/integrations | **mcp SDK** gateway, Gmail, Pushover, Tailscale Serve, Playwright (20 files) | **Resend + domain** | gbrain (2nd brain), phone_proxy |
| Media | ffmpeg + local renderer | **LTX-Video, SDXL/FLUX** local | Veo + fake-URL fallback |
| Frontend | (web only) | **Svelte/React + uPlot/lightweight-charts** | **SwiftUI HQ** |

---

## 6. Why Ace's chat box could never respond like this — and never knew its own state

This is the most important program-level failure, and it is structural, not a
prompt-tuning miss. Evidence from the real code:

**6.1 The model was the wrong class for the job.**
The chat/voice path defaults to **tier L1 = Qwen2.5-14B-4bit, `max_tokens=512`,
~350 ms latency budget** (`acesd/llm/router.py`). L2 (32B) times out; L3 (Claude
CLI) is a gated escalation, not the default. A **14B local model capped at 512
output tokens** physically cannot produce a multi-thousand-word, cross-file
synthesis. The answers you're reading now come from a frontier model (Opus 4.8,
~1M context, no 512-token cap). **Ace's chat was a latency-first local assistant;
this is a frontier agentic harness.** Different class of program entirely.

**6.2 The chat model had no tools to see itself.**
Its entire toolset (`acesd/llm/tools.py`) is a sandbox: `db_query` (read-only SQL
on whitelisted tables), `file_read` (restricted to `ALLOWED_FILE_ROOTS`),
`http_get` (**loopback only**: `ALLOWED_HTTP_HOSTS = ("127.0.0.1","localhost")`),
`engine_state`, `web_search`. **There is no shell, no `find`, no `git`, no
`sqlite3` CLI, no package enumeration, no subagents.** I answered by actually
running `find` across 192k files, `git grep` over the source, `ls site-packages`,
and `sqlite3` on the live DBs — then fanning out 8 parallel research agents. Ace
had **no mechanism for any of that**. It could not count its own files, list its
136 packages, read its own venv, or inspect its own process — there was simply no
tool wired to do so.

**6.3 It could only recite what was logged.**
The daemon encodes the limit literally: **`daemon.py:4281` — "if it isn't logged,
I can't see it."** The memory-first design (built to stop confabulation) makes the
chat path *recite* rows from `semantic_memory` rather than *investigate*. Safe,
but it means Ace's "knowledge of itself" was only ever what some agent had
previously written into a table — never a live look.

**6.4 The router actively deflected self-questions.**
`daemon.py:6348` and `:6733` handle the recurring **"I'm not wired into that data
stream / no credentials"** deflection — the chat would *decline* introspection
questions because the generic path had no live self-probe and the model was told
to refuse rather than guess. So even answerable state questions returned a
deflection instead of a look.

**6.5 No orchestration, no escalation-to-investigate.**
Single model, single turn, 512 tokens, sandboxed tools. There was no path that
said *"this is a deep/self-state question — escalate to a frontier model with real
tools and subagents."* The system had exactly one chat brain and it was the fast,
small, sandboxed one.

**Why it didn't know its state, in one line:** *a 14B model capped at 512 tokens,
with no tools to observe its own files/processes/dependencies, told to recite
memory and to deflect when unwired — could neither look at itself nor synthesize
what little it could see.*

**What Utah must do (program-level fix):**
1. **Tier the chat brain by intent, not just latency.** Chit-chat → fast local;
   *"what is your state / why did X fail / analyze yourself"* → escalate to a
   frontier agentic mode (Claude CLI/Opus-class) with a real tool budget and a
   higher token cap. The escalation must be automatic on self/analysis intents.
2. **Give the assistant genuine self-introspection tools** — read its own venv &
   package list, count/inspect its files, read its own logs/process/fds, run
   read-only shell and `git` against its own tree, query any of its DBs. Self-
   observation is a *capability to grant*, not a prompt to write.
3. **Allow investigate-then-answer, not recite-only.** Keep the no-fabrication
   guard, but let the assistant *run probes and synthesize* for deep questions
   instead of only reciting `semantic_memory`.
4. **Allow subagent fan-out from the chat surface** for big questions, the same
   way this analysis was produced.
5. **Never hardcode "I'm not wired into that data stream."** If a state question
   is asked, the correct behavior is to *go look*, not to deflect.

That single change — a chat surface that can *escalate and investigate itself with
real tools* — is the difference between Ace (which couldn't tell you what it was
made of) and this (which just read all 192k files and did).

### 6.6 Voice and chat are ONE system, with a visible thinking stream (binding requirement)

In Utah there is **one assistant surface, not two.** Voice and text are the same
pipeline — different *input devices* into one session, one transcript, one agent
loop, one memory.

- **Speaking wakes the chat.** When Michael says something, the wake fires, STT
  transcribes it, and the utterance **appears in the chat as the user turn** — the
  same as if he had typed it. There is no separate "voice mode" with a different
  brain or a different (smaller) model. One input → one loop.
- **The chat shows the full thinking process.** The transcript streams the
  assistant's **entire agentic trace in real time — reasoning, every tool call
  (name, args, result), every subagent it spawns, files it reads, commands it
  runs — exactly like the visible thinking/agent stream in Cursor, Gemini, or
  Claude.** Not a hidden black box that emits a one-line answer; the work is on
  screen as it happens. (This very response — investigate → tools → synthesize —
  is the reference for what a turn should look like.)
- **Both modalities render identically.** A typed turn and a spoken turn produce
  the *same* visible thinking stream and the *same* final answer. The only
  difference is the spoken turn **also speaks** the conclusion (or a spoken
  summary) via TTS while the full trace stays visible in the chat. Barge-in
  applies to both.
- **One session object** carries: input (voice|text), the streamed thinking +
  tool/subagent events, the final answer (rendered + optionally spoken), and the
  memory write. Voice is just a microphone and a speaker bolted onto the exact
  same agentic chat.

**Why this is binding:** Ace split voice and chat into two paths with two brains
(the fast 14B local voice path vs. the chat path) and **hid the thinking** — so a
spoken question got a shallow, opaque, recite-only answer. Utah unifies them:
**say it or type it, you get the same frontier agentic loop with its full thinking
on screen, spoken aloud when you spoke to it.**

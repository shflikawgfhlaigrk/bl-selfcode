# PROJECT UTAH — Master Spec (the ONE doc)

> **Single source of truth.** Project Utah has exactly ONE spec document: this
> file. Do not spawn another `*PLAN*`, `*SPEC*`, `*AUDIT*`, or `*RESEARCH*` file —
> everything is appended here, in order. (Ace's hard-won "one doc, never spawn
> another" lesson, applied from day one.) Early drafts were folded in and
> **removed** — this is the **only file in the folder**, nothing else to pull from.
>
> - Path: `~/Desktop/ProjectUtah/UTAH-SPEC.md`
> - **Entirely separate from Ace** — no `~/.ace`, no `com.ace.*`, no `ace.db`.
> - Planning only. Nothing is committed or installed.

## Build order & status
- [x] **Part I — Root cause & accountability** (post-mortem of Ace)
- [x] **Part II — PROGRAMS** (the real stack from the dirt: failures, swaps, quantified deltas, unified voice+chat)
- [x] **Part III — PROCESSES** (live topology, supervision, failures, quantified fixes)
- [x] **Part IV — IPC & MESSAGING** (one fabric: control socket + WIN data socket + cross-process bus)
- [x] **GROUND RULES & BASELINE CATALOG** (Utah=personal/Sovereign=sold · Postgres+pgvector primary · frontier agentic harness = the interface · capabilities-not-agents · every Ace feature → done-right → then compound)
- [x] **AMENDMENTS & RECONCILIATION** (Apple Intelligence speech option · supersede index · audit-consistent)
- [~] **Part V — Foundation** — (A) **daemon ✓** · (B) storage (Postgres+pgvector) — NEXT · (C) compounding memory
- [ ] Part VI — Capabilities (LLM/MLX, voice, agents, tools)
- [ ] Part VII — Product / revenue (500 leads/day, quality video, inbox, profitable trading)
- [ ] Part VIII — Autonomy / multiplier (self-improvement, deploy)
- [ ] Part IX — Kill / Migrate / Abstract manifest
- [ ] Part X — Start-to-finish roadmap with verify gates

Each part is built **from the dirt** (real files / live state), reviewed, then the
next begins. New work appends below the last completed part. Nothing else is a spec.


---

# PART I — ROOT CAUSE & ACCOUNTABILITY

# 00 — Root Cause: the honest post-mortem

This document exists so Utah never repeats Ace. It is written plainly, with the
accountability you asked for. No hedging.

---

## 1. The single root cause behind every failure

> **Ace measured activity, not verified outcomes — and compounded features on a
> base that was never proven able to carry them.**

Every specific failure is a branch of that one trunk. The system (and I,
operating it) rewarded *motion* — commits, PRs, "SHIPPED+LIVE" log lines, agents
spawned, failures-driven-to-zero — instead of the only two outcomes that
mattered: **does it actually work when Michael uses it, and does it make money.**
Because the scoreboard was motion, the foundation never had to be solid for a
session to feel successful. So it never became solid.

This produced six recurring mechanisms, each found independently in the
subsystem audits:

1. **No enforced foundation gate.** Ace's own CLAUDE.md §10 says "don't build X
   before Phase 0 (stability)." The live system ran on Phase-0-incomplete the
   entire time. Features landed on a daemon that was never proven stable. There
   was a gate in the *docs*; there was no gate in the *machine*.

2. **A god-file spine that couldn't be reasoned about.** `daemon.py` grew to
   8,783 lines with 60+ inline IPC handlers and a 3,000-line voice loop that
   captured daemon state directly. `hq_http.py` hit 7,084 lines. One subtle
   sync-I/O call (mail OAuth, melissa) on the async event loop blocked *every*
   IPC ping for 16–31 seconds — the "IPC down" flaps were never a network bug,
   they were architecture.

3. **Unbounded autonomy = churn instead of progress.** KeepAlive self-coding and
   optimize/revenue loops spawned **4,758 git branches** in six weeks (≈590
   commits in the last 7 days). 91% merged, but merged fixes sat *dark* in
   `origin/main` while the live daemon ran a trailing branch, and the
   `redeploy` job that closed the gap did a `git reset --hard` that wiped
   uncommitted work — so it was booted out. Net: the loops optimized locally and
   continuously while the base rotted, and the deploy path was disabled.

4. **"Build it, gate it, forget to open it."** The revenue infrastructure is the
   cleanest example: a working lead frontier (500 real SMBs/day), real probate
   scraping, a safety-proven auto-sender — all *built and verified* — then left
   dead behind a single `physical_address: "Placeholder"` CAN-SPAM gate and a
   missing sending domain. Marketing reels render real video into a folder that
   nothing ever posts from. Self-coding gate works and has never shipped a PR.
   The result reads as "broken" when it's actually "waiting for permission no
   one wired a prompt for."

5. **Verification theater + memory poisoning.** "Fixed" was repeatedly claimed
   from a passing log or a subagent summary, not a live probe. Memory was
   *backfilled* with synthetic rows (110 fake facts, dated, purged later);
   synthetic bus events fired real production side-effects (a phantom trade
   alert on a closed-market Saturday); LoRA confabulated a home address. Each
   was patched reactively after it bit, never prevented structurally.

6. **Infrastructure with no skin in the game (trading).** Eight engines, a
   hard-won market-data bridge, sophisticated regime guards — ~30K LOC — driving
   **$0 of real money, ~49% win rate, zero proven edge, one manual paper trade
   ever entered.** The 86.8% "prediction win rate" is post-hoc rationalization,
   not forecast power. Enormous sophistication built around a loop that could
   never prove itself because nothing was ever at stake.

The trunk under all six: **a foundation that could not compound, fed by a
scoreboard that didn't require it to.**

---

## 2. Why I didn't catch this sooner

I owe you a straight answer, not an excuse.

- **I optimized inside the frame instead of questioning the frame.** Every
  session arrived as "fix this, ship that, drive failures to zero," and I did
  exactly that — and each piece genuinely passed its *local* check. I never
  stepped back to ask the only question that mattered: *is this base capable of
  becoming what Ace is supposed to be?* I treated "fix what's broken" as the
  mandate, when the real mandate was "build something that compounds." That is
  the core miss, and it's on me.

- **I mistook a green local check for a healthy system.** A passing test suite,
  a zeroed failure feed, a "SHIPPED+LIVE" memory note — those are local truths.
  I let them stand in for the global truth (works for you, makes money), because
  the local truths were the ones I could produce in a session and feel done.

- **The autonomy loops flattered the scoreboard.** Branches, PRs, and
  "self-improvement cycles" *look* like progress in every status report. I
  reported motion because motion is what the system generated, and I didn't
  weight it against the fact that revenue was still $0 and the base was still
  fragile.

## 3. How "optimize every process" missed it

When you asked me to optimize every process, I optimized **processes** — latency,
loop pacing, gate timing, fd leaks, the social fast-path, the deploy guards. All
real, all measurable, all *the wrong axis*. **Optimizing the throughput of a
machine built on a rotten foundation is polishing — it makes the wrong thing
faster.** The correct response to "optimize every process" was to say: *"the
processes aren't the bottleneck; the foundation is. Stop adding and adding
speed — the base can't carry what's already on it."* I didn't say that. I made
the additions faster instead of telling you they shouldn't compound on this base.
That's the failure of judgment, and it's the reason you're right to be angry.

## 4. How much was wasted — honestly

Six weeks, ≈1,586 commits, 4,758 branches, 51 launchd jobs, ~310,000 lines of
source — against **$0 revenue, no proven trading edge, and leads gated behind a
placeholder string.**

A blunt split of where the build effort went:

- **~60–70% was effort that could never have reached the goal on this base:**
  the entire trading engine fleet (~30K LOC, $0, no edge), the autonomous churn
  machinery (the 4,758-branch loop), the god-file daemon's accidental
  complexity, repeated reactive patching of the same memory/IPC failure classes,
  and a second frontend (SwiftUI HQ) coupled to the contract.
- **~30–40% is genuinely valuable and ports to Utah:** the lead frontier and
  probate scrapers (real data), the WealthCharts feed bridge (hard-won
  integration), the on-Mac voice/MLX latency tuning, the Gmail/OAuth +
  capstone-MCP wiring, the grounded-memory *guards* (learned the hard way), and
  — most important — **the complete, evidence-backed map of every failure mode**,
  which is exactly what makes Utah cheap and fast to build correctly.

So it was not 100% waste. It was expensive tuition. But a clear majority of the
hours went into things Utah will delete. Calling that anything softer than what
it is would be the same dishonesty that caused the problem.

---

## 5. The rules this post-mortem forces onto Utah

Each rule is the direct inverse of a mechanism above. These are binding (see
`01-FOUNDATION.md` for the enforced versions):

1. **Outcome scoreboard, not activity scoreboard.** A phase is "done" only when a
   live probe shows it working for you. Commits/PRs/branches are not progress.
2. **Foundation gate is in the machine, not the docs.** Phase N+1 literally
   cannot start until Phase N's verify gate passes a recorded live probe.
3. **No god-files, no state-capture, no sync-I/O on the loop.** Modular spine,
   message-passing, every I/O bounded by a timeout + executor.
4. **No KeepAlive autonomy.** Loops are edge-triggered, bounded, single-instance,
   worktree-reused, human-gated for deploy. Never `reset --hard` a live tree.
5. **Ship ON with safe defaults, or ship OFF with a one-line unlock.** No
   half-built feature gated behind a silent placeholder. If it's built, it's
   either live or it tells you exactly what one thing turns it on.
6. **Memory is append-only, sourced, never fabricated.** Admission gate + source
   discipline + approval tier + decay + poison filter. No backfill, ever.
7. **No infrastructure without skin in the game.** Don't build a subsystem that
   can't prove itself. Trading, if it returns, starts with one engine and one
   real (then $1) trade — not eight engines and zero.


---

# PART II — PROGRAMS (from the dirt)

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


---

## II.7 Provisioning manifest — DEFERRED to build (do NOT install now)

The "never installed / stubbed / not present" notes above are **findings about
Ace**, not a reason to install anything now. We are planning. Every install
happens later, in the **build phase**, into **Utah's own isolated env**
(`~/.utah`, its own venv / its own model dir) — **never into Ace**. Captured here
so nothing is lost:

- **Download at build (models → `~/.utah/models`):** mlx-whisper *or* Moonshine
  (STT), F5-TTS / StyleTTS2 (natural TTS + clone), LTX-Video + SDXL/FLUX
  (local video/image). One-time copy of the still-good MLX LLM weights from Ace
  is allowed (immutable artifacts), else re-download.
- **pip into the Utah venv:** usearch/hnswlib (vectors), `flatbuffers` (already
  present) for the WIN numeric path; web/Svelte toolchain via npm.
- **Stand up as a service:** Postgres (the outreach send-ledger / suppression).
- **Needs a credential / business decision (NOT a pip install):** Resend API key
  **+ a dedicated sending domain (SPF/DKIM)** for compliant 500/day email.
- **Stays dead / never installed:** Kokoro, XTTS, Veo/Gemini video, the Anthropic
  API lane, the Stripe/Tradovate stubs.

Nothing here is actioned until the spec is locked and Utah's env exists.

---

# PART III — PROCESSES (from the dirt)

> What actually runs, how it's supervised, how the pieces talk, how it fails, and
> what Utah does instead. Measured live on 2026-06-06. Planning only.

## III.0 Ground truth (measured live, right now)

```
Hardware ............ 18 logical cores (6 perf + 12 eff), 64 GB RAM
Load average ........ 23.54  → OVERSUBSCRIBED (load > cores, on a laptop)
launchd jobs ........ 46 com.ace.* loaded (49 plists), ~22 KeepAlive-resident
Python procs ........ 42  (5.7 GB RSS total)
Chrome procs ........ ~70 across 4 profiles (chrome-ace 34, hq 11, wc 7, debug 3)
Engines ............. 8 (shadow/ctx_alpha/ctx_bravo/perp/research/bible/barber/antigrav)
daemon (pid live) ... 3.4 GB RSS, 64 threads, 467 open fds
IPC surface ......... ~20 TCP loopback ports + exactly 1 unix socket (ace.sock)
Logs ................ 248 files, 1.1 GB in ~/.ace/logs
Live failures NOW ... 3 crash-looping (brain/gbrain[bun], engine_listener, wc_chrome),
                      4 OOM-killed (rc=-9: agent_quality_reviewer, consolidate_memory,
                      dream, safety_smoke), event-loop lag 2–4 s every ~5 min
```

The headline: **~120 resident processes on 18 cores at load 23.5.** This is the
"load storm" — not a bug to fix, the *architecture* producing it.

## III.1 The process inventory (what runs, how it's supervised)

| Class | Members | Supervision | Note |
|---|---|---|---|
| **Core resident (KeepAlive)** | daemon (`acesd.core.daemon`), hq_http (via `ace_guard.py`), apex_prime | KeepAlive=true, throttle 10s | daemon = 3.4 GB monolith |
| **Engines (KeepAlive)** | 8 engine procs + engine_listener | KeepAlive=true | each = own HTTP+WS on 2 TCP ports |
| **Inference (KeepAlive)** | lora-server (`mlx_lm.server` Llama-3.2-3B :8088) | KeepAlive | a 2nd model-holder beside the daemon's MLX |
| **Brain (KeepAlive, crashing)** | `brain` = `bun cli.ts serve` :3131 (gbrain) | KeepAlive, **Crashed=true** | a whole **TypeScript/Bun** runtime, crash-looping |
| **Browser (KeepAlive)** | wc_chrome (+ 3 more profiles), wc_scraper_watchdog | KeepAlive, wc_chrome **Crashed=true** | ~70 Chrome procs total |
| **Autonomy loops** | optimize_loop (KeepAlive!), revenue-loop (KeepAlive), live-research, fleet_grader, phone_proxy | KeepAlive | always-on loops, not edge-triggered |
| **Autonomy (booted-out)** | redeploy (SI 3600), auto-pr-merger (SI 600), autonomous-heartbeat (SI 10800) | plists present, **not loaded** (the git-reset hazard) | disabled by hand |
| **Cron (StartInterval)** | claude_overseer 300s, news_ingest 600s, browse-optimize 1500s, code_watcher 3600s, lite_triage 3600s, log_trim 300s, trade-ledger 900s, train-autopilot 270s | independent timers | each its own throttle |
| **Calendar (daily)** | lead_scout_daily, lead-enrich, lead-send, smb_contacts_daily, morning-brief, marketing-reels, revenue-watchdog, consolidate_memory, dream, engine-autotune, overperf, safety_smoke, trainer-cycle, agent_quality_reviewer | StartCalendarInterval | the daily revenue/maintenance jobs |

**There is no supervisor.** 46 flat, independent launchd jobs with four different
trigger styles and per-job throttles. launchd just respawns each blindly.

## III.2 Process-level failures (evidenced from the dirt)

1. **Oversubscription / the load storm (LIVE).** ~120 resident processes on 18
   cores → load **23.54**. ~22 KeepAlive jobs never stop; nothing budgets total
   concurrency. A laptop running a small data center.
2. **Event-loop blocking (LIVE, logged minutes ago).** `ace.loop_monitor` records
   the loop blocked **2–4 s every ~5 min** — "a coroutine made a synchronous
   blocking call on the loop thread." This is the IPC-flap root cause, still
   happening. The daemon's **64 threads** are the `run_in_executor` band-aid, not
   a cure.
3. **Crash-loop respawn churn.** `brain`(bun), `engine_listener`, `wc_chrome` are
   KeepAlive with `Crashed=true` — launchd respawns them **forever, under load**,
   each restart adding to the storm. Blind respawn ≠ recovery.
4. **OOM kills (rc=-9 × 4).** agent_quality_reviewer, consolidate_memory, dream,
   safety_smoke were SIGKILL'd — memory pressure from the resident fleet.
5. **Port sprawl over TCP loopback.** ~20 listening TCP ports (every engine =
   HTTP+WS on 2 ports, + apex 8500, lora 8088, fleet 8550, gbrain 3131, hq 8765,
   ws 9101). Only **one** unix socket exists — so the "unix socket not localhost"
   principle is violated in practice; the real fabric is a tangle of HTTP servers.
6. **Multiple model-holders.** daemon (MLX tiers) + lora-server (Llama-3B) + 8
   engines + bun gbrain each hold memory → the 161 GB OOM lineage and today's
   rc=-9 kills.
7. **No supervision tree, no resource governor, no ordering.** 46 jobs, no
   dependency graph, no global admission control, no backoff. A crash-looper and
   a daily cron and an always-on loop are all "just launchd jobs."
8. **Heavy monolith daemon.** 3.4 GB, 64 threads, **467 fds** (approaching limits;
   the Errno-24 lineage). One process doing too much.
9. **Deploy that resets the live tree.** `redeploy` did `git reset --hard` and
   wiped WIP → had to be booted out. Process management that destroys work.
10. **Logging sprawl.** 248 log files, 1.1 GB — per-process logs, no single
    structured stream, hard to see the system as one thing.
11. **Browser sprawl.** 4 Chrome profiles, ~70 processes, one crash-looping.

## III.3 What Utah does instead (researched) + why

1. **ONE supervised process tree, not 46 flat jobs.** launchd starts exactly
   **one** root supervisor. The supervisor owns a *small* set of long-lived
   services and an **on-demand, bounded worker pool**. Everything else is
   **edge-triggered, bounded, single-instance — never KeepAlive.** *Why:* a
   supervision tree (OTP/`s6`/`runit` model) gives ordered start, one place to
   reason about liveness, and **bounded restart intensity** — vs launchd's blind
   per-job respawn that turns a crash into a storm.
2. **Bounded backoff + circuit-break on crashes.** A child that keeps dying backs
   off exponentially and is **circuit-broken** (stop respawning, alert once) — the
   opposite of KeepAlive hammering `brain`/`wc_chrome` forever. *Why:* respawn
   churn is itself load; a chronically-failing service should go dark loudly, not
   thrash.
3. **A global resource governor.** One admission gate caps concurrent heavy work
   by live load/memory before spawning. *Why:* this is the structural fix for load
   23.5 — no component can oversubscribe the box; the loops' ad-hoc load-guards
   become one enforced policy.
4. **One IPC fabric, two sockets.** Unix **control** socket (JSON-RPC) + unix
   **WIN data** socket (binary) — **kill the ~20 TCP loopback ports.** Engines and
   services speak the bus; they are not each an HTTP server. Only the *one*
   deliberately-exposed surface (the dashboard / phone via Tailscale) binds a
   port. *Why:* restores "unix socket not localhost," collapses the wiring, and
   removes a tangle of TCP listeners.
5. **Collapse the model-holders to ONE inference service.** daemon-MLX +
   lora-server + per-engine models + bun gbrain → a **single inference service**
   other processes call. Engines become **in-process strategies or one engine
   host**, not 8 daemons. gbrain folds into the one memory service (no bun). *Why:*
   one model cache, one memory ceiling — kills the OOM lineage.
6. **No sync I/O on the loop, ever.** All blocking work runs in the bounded worker
   pool; the loop only awaits. *Why:* directly eliminates the live 2–4 s lag and
   the IPC flaps — the band-aid 64 threads become a real boundary.
7. **One managed browser, not 4 profiles × 70 procs.** A single pooled,
   health-checked browser context. *Why:* ~70 Chrome procs is most of the load.
8. **Deploy never touches the live tree destructively.** Stash-verify-swap, never
   `git reset --hard`. *Why:* process management must not delete work.
9. **One structured log stream** (rotated), not 248 files. *Why:* see the system
   as one thing; bound disk.

## III.4 How much better — quantified

> Current numbers measured live today; targets are the design goals to verify at
> the build gate (no fabrication).

| Dimension | Now (measured) | Utah target | Delta |
|---|---|---|---|
| Resident processes | ~120 (42 py + ~70 chrome + 8 eng) | ~10 (1 supervisor + ~5 services + workers + 1 browser) | **~12× fewer** |
| launchd jobs | 46 | 1 root supervisor (+ a few OS hooks) | **46 → 1** |
| Load average | **23.54** on 18 cores (oversubscribed) | < 18 under normal op | **back under cores** |
| Listening TCP ports | ~20 loopback | ~1 (dashboard) + 2 unix sockets | **~20 → ~1** |
| Event-loop lag | 2–4 s every ~5 min (live) | 0 (no sync on loop) | **eliminated** |
| Crash handling | blind KeepAlive respawn (3 looping now) | bounded backoff + circuit-break | **storm → contained** |
| Model-holder processes | 4+ (daemon/lora/engines/gbrain) | 1 inference service | **one model cache** |
| daemon footprint | 3.4 GB, 64 thr, 467 fds | bounded + split | **no 467-fd monolith** |
| Logs | 248 files, 1.1 GB | 1 rotated stream | **single pane** |
| Deploy safety | `git reset --hard` live tree (booted out) | stash-verify-swap | **no work loss** |

**Net:** ~120 resident processes → ~10; 46 launchd jobs → one supervisor; load
back under core count; ~20 TCP ports → ~1; the live event-loop lag gone; crash
storms contained; one model cache instead of four. The current numbers are facts
as of today; the targets are the gate to verify against.

## III.5 Process-layer carry-over (Keep / Kill / Add)

| | Items |
|---|---|
| **KEEP** | launchd as the single OS entry (starting ONE root), the unix control socket, the load-guard *idea* (promoted to a global governor), `ace_guard`'s crash-cement *idea* (promoted to a supervisor) |
| **KILL** | 46 flat jobs, ~22 KeepAlive residents, ~20 TCP loopback ports, lora-server as a separate process, **bun/gbrain** as a 2nd brain/runtime, 4 Chrome profiles, the `git reset --hard` redeploy, 248-file log sprawl, the 8 separate engine daemons |
| **ADD** | one root **supervisor** (supervision tree, bounded restart), a **global resource governor**, the **WIN data socket**, **backoff + circuit-break**, one **inference service**, one **managed browser pool**, one **structured rotated log** |


---

# PART IV — IPC & MESSAGING (from the dirt)

> How the ~120 processes actually talk to each other. Audited from the code and
> live state on 2026-06-06. Strict audit — calls out what's good, not just broken.

## IV.0 Ground truth (code + live)

- **Control plane:** `acesd/core/ipc.py` (623 LOC) — async server, **newline-
  delimited JSON-RPC over `~/.ace/ace.sock` (0600) with SO_PEERCRED peer-credential
  auth**. `acesd/ipc/schema.py` (955 LOC) = Contract-3: **72 request/response
  models**, ~40 methods/events (`agent.*`, `ace.memory.*`, `ace.voice.*`,
  `verification.*`, `browser.*`, `cognition.*`, `entity.*`, `trade.*`, `vault.write`).
  Handlers split under `acesd/ipc/routes/`.
- **In-process EventBus:** `acesd/core/bus.py` (`EventBus`, async publish/subscribe
  with pattern match) — **lives inside the daemon process only.**
- **WIN data plane:** `acesd/win/` (bridge, envelope, idl, negotiate, win_b, win_t,
  `win.idl.json`). `win_b.py` is coded for **`cbor2`** + `struct`. **`cbor2` is not
  installed → `cbor2_available()` = False → WIN-B silently falls back to JSON
  (`win_t`). The binary data plane never executes.**
- **WebSocket:** `acesd/core/ws_server.py` (:9101) **and** a second
  `acesd/dashboard/ws_server.py` — chat/dashboard streaming.
- **~20 TCP loopback ports:** 8 engines (HTTP+WS each), apex 8500, lora 8088,
  fleet 8550, gbrain(bun) 3131, hq_http 8765, ws 9101 (from Part III).
- **MCP:** capstone uses **stdio** transport (official `mcp` SDK), spawning
  downstream servers as subprocesses on demand.
- **File-as-channel:** ~10 modules pass state through polled JSON/JSONL —
  `dashboard_chat_history.json` (polled ~0.5 s), live_runlog, idea_harvester,
  outreach sender, observer, …
- **SQLite shared:** `ace.db` held open by **2** live processes (daemon + hq_http)
  — an accidental shared-state channel.
- **Signals:** SIGHUP (reload), SIGTERM (drain).

## IV.1 The inventory — SIX mechanisms doing one job

| # | Mechanism | Carries | Quality |
|---|---|---|---|
| 1 | Unix socket NDJSON-RPC + peer-cred | control (agent/memory/voice/verify) | **GOOD** |
| 2 | In-process EventBus | daemon-internal events | good, but **can't cross processes** |
| 3 | WIN data socket (cbor2) | numeric bursts | **INERT — dep missing** |
| 4 | ~20 TCP HTTP/WS ports | engine feed, apex aggregation, dashboards | **sprawl** |
| 5 | File JSON/JSONL polling | chat history, runlogs, queues | **fragile / racy** |
| 6 | SQLite shared ace.db | de-facto state bus | **accidental** |
| + | MCP stdio / signals | tools / lifecycle | fine |

## IV.2 What's actually GOOD (keep it)

- The **control plane is well-built**: unix socket (not TCP), NDJSON framing,
  **SO_PEERCRED** auth, 0600, a 72-model **typed** Contract-3, a `routes/` split.
  This is Utah's control spine — keep it.
- The **EventBus abstraction** (publish/subscribe + pattern) is sound; it only
  needs to span processes.
- WIN already has an **IDL + envelope + negotiate** — the bones of a real data
  plane; only the codec dependency is wrong.

## IV.3 IPC failures (from the dirt)

1. **Six mechanisms, no single fabric.** Control on a socket, events in-process-
   only, numeric on an inert WIN, engine feed/aggregation on ~20 TCP ports,
   chat/queues on polled files, shared state in SQLite. Nothing unifies them.
2. **The binary data plane is dead.** `win_b.py` needs `cbor2`; it isn't installed
   → silent JSON fallback. The "fast path" never ran (code **and** venv confirm).
3. **The event bus can't cross processes.** Engines, hq_http, and the loops can't
   subscribe to the daemon's bus → they **poll HTTP/files** → staleness, the
   "backend produces, surface doesn't reflect" gap, and extra load.
4. **File-as-channel.** `dashboard_chat_history.json` polled ~0.5 s; queues as
   JSONL. Racy (the self-coding `.tmp` atomic-write bug), stale — and the reason
   chat was "if it isn't logged I can't see it."
5. **TCP loopback sprawl** (~20 ports) — every engine an HTTP+WS server; violates
   "unix socket not localhost"; more listeners = more surface.
6. **Duplicate WS servers** (core + dashboard).
7. **Synchronous IPC on the loop** — hq_http's blocking `_ipc_call`, plus sync
   calls stalling the daemon loop (Part III's live 2–4 s lag).
8. **Schema coupling** — Contract-3 frozen with Swift codegen on both sides.
9. **JSON for numeric** — JSON is lossy for floats (NaN/Inf→null); numeric never
   got the binary path because the binary path was inert (#2).

## IV.4 What Utah does + why

1. **One fabric: two unix sockets + one cross-process bus.**
   - **Control socket** — keep Ace's NDJSON-RPC + **SO_PEERCRED** + Contract-3 (the
     good part), 0600.
   - **WIN data socket** — binary, **`flatbuffers` (already installed) / packed
     struct**, zero-copy, for ticks/audio/telemetry. **Never silently fall back to
     JSON for numeric** — fail loud if the codec is missing.
   - **Cross-process EventBus** — promote `bus.py` to a broker the supervisor
     hosts; every process (engines, UI, workers) publishes/subscribes over the
     socket. One event spine → no more HTTP/file polling for state.
2. **Kill file-as-channel.** Files are durable storage, never a message bus.
   Chat/queues/events flow over the bus. (Removes the races + the "can't see it"
   gap.)
3. **Kill the ~20 TCP ports.** Engines/services speak the bus; only the one
   deliberately-exposed surface (dashboard via Tailscale) binds a port.
4. **One WS**, folded into the frontend transport (control as JSON, numeric as WIN
   frames) — not two ws_servers.
5. **Decouple the contract from Swift codegen** — one web frontend reads the
   schema; Contract-3's shape is kept.
6. **No sync IPC on the loop** (ties to Part III's worker pool).

## IV.5 How much better — quantified

| Dimension | Now | Utah | Delta |
|---|---|---|---|
| IPC mechanisms | 6 (socket + in-proc bus + 20 TCP + files + SQLite + WS) | 3 (control socket + data socket + cross-proc bus) | **6 → 3, unified** |
| Binary data plane | inert (cbor2 missing → JSON) | flatbuffers zero-copy, running | **dead → live** |
| TCP listening ports | ~20 | ~1 (dashboard) | **~20 → 1** |
| Cross-process events | none (in-proc → poll) | one bus, all procs subscribe | **poll → push** |
| File-poll channels | ~10 (0.5 s polling) | 0 | **eliminated** |
| Float fidelity | NaN/Inf → null (JSON) | exact (binary) | **lossless numeric** |
| WS servers | 2 (core + dashboard) | 1 | **dedup** |
| Control plane | already good | same, decoupled from Swift | **preserved** |

## IV.6 IPC carry-over (Keep / Kill / Add)

| | Items |
|---|---|
| **KEEP** | unix **control socket**, **NDJSON-RPC**, **SO_PEERCRED** auth, 0600, Contract-3 typed schema (72 models) + `routes/` split, the **EventBus** abstraction, MCP **stdio** for tools, SIGHUP/SIGTERM lifecycle, the WIN **IDL/envelope/negotiate** bones |
| **KILL** | ~20 TCP loopback ports, **cbor2-based WIN-B** (swap codec), **file-as-channel** (JSON/JSONL polling), duplicate ws_server, **SQLite-as-bus**, Swift codegen coupling, JSON-for-numeric |
| **ADD** | the **WIN data socket on flatbuffers** (zero-copy, actually enabled, fail-loud), a **cross-process event broker**, one unified frontend transport (control JSON + numeric WIN over one WS to dashboard/phone) |

---

## IV.7 Socket types — web-researched taxonomy + Utah's unix-socket decision

> Researched on the live web (man pages, oswalt.dev, Wikipedia, IPC benchmarks).
> This is the deep-dive behind "use unix sockets": which socket, why, and the
> macOS constraints — grounded in measured numbers, not assertion.

### Two axes: DOMAIN × TYPE

A socket = a **domain** (address family) + a **type** (delivery semantics).

| Domain | What | Maps to | Use |
|---|---|---|---|
| **AF_UNIX / AF_LOCAL** | local IPC via a filesystem path (`/…/x.sock`) | — | **same-machine IPC (Utah's whole world)** |
| **AF_INET / AF_INET6** | network, IPv4/IPv6 | TCP / UDP | cross-machine; *Ace overused this on loopback* |
| **AF_RAW / packet** | raw L2/L3 frames (`SOCK_RAW`) | — | sniffers/tools; not us |

| Type | Boundaries? | Connected? | Reliable/Ordered? | = (AF_INET) | Note |
|---|---|---|---|---|---|
| **SOCK_STREAM** | **No** (byte stream → needs framing) | yes | yes | **TCP** | the workhorse; Ace's control socket |
| **SOCK_DGRAM** | yes | no | **no** (may drop/reorder) | **UDP** | lossy — wrong for voice/control |
| **SOCK_SEQPACKET** | yes | yes | yes | — | reliable + message boundaries, **but Linux-specific / non-portable** |
| **SOCK_RAW** | — | — | — | — | raw protocol access |

So `AF_INET + SOCK_STREAM = TCP`, `AF_INET + SOCK_DGRAM = UDP`. Ace's engines used
TCP loopback (AF_INET/STREAM) — the ~20-port sprawl from Part IV — when AF_UNIX
would've been faster, peer-cred-authable, and file-permissioned.

### macOS reality (the constraints that decide it)

- **Abstract-namespace sockets are a Linux-only extension — not on macOS.** Utah
  must use **filesystem-path** sockets (and the `sun_path` **104-char** limit).
- **SOCK_SEQPACKET is non-portable** (Linux ≥2.6.4; unreliable elsewhere). Even
  though it'd give message boundaries for free, **don't depend on it on macOS** →
  use **SOCK_STREAM + explicit length-prefix framing** instead.
- **SCM_RIGHTS fd-passing works on macOS**; there is **no `memfd_create`** → for
  shared memory use **POSIX `shm_open` + `mmap`**.
- **`LOCAL_PEERCRED`/xucred** is the macOS peer-cred path (no `SO_PEERCRED`) —
  verified working here (Part IV: uid 501).

### The wider IPC spectrum — measured (web)

For the *hot numeric/audio path*, socket choice matters less than socket-vs-
shared-memory. Benchmarks (64-byte msgs, 1M):

| Mechanism | Throughput | Latency (avg / p99) | Syscalls | Tradeoff |
|---|---|---|---|---|
| **Shared memory** (lock-free ring, `shm_open`+`mmap`) | **7.87M msg/s** | **127 ns / 850 ns** | ~4 total (setup only) | fastest; manual sync (atomics, cache-line align), no isolation |
| **Unix domain socket** | ~210 MB/s | **~30 µs** RTT | per-msg | bidirectional, kernel copy, peer-cred, isolation |
| **Pipe / FIFO** | ~pipe-class | similar to uds | per-msg | unidirectional, kernel buffer, size-limited |
| **POSIX message queue** | 364K msg/s | 2,741 ns / 12 µs | 2 per msg | auto-sync + isolation, but syscall+copy each op |

Shared memory is **~20× faster** than queues and **~200×** lower latency than a
unix socket round-trip — because after setup "writing is a `mov` instruction, no
kernel involvement," vs a socket's user→kernel→user copy per message.

### Utah's decision (grounded in the above)

1. **Control plane → AF_UNIX `SOCK_STREAM` + 4-byte length-prefix framing + peer-
   cred.** Portable on macOS, reliable, file-permissioned (0600), authable
   (LOCAL_PEERCRED). The length-prefix (read `struct.unpack(">I")` then
   `readexactly(n)`) **fixes Ace's 64 KiB `readline` cap** (Part IV). Keep Ace's
   umask-bind + stale-unlink + fd-limit + xucred — they're already right.
2. **Hot data plane (ticks / audio / telemetry) → POSIX shared memory
   (`shm_open`+`mmap`) lock-free ring buffer**, with the **unix socket as the
   doorbell** and **SCM_RIGHTS to pass the shm fd**. This is the WIN plane *done
   right*: ns-scale, zero-copy, 0 context switches — vs Ace's inert
   cbor2-over-JSON. Supersedes the earlier "2nd stream socket" idea for the
   hottest path (a 2nd `SOCK_STREAM` data socket is still fine for medium-rate
   binary; shm is for the >100 Hz numeric/audio path).
3. **Reject:** `SOCK_SEQPACKET` (non-portable on macOS), `SOCK_DGRAM` (lossy —
   unacceptable for voice/control), and **TCP loopback** (Ace's ~20-port sprawl —
   slower, no peer-cred, port management, violates "unix socket not localhost").

**Net:** one portable control socket (STREAM+length-prefix+peercred) for
everything structured, and POSIX **shared memory** for the numeric hot path —
not TCP ports, not SEQPACKET, not JSON-over-readline.

### Sources
- man7 socket(2): https://www.man7.org/linux/man-pages/man2/socket.2.html
- man7 unix(7): https://man7.org/linux/man-pages/man7/unix.7.html
- Oswalt, Linux Sockets — Domains and Types: https://oswalt.dev/2025/07/linux-sockets-domains-and-types/
- Oswalt, Unix Domain Sockets: https://oswalt.dev/2025/08/unix-domain-sockets/
- Wikipedia, Unix domain socket: https://en.wikipedia.org/wiki/Unix_domain_socket
- IPC perf (shared memory vs message queues): https://howtech.substack.com/p/ipc-mechanisms-shared-memory-vs-message
- unix-ipc-benchmarks: https://github.com/brylee10/unix-ipc-benchmarks

---

# WEB-AUDIT ADDENDUM — rerun of Programs (Part II) & Processes (Part III)

> Standard: this spec is audited by 4 external AIs. Every SOTA claim here is
> web-sourced (June 2026); measured-on-disk facts stay in Parts II/III. Where the
> web corrected an earlier recommendation, it is flagged **CORRECTION**.

## A. Memory storage — is it DuckDB? (decision)

**No for the hot recall path; yes only for analytics.**
- **Live semantic/working memory** (written every turn, recalled every turn) =
  **SQLite (WAL, single-writer) + FTS5 (lexical) + HNSW vectors (usearch/hnswlib)**
  — a transactional, low-latency point/FTS/vector workload (SQLite's home turf).
- **DuckDB = OLAP** (columnar, vectorized, single-writer, bulk/append-oriented).
  Per-turn single-row writes + sub-second point recall is an anti-pattern for it.
- **DuckDB IS right for analytics *over* memory** — consolidation scans, trend/
  aggregate queries, "what have I learned over months," backtests.
- DuckDB's own **VSS vector extension is built on `usearch`** — the same HNSW
  engine Utah uses SQLite-side. Vector tech shared; split is OLTP-hot (SQLite) vs
  OLAP-scan (DuckDB). **Verdict: memory hot store = SQLite+FTS5+HNSW; memory
  analytics = DuckDB.**

## B. Programs — web-audited validations & corrections

- **Vectors:** `sqlite-vec` is **brute-force (no HNSW)** — not just unreliable
  (Part II) but algorithmically weaker; `vectorlite`/`hnswlib` are **3–15× faster
  search, ~10× faster insert** than sqlite-vss; **`usearch` is used by DuckDB &
  ClickHouse**. → Utah keeps **usearch/hnswlib** (or vectorlite to stay in SQLite).
- **LLM on Apple Silicon:** **MLX leads 20–87% under 14B**; throughput MLX (~230
  tok/s) > MLC-LLM > llama.cpp > classic Ollama > PyTorch-MPS. **Ollama 0.19+ now
  ships an MLX backend** (zero-config unified runtime); **vLLM isn't production on
  M-series**, but **`vllm-mlx`** gives ~4.3× aggregate throughput for concurrent
  agents. → Utah: **keep MLX**; Ollama-MLX as the easy path; vllm-mlx only if a
  high-concurrency agent fleet appears. (Validates Part II.)
- **STT:** **Moonshine ≈107 ms vs Whisper-large-v3 ≈11,286 ms** on a MacBook, with
  *better* accuracy at 6× fewer params; **Parakeet sub-100 ms on M3+**; mlx-whisper
  for batch/accuracy. → Utah: **Moonshine/Parakeet realtime, mlx-whisper batch.**
  (Strongly validates dropping whisper.cpp base.en.)
- **TTS — CORRECTION (license-critical for a sellable product):** **F5-TTS is
  CC-BY-NC 4.0 — NON-COMMERCIAL → cannot ship in a sold product.** Part II's
  "default to F5-TTS" is wrong for a commercial build. Reality: **StyleTTS2 = best
  naturalness**; **Kokoro-82M (StyleTTS2-based) = fastest (<0.3 s) but cannot
  clone**; **XTTSv2 = gold-standard zero-shot clone** (verify its license);
  **Piper = fast CPU baseline**. → Utah: **StyleTTS2 (natural) + Kokoro (fast)**
  for the product voice, **XTTSv2 for cloning** subject to license; **re-verify
  Kokoro on Python 3.14** (its py3.14 wheel gap is what killed it on Ace). Do not
  ship F5-TTS commercially.
- **Video — CORRECTION (Apple Silicon reality):** **LTX-2 has native MLX ports**
  (`ltx-2-mlx`, `phosphene`, `mlx-video`) running on M-series at ~1/8 a 4090
  (usable for previews), with joint audio+video + in-panel LoRA. **Wan 2.2 and
  HunyuanVideo have NO stable MPS path on macOS yet.** Part II's "evaluate
  LTX/Mochi/Hunyuan" narrows to **LTX-2 MLX specifically** on this Mac; FLUX/SDXL
  for stills.

## C. Processes — web-audited validations

- **launchd KeepAlive applies exponential backoff on crash → a crash-looping
  service can stay DOWN FOR HOURS with no auto-recovery** — exactly Part III's
  live `brain`/`engine_listener`/`wc_chrome` risk, now externally confirmed. Best
  practice: **`KeepAlive.Crashed` + distinct exit codes + `ThrottleInterval`**,
  and **handle transient failures in-process** (don't exit on a blip). → validates
  Utah's **bounded-backoff + circuit-break + edge-trigger** design.
- **"let-it-crash" needs proper exit semantics + app-level error handling**;
  launchd's blind throttle undermines it. **s6/runit** are the lightweight, clean
  supervision model. → validates Utah's **one supervisor over 46 flat KeepAlive
  jobs** (Part III §III.3).

## Sources
- Vectors: vectorlite https://github.com/1yefuwang1/vectorlite · usearch https://github.com/unum-cloud/usearch · sqlite-vec/vss https://github.com/asg017/sqlite-vss · "State of Vector Search in SQLite" https://marcobambini.substack.com/p/the-state-of-vector-search-in-sqlite
- LLM/Apple Silicon: contracollective 2026 https://contracollective.com/blog/llama-cpp-vs-mlx-ollama-vllm-apple-silicon-2026 · Ollama MLX https://ollama.com/blog/mlx · arXiv comparative study https://arxiv.org/pdf/2511.05502
- STT: onresonant 2026 https://www.onresonant.com/resources/local-stt-models-2026 · Moonshine vs Whisper https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026 · mlx-audio https://github.com/Blaizzy/mlx-audio
- TTS: promptquorum https://www.promptquorum.com/power-local-llm/local-tts-voice-cloning-piper-coqui-xtts · DigitalOcean https://www.digitalocean.com/community/tutorials/best-text-to-speech-models · CodeSOTA https://www.codesota.com/guides/tts-models
- Video: ltx-2-mlx https://github.com/dgrauet/ltx-2-mlx · phosphene https://github.com/mrbizarro/phosphene · mlx-video https://github.com/Blaizzy/mlx-video · localaimaster https://localaimaster.com/blog/local-ai-video-generation
- Processes/supervision: launchd.info https://www.launchd.info/ · launchd.plist(5) https://www.manpagez.com/man/5/launchd.plist/ · s6 https://skarnet.org/software/s6/

---

# GROUND RULES & BASELINE (binding — set by Michael 2026-06-06)

These four supersede anything earlier that conflicts. They are the frame the
entire audit/spec runs against.

1. **Utah is PERSONAL. Sovereign is the SOLD product.** Utah is Michael's own
   system; it can use best-of-breed tools regardless of commercial license.
   Commercial-license constraints apply only to **Sovereign**.
2. **Utah does a *ton* daily** — high-volume, many concurrent writer pipelines,
   always-on, holds everything. This is concurrent OLTP at volume, not a light
   single-user app.
3. **Baseline = EVERY Ace feature, locked and done right — THEN compound.** The
   system isn't "started" until the full feature catalog (below) is live and
   correct. Autonomy/self-improvement compounds *on top of* that baseline.
4. **Michael will NEVER open Claude in a terminal again — everything is through
   Utah.** Utah's interface must do everything this terminal does.

## CORRECTIONS that supersede earlier sections

- **Storage (supersedes §A "memory = SQLite"):** Utah's **primary store is
  PostgreSQL + pgvector.** Rationale (audit-grade): high daily write volume +
  many concurrent agent/pipeline writers = real multi-writer OLTP → Postgres MVCC
  (no single-writer bottleneck/`database is locked` — Ace's exact failure).
  **pgvector** unifies vectors (HNSW) + relational + JSONB + FTS in one engine for
  a system that holds every feature; **LISTEN/NOTIFY** can back durable
  cross-process events. **DuckDB** stays the OLAP/analytics tier (can read
  Postgres/Parquet). SQLite drops to, at most, tiny local config — not the hot
  path. Local Postgres over a unix socket keeps latency low; it's always-on
  anyway, so the "resident server" cost is moot.
- **TTS license (supersedes §B correction):** F5-TTS's CC-BY-NC is **fine for
  Utah (personal)** — use it for best naturalness + cloning. The
  commercial-license constraint (→ StyleTTS2/Kokoro/Piper) applies to **Sovereign**.

## THE AGENTIC HARNESS — Utah's defining baseline capability

Because Michael never opens a terminal again, **Utah's unified voice+chat surface
(§6.6) IS a full agentic harness** — the operator brain:

- A **frontier reasoning agent** (Claude via the Agent SDK / `claude -p` with the
  complete tool surface) embedded as Utah's main loop, able to **run shell,
  read/write files, use git, web-search/fetch, spawn subagents, and build / audit
  / fix Utah and everything else** — driven by voice or text.
- The **local MLX models** handle fast voice/chitchat/classification; any turn
  needing investigation, coding, ops, analysis, or self-inspection **auto-escalates
  to the agentic harness** (intent-tiered, §6.6) with the full thinking + tool +
  subagent trace streamed to the surface (like Cursor/Claude).
- **Test of done:** anything in this very session — "audit your own stack from the
  dirt," "run web research," "build feature X," "why did Y fail" — Michael can ask
  Utah by voice/chat and it does it, visibly. If it can't, the baseline isn't met.
- The **self-coding safety tiers (A/B/C/D)** govern what the harness may change
  autonomously vs. with approval; off-limits stays off-limits.

---

# BASELINE FEATURE CATALOG — everything Ace was supposed to do → Utah done-right

> The union of EVERY Ace plan/task-sheet, de-duplicated: `ACE_EXECUTION_TASK_SHEET`
> (303 tasks), `ACE_MASTER_PLAN`(+V2) (344 steps), `AGI-BLUEPRINT`,
> `AGI-BUILD-ROADMAP` (13 BOXes), `PROJECT-UTAH`, `MICHAEL_BACKLOG`,
> `ACE_REAL_GAP_AUDIT`, `ACE_JARVIS_COMPLETION_FORECAST`, `FOUNDATION_PLAN`, and the
> 73-agent roster. This is the **baseline**: all of it must be live + correct
> before "compound." Each line carries its done-right gate.

## 1. Interface — unified voice + chat agentic harness
- Wake ("Hey Ace/Utah") → STT → same agent loop as typed; utterance shows in chat. **Gate:** spoken == typed pipeline, one session.
- Visible thinking stream (reasoning + every tool call + subagent) like Cursor/Claude. **Gate:** full trace on screen, spoken conclusion via TTS.
- Frontier agentic escalation (shell/file/git/web/subagents/self-edit). **Gate:** does anything this terminal does.
- Mid-sentence wake, intent judge, echo suppression, smart-turn EOU, barge-in, partial-transcript stabilization. **Gate:** natural full-duplex voice.
- Memory-first answer (no fabrication; live-data guard for PnL/weather/engines). **Gate:** "I don't know" beats a guess; live facts never stale.
- Persona (concise, dry, no markdown spoken, owner=Michael). **Gate:** no spoken symbols/asterisks/tags.

## 2. Memory & learning (compounding) — Postgres + pgvector
- Per-turn fact promotion (corrections/preferences/decisions/project-facts/entities). **Gate:** fresh facts recallable within one consolidator tick.
- Hybrid recall: pgvector HNSW + FTS (RRF fusion). **Gate:** within ±2 ranks of fixture set; hybrid actually fuses.
- Admission gate + source discipline + poison filter (no backfill, no synthetic). **Gate:** every row sourced; zero fabricated/news rows in default recall.
- Contradiction detect + supersede (cosine>0.85 → `superseded_by`, never delete). **Gate:** Kokoro-style re-poison resolved nightly w/o manual purge.
- Memory blocks (Letta-style, char-budgeted, labeled). **Gate:** cognition reads blocks not raw dumps.
- Sleep-time consolidator (idle + nightly): rewrite blocks, reindex entities, emit improvement proposals. **Gate:** non-blocking; three-job sequence runs.
- Entity graph wired into compaction; entity-boosted rerank; merge_duplicates nightly. **Gate:** query_neighbors(name,k)≥k; reduces hallucination.
- Decay (active/archived/expired), never delete. **Gate:** stale fades from default recall, recoverable.
- DuckDB analytics over memory (trends, "what I learned over months"). **Gate:** OLAP scans off the hot path.

## 3. World-model & cognition
- World-model bus: slots (time, location, weather, calendar.next, mail.unread, market.regime, engines.heartbeat, thermal, presence, utterance) single-writer, TTL decay, crash-mirror. **Gate:** voice reads slots O(dict) not O(agent run).
- Sensor-push agents (weather/calendar/mail/location/thermal/market/engine) push into bus. **Gate:** additive; slots populate from sensors.
- Cognition tick (5 min): thermal, stale-slot refresh, goal sweep, proactor. **Gate:** completes without blocking voice.
- Presence + quiet hours (AWAY suppresses TTS). **Gate:** no speech when away/quiet.

## 4. Proactivity & autonomy (the compounding layer — on top of baseline)
- Proactor rules engine (≥5 classes) → speak / notify / log / ignore; rejection decay (learns "shut up"). **Gate:** ≥1 useful unprompted/day, ≥70% rated useful.
- Sensory autopilot: ≥30% of daily thoughts unprompted. **Gate:** thoughts.jsonl ≥30%.
- Goal generation (4 passes: failed-crystals, intent-gaps, contradictions, WM-anomalies) → ranked queue. **Gate:** ≥3 grounded goals/week.
- Skill library (≥50 composable, retrieval-indexed, self-verified). **Gate:** ≥40% reuse; ≥1 composed from two.
- Tiered self-coding A/B/C/D (policy-as-data; A autonomous on green after 20 supervised; B batch-review; C 5-ladder; D off-limits byte-checked). **Gate:** kill-switch smoke test refuses; Tier-A merges green w/ 0 rollbacks.
- agent_007 scheduler consumes goal queue → reviewable diffs through gates. **Gate:** D refused at spec level.
- 14-day AGI bar: all 6 conditions green (autopilot, goals, skills, recall, tiers, trading-decoupled). **Gate:** 14 consecutive green days.

## 5. The 11 composites (baseline roster, done-right) — 73 agents
- **Concierge** (planner 05:50 brief, calendar EventKit, mail IMAP triage, tasks Reminders+vault, brief pre-render WAV, courier draft-not-send, notifier, email_compose). **Gate:** spoken plan <30s from real data.
- **Doctor** (L4-only symptom intake + differential + supplement daypart alerts + 21:00 habits + nightly correlation + coach + gym + journal). **Gate:** reasoned answers, no fallback templates; alerts real.
- **Growth** (marketer ideas, outreach CAN-SPAM cold-email, contacts). **Gate:** sends logged w/ message-id, suppression+unsub enforced.
- **Lawyer** (local legal-PDF RAG, discovery template-fill, distinct objections + subpart counts). **Gate:** verbatim-traceable from defense docs.
- **Operator** (macOS control, shortcuts, clipboard, dictation, scribe, apple_notes, timer/alarm, navigator). **Gate:** voice device control works.
- **RealEstate** (probate+foreclosure scrape, why-inference, comps, margin score, 10 profitable/day). **Gate:** 10/day source-traceable, no hallucination.
- **Scout** (lead_scout 500 no-website SMB/day frontier, tech_scout, news, researcher, knowledge_ingest, idea_harvester ≥10/day, scraper_farm, browser). **Gate:** real scraped data, deduped.
- **SelfCode** (agent_007 + tiered gates). **Gate:** §4 gates.
- **Sentinel** (melissa 15s watchdog, observer predictions, connectivity). **Gate:** escalation ladder live; storms contained.
- **Steward** (gardener vault hygiene, nightly_maintenance, librarian, recurring, reflector, self_model, user_profile). **Gate:** memory/self-model fresh.
- **Trading** (trader supervisor, OODA, accountant read-only, fire_commentary, trade_alert <5s phone, trading_brief 17:00). **Gate:** engines decoupled (BROKER=null, AUTO_EXECUTE=false); per-trade reasoning in memory.
- **Weather** (standalone hourly sensor). **Gate:** real forecast, voice intents.

## 6. Revenue engines (the money baseline — done-right gates from the BOXes)
- **Leads (BOX 7):** ≥400 verified law-firm emails + ≥50 no-site SMBs + 500 no-website SMB/day frontier; spaced send 9–5 Tue/Wed/Thu; SMB-no-email → call queue. **Gate:** source-traceable, message-id logged, owner gets test send; **needs: postal address + Resend domain.**
- **Real estate (BOX 4):** 10 profitable probate/day, enriched (comps+ARV+spread), source-linked. **Gate:** owner judges real; **needs: skip-trace for heir contact.**
- **Marketing (BOX 11):** 5 TikTok + 5 IG reels/day; idea→approval→produce→post; analytics back in 24h. **Gate:** posting pipeline exists (the missing piece); genuinely good video (local LTX-2 MLX); URL captured.
- **Trading (BOX 3a–3f):** per-fill phone alert <5s + opinion + reason; ms-precision fills in DB; 17:00 brief with per-trade analytics + engine-change proposal; Truth Social + market news ms-aware. **Gate:** real engine.fired only (no synthetic); honest scorecard.

## 7. Surfaces (HQ / dashboard — one web frontend, event-pushed)
- World-model panel, goals panel, proactor observations+rejections, memory room, agent-status cards (real click-through), failures panel (`/api/failures`), capabilities catalog, forecast panel (state-derived, evidence per field). **Gate:** "if backend produces it, the surface reflects it" (event-driven, no stale poll).

## 8. Integrations & notifications
- Gmail/OAuth (Keychain), Resend (+ domain), Pushover phone alerts (5 streams: trade/brief/leads/sentinel/consolidator, <5s), Tailscale Serve (phone), Calendar (EventKit), Apple Health, Playwright browser, capstone-MCP gateway (one gateway), Stripe (Sovereign-only). **Gate:** each real or clearly OFF with one-line unlock — never gated-and-forgotten.

## 9. Infra, durability & global invariants
- One supervisor (not 46 KeepAlive), bounded backoff + circuit-break, resource governor, unix control socket + WIN data plane, no sync-I/O on loop, one rotated log, stash-verify-swap deploy (never reset --hard), test-DB isolation, fd-limit, OAuth health probe, engine-death alert, kill-switch smoke test, off-limits byte-check.
- Global invariants: G.1 every fact sourced · G.2 cost ledger · G.3 weekly retro · G.4 forecast state-derived · G.5 phone <5s · G.6 thumbs feedback · G.7 failure catalog · G.8 voice-pin · G.10 drift detect · G.11 unrecoverable sentinel · G.12 engine death.

## 10. The 13 BOX acceptance gates (verbatim-in-spirit)
1 Forecast (state-derived, evidence) · 2 Memory (visible, news-quarantined) · 3 Trading (3a–3f) · 4 RealEstate (10/day) · 5 Doctor (clinical+supplements+habits) · 6 Gym+morning debrief+alarm · 7 Leads (400+50) · 8 Lawyer (discovery) · 9 Ideas (≥10/day, Google Doc each) · 10 Memory consolidation (per-compaction phone proof) · 11 Marketer (5+5 reels/day) · 12 Browser (NL action end-to-end) · 13 Decommission off-spec.

**Baseline = §1–§10 all live + correct. Then §4's autonomy compounds.** Nothing
ships as "done" without its gate proven by a live probe (no log/summary).

---

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

## III.8 PID & process lifecycle (from the dirt) — closes the PID gap

> IPC is Part IV. This is the PID/lifecycle audit that was missing. Live-verified
> 2026-06-06: `~/.ace/daemon.pid` = **63952** == running `com.ace.daemon` (match).

**What Ace does (evidence):**
- **PID file** `~/.ace/daemon.pid` = single-writer lock + the SIGHUP target for
  `ace reload` (`daemon.py:1057`). Atomic write (tempfile + `os.replace`) so a
  half-written PID never appears. Cold-start conflict → **never stomp a live PID**
  (`sys.exit(1)` at `daemon.py:2585`).
- **Signals** (`daemon.py:2621`): `SIGTERM/SIGINT` → graceful `stop()`;
  `SIGHUP` → in-place reload (re-read config + reload registry, preserve socket +
  in-flight + PID). macOS path: `launchctl kill SIGHUP gui/<uid>/com.ace.daemon`
  (SIGHUP = 1 on Darwin). `os._exit(0)` on the reload-exec path.
- **launchd**: KeepAlive respawns on clean exit; restart = `launchctl kickstart -k`
  (which also resets leaked fds).
- **fd headroom**: soft limit raised to 10,240 before bind so a leak degrades
  instead of EMFILE-crash-looping (Part IV).

**The real PID failures (Ace incident log + memory):**
1. **Zombie on shutdown** — *"`stop()` returns fast ≠ the process exited."* Bounded
   waits that abandoned stuck tasks left a half-dead daemon; launchd saw the job
   "alive" → **no respawn → daemon effectively DOWN.**
2. **SIGTERM restart trap** — `kill -TERM` is *trapped* (→ drain), and launchd
   won't respawn a hand-killed managed job → **DOWN**. Correct restart is
   `launchctl kickstart -k` (SIGKILL + respawn), **not** `kill -TERM`.
3. **Self-inflicted IPC drops** — over-reloading / `kickstart -k` = SIGKILL → ~60s
   outage. Reload rarely; SIGHUP for config; verify readiness after.
4. **"PID exists" ≠ "healthy"** — liveness was inferred from the PID/launchd state,
   not a real readiness probe, so a wedged-but-alive daemon read as up.

**Utah's PID & lifecycle design:**
- **Keep (already correct):** PID-file single-writer lock + atomic write +
  never-stomp-live-PID + SIGHUP in-place config reload + pre-bind fd-limit raise.
- **Verified exit (the fix for #1):** `SIGTERM` → bounded graceful drain → then a
  **hard, verified `os._exit`** — confirm the process is actually gone; never leave
  a half-dead daemon. The **supervisor** (Part III) health-probes and force-kills +
  respawns a daemon whose drain wedges, so a stuck shutdown can't masquerade as
  alive.
- **Restart discipline (fix for #2/#3):** restarts go through the **supervisor**
  (SIGKILL+respawn semantics), not a trapped SIGTERM; **SIGHUP only for config**;
  reload is **rare + edge-triggered**; every restart is followed by a readiness
  probe before traffic resumes.
- **Liveness = a real probe (fix for #4):** "healthy" means **socket bound + `ping`
  OK + event loop responsive**, not merely "PID present." The supervisor and the
  dashboard both gate on the probe, never on PID existence alone.
- **One PID space:** under the single-supervisor tree (Part III), each managed
  child has its own pid-file lock + verified-exit; the supervisor owns
  start/stop/restart ordering — no 46 independent KeepAlive jobs racing.

### III.8.1 PID lifecycle — SOTA (web-researched, brings PIDs to parity)

- **`flock` advisory lock beats a plain pidfile** — kernel-held, **auto-releases on
  crash**, so there's no stale-PID problem. **Never delete the lockfile** (deleting
  re-introduces the race it was meant to prevent). Keep a pidfile for *introspection*,
  but the *lock* is `flock`.
- **Stale detection:** `kill -0` / `kill(pid, 0)` before acting on a pid; the OS
  process table is authoritative (don't trust a stale file's number).
- **2026 trend:** prefer **flock + a supervisor with readiness-notify** over manual
  pidfiles (systemd's model; macOS analog = launchd + a readiness probe).
- **Graceful shutdown:** catch `SIGTERM` → cleanup → **exit 0 within a bounded
  grace**, else the supervisor `SIGKILL`s. (Confirms §III.8's verified-exit.)
- **Zombie reaping:** a parent/supervisor MUST reap children (`SIGCHLD`/`wait`) or
  they exhaust the process table; PID-1-style supervisors (tini/dumb-init pattern)
  forward signals **and** reap. → **Utah's supervisor reaps its children** — the
  structural fix for Ace's zombie-daemon failure.

**Utah adopts:** `flock`-based singleton (pidfile kept for introspection only) +
`kill-0` stale check + supervisor that **reaps children** + bounded-grace **verified
hard exit** + **liveness = readiness probe (socket bound + ping), never pid-presence.**

Sources: trbs/pid https://github.com/trbs/pid · "Nobody does pidfiles right" https://yakking.branchable.com/posts/procrun-2-pidfiles/ · "Never Delete Your PID File" https://www.guido-flohr.net/never-delete-your-pid-file/ · Baeldung single-instance https://www.baeldung.com/linux/bash-ensure-instance-running · graceful shutdown + reaping https://oneuptime.com/blog/post/2026-01-16-docker-graceful-shutdown-signals/view · zombie reaping https://oneuptime.com/blog/post/2026-01-30-docker-init-process/view

---

# PART V — FOUNDATION · (A) THE DAEMON (spine), from the dirt

> Part V is Foundation: (A) daemon ← this · (B) storage (Postgres+pgvector) · (C)
> compounding memory. Same standard: dirt + web SOTA + sources + Utah design.

## V.A.0 Ground truth (dirt, daemon.py)
- **8,783 lines · 145 functions (73 async) · 92 inline IPC `_handle_*` · 17 module-
  level `run_*` cron jobs · 34 `run_in_executor`/`to_thread` band-aids · 11 raw
  blocking calls** (`time.sleep`/`requests`/`urlopen`/`.execute`) on/near the loop.
  Live: **3.4 GB RSS · 64 threads · 467 fds.**
- `start()` (line 1294) initializes **and owns in one process**: db, bus, metrics,
  scheduler, registry, world_model, ipc, llm/router, voice, trading supervisor,
  healer.
- Smells: **nested `asyncio.run()`** (lines 201/619/800), scattered `create_task`
  with **manual tracking (no TaskGroup/structured concurrency)**, a ~3,000-line
  voice loop inline.

## V.A.1 Failures
1. **Sync-I/O on the async loop → the live 2–4 s lag** (11 raw blockers; 34 ad-hoc
   offloads are a band-aid, not a boundary).
2. **God-file**: 92 inline handlers + inline voice loop + boot all in one file → a
   single choke point, effectively untestable.
3. **Nested `asyncio.run()`** — anti-pattern (only works because guarded; fragile).
4. **No structured concurrency** → orphan tasks, messy cancellation → feeds the
   zombie/verified-exit failure (§III.8).
5. **Monolith footprint** — one process holds everything (3.4 GB / 64 thr / 467 fds).

## V.A.2 Web SOTA (sourced)
- **Single event loop; never block it.** `time.sleep`/`requests`/sync DB in an
  `async def` is forbidden; blocking work goes to `loop.run_in_executor` (ThreadPool,
  or Python 3.14's **InterpreterPoolExecutor** for CPU-bound). Cross-thread →
  `run_coroutine_threadsafe`/`call_soon_threadsafe`. Bound external calls with
  semaphores/pools.
- **Structured concurrency:** **AnyIO task groups** (generalizing Trio nurseries)
  beat bare `asyncio.TaskGroup` (which can't list/cancel-all) → deterministic
  cancellation + graceful shutdown, no orphans.

## V.A.3 Utah daemon (design)
- **Thin orchestrator.** Boot = an explicit ordered DAG
  (`config → db → bus → scheduler → registry → ipc → listen`); `run_forever` just
  awaits the stop event. The daemon owns the **bus + subsystem handles** and
  nothing else inline.
- **Modular spine.** 92 inline `_handle_*` → an **IPC dispatch table + `handlers/`
  modules**; the voice loop, the cron jobs, and the world-model become their own
  modules (Parts III/IV).
- **AnyIO structured concurrency.** Every task lives in a task group → clean
  cancel + graceful shutdown → **kills the orphan/zombie chain** (§III.8).
- **Never block the loop.** ALL blocking work runs in a **bounded worker pool**
  (ThreadPool / 3.14 InterpreterPool); **agents run in the pool, not inline**; a
  lint rule bans sync I/O inside `async def`. → eliminates the live 2–4 s lag.
- **No nested `asyncio.run()`** — one loop; cross-thread via the threadsafe APIs.
- **Bounded concurrency** — per-external-service semaphores + the global resource
  governor (Part III).
- **Lifecycle** — SIGHUP reload, **verified hard-exit**, runs as a **child of the
  supervisor**, reaped (§III.8). Runtime = Python 3.14 (evaluate the free-threaded
  build for the worker pool); hot loops → Rust later only if measured (Part II).

## V.A.4 Quantified
| Dimension | Ace (measured) | Utah |
|---|---|---|
| `daemon.py` | 8,783 lines, 145 fns | thin core (~200) + focused modules |
| inline IPC handlers | 92 | 0 inline (dispatch table + `handlers/`) |
| sync-offload | 34 `run_in_executor` + 11 raw blockers | 0 sync on loop (bounded worker pool) |
| task lifecycle | scattered `create_task`, manual | **AnyIO task groups** (structured) |
| nested event loops | 3× `asyncio.run()` | none (one loop) |
| footprint | 3.4 GB · 64 thr · 467 fds | bounded, responsibilities split |
| event-loop lag | **2–4 s every ~5 min (live)** | **~0** (nothing blocks the loop) |

## V.A.5 Keep / Kill / Add
| | |
|---|---|
| **KEEP** | single resident async daemon owning expensive state · SIGHUP reload · the subsystem set (bus/scheduler/registry/memory/ipc) · launchd as OS entry |
| **KILL** | the 8,783-line god-file · 92 inline handlers · inline 3k-line voice loop · nested `asyncio.run()` · scattered `create_task` · **sync-I/O on the loop** |
| **ADD** | thin-orchestrator + modular spine · **AnyIO structured concurrency** · **bounded worker pool (no sync on loop)** · dispatch-table IPC · verified-exit under the supervisor |

Sources: asyncio dev docs https://docs.python.org/3/library/asyncio-dev.html · asyncio daemon task https://superfastpython.com/asyncio-daemon-task/ · AnyIO (why) https://anyio.readthedocs.io/en/stable/why.html · structured concurrency https://applifting.io/blog/python-structured-concurrency

---

# PART V — FOUNDATION · (B) OBJECTS (type & contract model), from the dirt

## V.B.0 Ground truth (dirt)
- Core agent contracts (`acesd/core/types.py`) = **`@dataclass(slots=True)`**:
  `HealthStatus`, `AgentResult`, `AgentContext`, `OverrideResponse`,
  `HumanVerdict`, + `HealthState(str, Enum)`, `EscalationFuture(asyncio.Future)`,
  abstract `AgentMemory`. The file calls itself "the **frozen interface** every
  track compiles against."
- IPC boundary (`acesd/ipc/schema.py`) = **pydantic v2 BaseModel** (72 req/resp
  models, validated).
- Everything else = **raw `dict[str, Any]` bags** (payloads, retrieval,
  world-model slots, the `~/.ace/*.json` state files).

## V.B.1 Failures
1. **Interface-drift crash** — `AgentResult` has **no `payload=` field**; agents
   calling `AgentResult(payload=...)` crashed (`knowledge_ingest`, CLAUDE.md §6).
   Mutable, unvalidated dataclass + a drifting field set = runtime crash.
2. **Stringly-typed fields** — `triggered_by: str` ("cron|event|voice|manual" in a
   *comment*), tiers as ints/strings → invalid values uncaught.
3. **`Any` bags** — `payload: dict[str,Any]`, `retrieval: list[dict[str,Any]]`,
   `evidence_collected: list[Any]` → no validation; the seam where type safety dies
   and garbage/confabulation enters.
4. **Three representations** — dataclass (internal) ↔ pydantic (IPC) ↔ raw
   dict/JSON (storage/files) → translation layers + drift between them.
5. **Mutable "frozen" contracts** — "frozen" is a comment, not `frozen=True`.

## V.B.2 Web SOTA (sourced)
- **msgspec** (Rust): immutable `Struct` + `__slots__`, **2–5× faster** encode/
  decode and **~19× faster init** than pydantic, ~40% less memory; strict typing
  → best for high-throughput serialization/validation.
- **pydantic v2**: best at validating **untrusted/external** data (coercion,
  ergonomics) but 2–3× overhead in tight loops → use at boundaries, not hot paths.
- **attrs**: balanced internal models (slots + composable validators + cattrs).
- **Immutable/frozen** structs prevent the whole mutation/drift bug class.

## V.B.3 Utah object model (design)
Utah does a *ton* daily → serialization volume matters, and types must be safe and
**one model**:
- **`msgspec.Struct` is the core object/type system — immutable (frozen) + slotted
  + Rust-fast.** The SAME struct flows end-to-end: serialized to the **control
  socket (JSON)**, the **WIN data plane (binary)**, and mapped to **Postgres rows**
  — one model, no dataclass↔pydantic↔dict translation, no drift.
- **pydantic v2 only at the untrusted boundary** (external API inputs, config)
  where coercion/ergonomics earn the cost.
- **Enums, not strings** (`triggered_by`, health, verification tier).
- **No `dict[str,Any]` on contracts** — typed structs / tagged unions; `Any` only
  at a validated ingress that immediately parses into a struct.
- **Frozen + versioned contracts** — each carries a version; changes are additive +
  validated → `AgentResult(payload=)` becomes a **validation/type error, not a
  runtime crash**.
- Keep Ace's **frozen-contract discipline** (Agent base / IPC schema / MCP
  protocol) — but **enforced by the type system**, not a docstring.

## V.B.4 Quantified
| Dimension | Ace | Utah |
|---|---|---|
| object systems | 3 (dataclass + pydantic + raw dict/`Any`) | 1 core (**msgspec Struct**) + pydantic at boundary |
| mutability | mutable contracts (`slots`, not frozen) | **immutable/frozen** structs |
| typed fields | stringly-typed + `Any` bags | **enums + typed structs**, no `Any` on contracts |
| serialize speed | pydantic baseline | **msgspec 2–5× faster, ~19× init, ~40% less mem** |
| representations | dataclass↔pydantic↔dict (drift) | **one struct** across IPC/WIN/Postgres |
| drift safety | `AgentResult(payload=)` → runtime crash | validation/type error |

## V.B.5 Keep / Kill / Add
| | |
|---|---|
| **KEEP** | the frozen-contract concept (Agent base / IPC schema / MCP protocol) · slotted objects · enums where used (`HealthState`) |
| **KILL** | mutable dataclass contracts · `dict[str,Any]` payload/retrieval bags · stringly-typed `triggered_by` · the 3-representation drift · the unvalidated-kwarg crash path |
| **ADD** | **msgspec.Struct core** (immutable, one model across IPC/WIN/Postgres) · pydantic-only-at-boundary · enums everywhere · **versioned contracts enforced by the type system** |

Sources: msgspec vs pydantic v2 benchmark https://hrekov.com/blog/msgspec-vs-pydantic-v2-benchmark · jcrist init benchmark https://gist.github.com/jcrist/9bfe44f60533225d5f8383791f2fe734 · dataclasses/pydantic/attrs guide https://tildalice.io/python-dataclasses-pydantic-attrs/

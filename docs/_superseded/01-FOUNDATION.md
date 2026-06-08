# 01 — Foundation: doctrine + the spine

This is the single architecture source of truth for Utah's core. If any other
doc disagrees with this one, this one wins.

---

## 1. Doctrine (binding, enforced in code)

1. **Foundation-first.** Harden the base, prove it with a live probe in the same
   session, then compound. No layer lands on an unverified base. The gate is a
   real script that blocks the next phase, not a paragraph.
2. **Every loop is:** edge-triggered (fires on change, not a timer storm),
   bounded (hard timeout + size cap), single-instance (flock), non-destructive
   on the live tree (never `git reset --hard`, trash-not-delete), loud-once
   (alert on the edge, not every tick).
3. **One source of truth per fact.** No shadow copies that drift. One config,
   one writer per datastore.
4. **No fabrication.** Memory records what happened. No backfill, no synthetic
   events with production side-effects, no model-generated history written
   backward.
5. **Control plane and data plane are separate.** Structured low-rate control
   over JSON-RPC; high-rate numeric over the binary WIN plane. Never put ticks or
   audio frames through JSON.
6. **Message-passing, not state-capture.** No subsystem holds another's
   internals. They communicate over the bus. This is the rule that kills the
   IPC-blocking flaps at the design level.
7. **Total isolation from Ace.** No `~/.ace/` path, no `com.ace.*` job, no
   `ace.db`. Utah is its own world.

---

## 2. Process topology (what runs, and why each is its own process)

Ace's mistake was a monolith doing everything plus 51 ad-hoc launchd jobs. Utah
splits along **failure-isolation boundaries** — a crash or a slow call in one
must not stall another.

```
launchd (com.utah.*)
│
├── com.utah.daemon         ← the resident brain. asyncio. owns state, memory,
│                             agents, the control socket. NOTHING blocking runs
│                             inline here.
├── com.utah.win            ← the data-plane relay (Rust). owns the WIN socket,
│                             fans numeric windows (feed/voice/telemetry) to
│                             subscribers. separate process = a tick storm can
│                             never stall control RPCs.
├── com.utah.web            ← FastAPI frontend host (thin client over the socket)
├── com.utah.workers        ← the bounded worker pool that actually RUNS agents
│                             (so a 31s mail call runs HERE, off the daemon loop)
├── com.utah.scheduler      ← optional: edge/cron trigger source (or in-daemon)
└── com.utah.feed           ← market/data ingest producer → WIN (only if trading
                              returns; off by default)
```

Rule: **the daemon never does blocking I/O and never runs an agent inline.** It
dispatches a job to `workers` and awaits a result with a timeout. That single
decision eliminates the entire class of "IPC down" failures Ace had.

## 3. PIDs, signals, sockets, paths

- **PID file** `~/.utah/daemon.pid`: atomic write (tempfile + `os.replace`),
  single-writer lock, never stomp a live PID, release only if still owner.
  (Ace's pattern here was correct — port it.)
- **Signals:** `SIGTERM/SIGINT` → graceful drain with a bounded grace, then a
  *hard verified exit* (Ace zombied because it trusted "stop() returned" ≠
  "process exited" — Utah confirms the process is gone). `SIGHUP` → in-place
  reload (re-read config, reload agent registry, preserve socket + in-flight).
- **Sockets (two, both Unix-domain, mode 0600, unreachable from network):**
  - `~/.utah/utah.sock` — **control plane**, newline-delimited JSON-RPC 2.0.
  - `~/.utah/win.sock` — **data plane**, length-prefixed binary frames (§5).
- **The three roots (and nothing else named Utah on disk):**
  - `~/Desktop/ProjectUtah/` — code.
  - `~/.utah/` — runtime: `config.yaml`, `state.db`, `analytics.duckdb`,
    `utah.sock`, `win.sock`, `logs/`, `models/`, `vault/`.
  - (no fourth root — vault lives *under* `~/.utah/` so it can never drift from
    a second location the way Ace's vault did.)

## 4. The modular spine (how the god-file is broken up)

Ace's `daemon.py` (8,783 lines) becomes a thin orchestrator plus focused
modules. Concretely:

```
utah/
  core/
    daemon.py        ← ~200 lines: boot sequence + run_forever() only
    boot.py          ← explicit startup DAG: db→bus→scheduler→registry→ipc→listen
    loop.py          ← the event loop shell; persistence ticks spun off as tasks
    bus.py           ← async event bus (audit to state.db); the spine of #6
    scheduler.py     ← edge/cron triggers; module-level job fns for serialization
    signals.py       ← PID, signal handlers, verified-exit
    config.py        ← one typed config loader (pydantic), Keychain for secrets
  ipc/
    server.py        ← control socket bind + JSON-RPC envelope
    router.py        ← a DISPATCH TABLE (method → handler), not 60 inline ifs
    handlers/        ← one file per domain: agents, memory, voice, verify, …
  win/               ← data-plane client/codec (Rust core via PyO3)
  dispatch/
    workers.py       ← bounded pool; runs agents OFF the daemon loop; per-job
                       timeout via asyncio.wait_for + run_in_executor
  llm/               ← router/tiers, local MLX clients (ported, see 02)
  voice/             ← wake→STT→route→TTS state machine (ported, see 02)
  memory/            ← grounded store + guards (ported+cleaned, see 02)
  agents/            ← hot-loaded plugins: def run(ctx)->Result, KEYWORDS/NAME
```

**Frozen contracts (extend, never mutate)** — Ace's Contract-1/2/3 model was
sound; Utah keeps it:
- **Agent base** (`run/health/pause/resume/escalate/emit_event`). Subclasses
  override `run()` only.
- **MCP client protocol** (read-only vs read-write types; pool refuses write
  tools to read-only agents).
- **IPC schema** (the JSON-RPC method + event catalogue). One registry, the
  frontend reads it; add a method → update the one registry.

## 5. The WIN data plane (how Utah is actually fast)

**The problem WIN solves.** JSON-RPC is perfect for control (low rate,
structured, debuggable). It is fatal for high-frequency numeric streams — market
ticks (10–100/s), audio frames (16 kHz), telemetry bursts: every JSON message
allocates, every float does a string round-trip (slow + lossy), and parsing on
the async loop blocks control RPCs. That's the same flap pattern as §2, but for
data. Ace built a binary plane ("WIN") but left it dormant on a WS; Utah makes it
first-class.

**What WIN is.** A separate Unix-domain socket (`~/.utah/win.sock`) carrying
**length-prefixed binary frames**, owned by the `com.utah.win` process:

```
frame = [u32 len][u16 type][u16 flags][ body ]
        type  = TICK | AUDIO | TELEM | SIGNAL | …
        body  = packed little-endian numeric payload
```

- **Windowed = batched.** The producer packs N samples into ONE frame (a
  "window") instead of one-message-per-sample → amortizes syscall + framing
  10–100×.
- **Zero-copy on read.** For fixed schemas (tick = `ts,o,h,l,c,v`), the body is a
  packed C-struct array; the reader does `numpy.frombuffer(view)` — no parse, no
  allocation, straight into the math. For evolving structured payloads, use
  **FlatBuffers** (zero-copy reads) rather than protobuf (compact but must
  decode); reserve protobuf for control-ish messages where ergonomics beat ns.
- **Own socket = no contention.** A tick storm can never stall a control ping,
  because they're different sockets on different processes.
- **Backpressure = drop-oldest ring.** A late tick is worthless; the ring buffer
  drops stale frames rather than queueing latency.

**Why this makes everything fast:** removes JSON from the hot path, batches
syscalls, zero-copies into numpy/Rust, and physically isolates bursty data from
control. The market feed → WIN → engines + dashboard; voice audio frames → WIN;
engine/agent telemetry → WIN → live charts. **Control actions stay on JSON-RPC.**

**Transport off-machine.** To reach the phone/dashboard over the network, the
same binary payload rides a binary WebSocket frame (Tailscale-served) — identical
bytes, WS framing. No JSON re-encode.

## 6. Languages (what to write each layer in, and the binary differences)

A senior call: **Python is the brain and glue; compiled code owns the hot paths;
the frontend is web.**

- **Python (CPython)** — orchestration, agents, LLM routing, MLX bindings. Best
  ecosystem + fastest iteration. Weakness: the GIL + a single async loop is
  *exactly* what made sync I/O catastrophic. Mitigation: never block the loop
  (§2), push CPU/hot-I/O work out.
- **Rust** — the WIN codec, the feed ingest/relay, vector math hot paths.
  Compiled to a native `.so`, imported from Python via **PyO3/maturin**. No GIL,
  memory-safe, ~C speed. This is where "make everything fast" actually lives.
- **Go** — a fine alternative for the localhost egress/network broker (great
  concurrency, simpler than Rust) — but GC pauses make it wrong for numeric hot
  loops. Use for I/O-bound services only.
- **C/C++** — already vendored as `whisper.cpp` (STT) and `llama.cpp`/MLX-Metal
  (inference). Keep as binaries; don't reinvent.
- **Swift** — only if we ever want a *native* macOS HQ. We are choosing **web**
  for the frontend (portable, phone-ready over Tailscale, no contract codegen
  coupling), so Swift is optional and secondary. Ace's mistake was coupling the
  IPC contract to Swift codegen and maintaining two frontends.

**Binary-format differences (decision cheat-sheet):**
- **Fixed C-struct / packed bytes** — fastest, zero-copy, but rigid schema. Use
  for ticks/audio (the hot path).
- **FlatBuffers / Cap'n Proto** — zero-copy reads, evolvable schema. Use for
  structured telemetry and any WIN message that isn't a flat numeric array.
- **Protobuf** — compact, mature, but requires a decode step + allocation. Use
  only where ergonomics matter more than nanoseconds.
- **Arrow IPC** — columnar; ideal if a window is a *table* of many series headed
  to DuckDB/analytics. Optional, for the analytics bridge.
- **JSON** — control plane only. Human-debuggable, never on the hot path.

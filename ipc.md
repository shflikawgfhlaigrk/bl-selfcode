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

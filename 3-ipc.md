# 3 — IPC audit (incl. unix sockets & socket types)

> How the processes talk. Audited from the dirt (read `ipc.py`, empirically tested
> on this Mac) + web SOTA on socket types.

## 1. What we had
- **Control plane (good):** `acesd/core/ipc.py` — newline-delimited **JSON-RPC** over
  unix socket `~/.ace/ace.sock` (0600), **SO_PEERCRED/LOCAL_PEERCRED** peer-auth
  (xucred — empirically WORKS here, uid 501), fd-limit raised to 10,240.
  Contract-3 = 72 typed models.
- **In-process EventBus** (`bus.py`) — async pub/sub, **daemon-process-only**.
- **WIN data plane** (`acesd/win/`) — coded for `cbor2` (**not installed → inert →
  silent JSON fallback**); has an IDL/envelope/negotiate skeleton.
- **WebSocket** :9101 (+ a duplicate dashboard ws_server).
- **~20 TCP loopback ports** (engines/apex/lora/gbrain/hq).
- **MCP** = stdio (official `mcp` SDK).
- **File-as-channel** (~10 modules poll JSON/JSONL, e.g. `dashboard_chat_history.json`
  @0.5s); **SQLite** shared = a de-facto bus.
- Sockets: 1 control (`SOCK_STREAM`, `readline`, **no `limit=` → default 64 KiB**).

## 2. Why we did it
JSON-RPC over a unix socket is simple, debuggable, and the right "control" choice;
peer-cred + 0600 is genuinely good security. The in-proc bus was the easy event
mechanism. TCP ports per engine made each independently reachable. Files/SQLite
were the quickest way to "share state" between processes without designing a bus.

## 3. What we didn't think about
- **Six mechanisms, no single fabric** (socket + in-proc bus + 20 TCP + files +
  SQLite + WS).
- **The binary plane is dead** — cbor2 missing → numeric never got the fast path.
- **The bus can't cross processes** → engines/UI/loops **poll** HTTP/files →
  staleness ("backend produces, surface doesn't reflect") + load.
- **64 KiB control cap** — `start_unix_server` set no `limit=`; large control
  messages overrun `readline` (the 16 MB fix was only on the L3 subprocess).
- **File-as-channel** is racy (the self-coding `.tmp` bug) and is *why* chat was
  "if it isn't logged I can't see it."
- **TCP loopback sprawl** violates "unix socket not localhost"; JSON loses NaN/Inf.
- **macOS reality:** abstract-namespace sockets are Linux-only; SEQPACKET is
  non-portable → must use `SOCK_STREAM` + explicit framing.

## 4. What we're gonna change
- **One fabric: two unix sockets + one cross-process bus.**
  - **Control:** keep JSON-RPC + peer-cred + 0600, but **4-byte length-prefix
    framing + `readexactly`** (kills the 64 KiB cap).
  - **WIN data:** binary on **flatbuffers** (zero-copy) for ticks/audio/telemetry;
    **fail-loud** if codec missing; for the hottest path, **POSIX shared memory
    (`shm_open`+mmap) ring buffer + the socket as doorbell + SCM_RIGHTS fd-pass**
    (web: shm ~20× faster than queues, 127 ns vs 2.7 µs).
  - **Cross-process event broker** — promote the bus so every process subscribes
    over the socket (poll → push).
- **Kill** the ~20 TCP ports, file-as-channel, SQLite-as-bus, the duplicate WS.
- **Reject** SEQPACKET (non-portable), DGRAM (lossy), TCP loopback.

## 5. How it helps
| | Now | Utah |
|---|---|---|
| IPC mechanisms | 6 | 3 (control + data + bus) |
| binary plane | inert (cbor2 missing) | flatbuffers/shm, live |
| TCP ports | ~20 | ~1 |
| cross-proc events | poll | push (one bus) |
| control msg cap | 64 KiB (overruns) | length-prefixed, unbounded |
| numeric path | JSON (lossy/slow) | zero-copy binary (ns-scale) |

Sources: socket(2) https://www.man7.org/linux/man-pages/man2/socket.2.html · unix(7) https://man7.org/linux/man-pages/man7/unix.7.html · socket domains/types https://oswalt.dev/2025/07/linux-sockets-domains-and-types/ · IPC perf (shm vs queue) https://howtech.substack.com/p/ipc-mechanisms-shared-memory-vs-message

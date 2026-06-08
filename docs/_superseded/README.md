# Project Utah

A local-first, voice-first personal AI operating system — **rebuilt from the
ground up** on a verified foundation, carrying forward only what Ace *proved*
and discarding everything that rotted.

Utah is the clean-room successor to AceOS. It is **entirely separate from Ace**:
its own repository, runtime root, launchd jobs, databases, sockets, and vault.
Ace keeps running, untouched, until Utah is proven and we cut over deliberately.

## The hard isolation rule (non-negotiable)

| Concern | Ace (legacy, do not touch) | Utah (this project) |
|---|---|---|
| Code repo | `~/Desktop/AceOS/` | `~/Desktop/ProjectUtah/` |
| Runtime root | `~/.ace/` | `~/.utah/` |
| launchd prefix | `com.ace.*` | `com.utah.*` |
| Control socket | `~/.ace/ace.sock` | `~/.utah/utah.sock` |
| Data-plane socket | (none) | `~/.utah/win.sock` |
| Databases | `~/.ace/ace.db` | `~/.utah/state.db`, `~/.utah/analytics.duckdb`, Postgres `utah` |
| Vault | `~/Documents/Ace Vault/` | `~/.utah/vault/` |
| Models | `~/.ace/models/` | `~/.utah/models/` (one-time copy, then independent) |

Utah code **never reads or writes any `~/.ace/` path, never calls a `com.ace.*`
job, never opens `ace.db`.** The only permitted contact with Ace is a one-time
*copy* of immutable model weights and an explicit, audited *port* of a proven
source module into Utah's own tree. After cutover, Ace is archived and its
launchd jobs are booted out.

## What lives here

- `docs/00-ROOT-CAUSE.md` — the honest post-mortem: the single root cause behind
  every Ace failure, why it wasn't caught sooner, and what the wasted effort was.
- `docs/01-FOUNDATION.md` — the binding doctrine + the spine architecture
  (daemon, processes, PIDs, paths, signals, control plane vs data plane).
- `docs/02-DATA-AND-RUNTIME.md` — storage tiers (SQLite / Postgres / DuckDB),
  WAL, RAG, gbrain consolidation, MLX, the WIN binary data plane, config, langs.
- `docs/03-MIGRATION-AND-PLAN.md` — keep / migrate / change / discard manifest,
  backend↔frontend wiring, processes to configure, and the phased roadmap with
  verify gates.

## The one rule that orders everything

**Harden the base, prove it with a live probe, *then* compound.** No layer lands
on an unverified foundation. Every loop is edge-triggered, bounded,
single-instance, non-destructive, and loud-once. One source of truth per fact.
"Fixed" means a live probe in the same session — never a log or a summary.

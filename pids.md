# PIDs & process lifecycle (from the dirt + web)

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

# 4 — PIDs & process-lifecycle audit

> Daemon identity, singleton locking, signals, shutdown/restart. Dirt + web SOTA.
> Live-verified: `~/.ace/daemon.pid` = 63952 == running `com.ace.daemon`.

## 1. What we had
- **PID file** `~/.ace/daemon.pid` = single-writer lock + SIGHUP target
  (`daemon.py:1057`); atomic write (tempfile + `os.replace`); cold-start conflict →
  **never stomp a live PID** (`sys.exit(1)`).
- **Signals** (`daemon.py:2621`): `SIGTERM/SIGINT` → graceful `stop()`; `SIGHUP` →
  in-place reload (preserve socket + in-flight). macOS: `launchctl kill SIGHUP`.
- **launchd** KeepAlive respawns on clean exit; restart = `launchctl kickstart -k`.
- **fd headroom** raised to 10,240 before bind.

## 2. Why we did it
A pidfile + SIGHUP reload is the classic daemon pattern; atomic write + never-stomp
were deliberately careful. SIGTERM-drain to finish in-flight work cleanly. launchd
KeepAlive so it "always comes back." All reasonable.

## 3. What we didn't think about
- **Zombie on shutdown:** *"`stop()` returns fast ≠ the process exited."* Bounded
  waits that abandoned stuck tasks left a half-dead daemon → launchd saw it "alive"
  → **no respawn → daemon DOWN.**
- **SIGTERM restart trap:** `kill -TERM` is *trapped* (→ drain) and launchd won't
  respawn a hand-killed managed job → DOWN. Correct restart is `kickstart -k`.
- **Self-inflicted IPC drops:** over-reloading / `kickstart -k` = SIGKILL → ~60 s
  outage.
- **"PID exists" ≠ "healthy":** liveness was inferred from PID/launchd state, not a
  real probe → a wedged-but-alive daemon read as up.
- **Plain pidfile is stale-prone** (a crash leaves a stale number; deleting it races).

## 4. What we're gonna change
- **`flock` advisory lock** as the singleton (kernel-held, **auto-releases on
  crash**) + keep a pidfile for *introspection only*; **`kill -0`** stale check;
  **never delete the lockfile**.
- **Verified hard exit:** SIGTERM → bounded graceful drain → confirm the process is
  actually gone (`os._exit`); the **supervisor** force-kills + respawns a wedged
  daemon and **reaps children** (no zombies).
- **Restart discipline:** restarts go through the supervisor (SIGKILL+respawn), not
  a trapped SIGTERM; **SIGHUP only for config**; reload is rare + edge-triggered;
  every restart followed by a readiness probe.
- **Liveness = a real probe** (socket bound + `ping` OK + loop responsive), never
  pid-presence.

## 5. How it helps
- Eliminates the **zombie-daemon outage** (the worst lifecycle failure) and the
  **SIGTERM-trap** down-state; crash → clean auto-recovery via flock + supervisor.
- No false "up" — health gates on a probe, so a wedged daemon is caught and
  restarted instead of silently dead.
- No 60 s self-inflicted outages (reload rarely, restart deliberately).

Sources: trbs/pid (flock + stale detect) https://github.com/trbs/pid · "Nobody does pidfiles right" https://yakking.branchable.com/posts/procrun-2-pidfiles/ · "Never delete your PID file" https://www.guido-flohr.net/never-delete-your-pid-file/ · graceful shutdown + reaping https://oneuptime.com/blog/post/2026-01-16-docker-graceful-shutdown-signals/view

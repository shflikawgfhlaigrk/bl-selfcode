#!/bin/bash
# utah_pg.sh — keep Utah's Postgres cluster UP.
#
# Idempotently ensure the Utah cluster (~/.utah/pgdata) is accepting on :5433
# via the /tmp socket. Self-heals after a crash, OOM, or a stray `pg_ctl stop`
# (the 2026-06-07 outage: a smart-shutdown left PG dead and nothing restarted
# it -> 2689 connection errors, all /api -> {}). Run by launchd
# com.utah.postgres (RunAtLoad + every 60s). No-op when already accepting.
# Exit 0 when healthy/healed/gated; exit 1 only when a needed start FAILED
# (the honest signal — launchd's StartInterval retries on the next tick).
#
# Every knob is env-overridable (UTAH_PG*) so tests drive the script against
# fake binaries and a sandbox HOME — never the live cluster.
set -euo pipefail

# launchd runs us with a bare environment (no locale). Postgres 17 on macOS
# aborts startup with "postmaster became multithreaded during startup" unless a
# valid locale is set — so pin one. This is THE reason a launchd-driven restart
# failed on 2026-06-07 while a manual `pg_ctl start` (inheriting the shell's
# LANG) succeeded.
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

PGBIN="${UTAH_PGBIN:-/opt/homebrew/opt/postgresql@17/bin}"
PGDATA="${UTAH_PGDATA:-$HOME/.utah/pgdata}"
PGLOG="${UTAH_PG_SERVER_LOG:-$HOME/.utah/logs/pg.log}"
LOG="${UTAH_PG_GUARD_LOG:-$HOME/.utah/logs/utah_pg.log}"
PORT="${UTAH_PG_PORT:-5433}"
SOCKDIR="${UTAH_PG_SOCKDIR:-/tmp}"
LOCKDIR="${UTAH_PG_LOCKDIR:-$HOME/.utah/run/utah_pg.lock}"

mkdir -p "$(dirname "$LOG")" "$(dirname "$PGLOG")"

note() { echo "$(date '+%F %T') $*" >> "$LOG"; }
# Under `set -e` any unguarded failure exits; the trap turns that into a logged
# line instead of a silent death, so the guard's own breakage is visible in $LOG.
trap 'note "ERR line $LINENO: $BASH_COMMAND (rc=$?)"' ERR

if [ ! -x "$PGBIN/pg_ctl" ] || [ ! -x "$PGBIN/pg_isready" ]; then
  note "gated: postgres@17 binaries missing under $PGBIN"
  exit 0
fi

# Already accepting connections? -> nothing to do. pg_isready's own connect
# timeout (-t) bounds the probe; a wedged postmaster can't hang the guard.
if "$PGBIN/pg_isready" -h "$SOCKDIR" -p "$PORT" -t 5 -q 2>/dev/null; then
  exit 0
fi

# Single-instance lock: RunAtLoad + StartInterval can overlap a slow start and
# two pg_ctl starts on one PGDATA fight each other. mkdir (no -p) is the atomic
# primitive; a lock whose recorded holder is dead is stale and gets broken (a
# crash mid-run must not wedge every future tick).
mkdir -p "$(dirname "$LOCKDIR")"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  holder="$(head -1 "$LOCKDIR/pid" 2>/dev/null || true)"
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    note "another guard run (pid $holder) is mid-start — skipping"
    exit 0
  fi
  note "breaking stale lock (dead holder ${holder:-unknown})"
  rm -rf "$LOCKDIR"
  mkdir "$LOCKDIR"
fi
echo "$$" > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

# Not accepting. Clear a stale pidfile only if its postmaster is truly gone,
# so pg_ctl can do a clean (re)start. A LIVE pid means the postmaster exists but
# isn't accepting (still booting / recovering) — leave it alone entirely.
PIDFILE="$PGDATA/postmaster.pid"
if [ -f "$PIDFILE" ]; then
  pid="$(head -1 "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    note "removed stale postmaster.pid (dead pid $pid)"
  fi
fi

# Bounded start: -w -t 30 makes pg_ctl itself give up (rc!=0) if the postmaster
# doesn't reach accepting state in 30s — the guard can never hang here.
if "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGLOG" -o "-p $PORT -k $SOCKDIR" -w -t 30 start >/dev/null 2>&1; then
  note "started Utah PG on :$PORT (was down)"
  exit 0
fi
note "FAILED to start Utah PG on :$PORT — see $PGLOG"
exit 1

#!/bin/bash
# utah_pg.sh — keep Utah's Postgres cluster UP.
#
# Idempotently ensure the Utah cluster (~/.utah/pgdata) is accepting on :5433
# via the /tmp socket. Self-heals after a crash, OOM, or a stray `pg_ctl stop`
# (the 2026-06-07 outage: a smart-shutdown left PG dead and nothing restarted
# it -> 2689 connection errors, all /api -> {}). Run by launchd
# com.utah.postgres (RunAtLoad + every 60s). No-op when already accepting.
set -u
# launchd runs us with a bare environment (no locale). Postgres 17 on macOS
# aborts startup with "postmaster became multithreaded during startup" unless a
# valid locale is set — so pin one. This is THE reason a launchd-driven restart
# failed on 2026-06-07 while a manual `pg_ctl start` (inheriting the shell's
# LANG) succeeded.
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
PGBIN=/opt/homebrew/opt/postgresql@17/bin
PGDATA="$HOME/.utah/pgdata"
PGLOG="$HOME/.utah/logs/pg.log"
LOG="$HOME/.utah/logs/utah_pg.log"
PORT=5433
SOCKDIR=/tmp

[ -x "$PGBIN/pg_ctl" ] || { echo "$(date '+%F %T') postgres@17 binary missing" >> "$LOG"; exit 0; }

# Already accepting connections? -> nothing to do.
if "$PGBIN/pg_isready" -h "$SOCKDIR" -p "$PORT" -q 2>/dev/null; then
  exit 0
fi

# Not accepting. Clear a stale pidfile only if its postmaster is truly gone,
# so pg_ctl can do a clean (re)start.
PIDFILE="$PGDATA/postmaster.pid"
if [ -f "$PIDFILE" ]; then
  pid="$(head -1 "$PIDFILE" 2>/dev/null)"
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "$(date '+%F %T') removed stale postmaster.pid (dead pid $pid)" >> "$LOG"
  fi
fi

if "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGLOG" -o "-p $PORT -k $SOCKDIR" -w -t 30 start >/dev/null 2>&1; then
  echo "$(date '+%F %T') started Utah PG on :$PORT (was down)" >> "$LOG"
else
  echo "$(date '+%F %T') FAILED to start Utah PG on :$PORT — see $PGLOG" >> "$LOG"
fi

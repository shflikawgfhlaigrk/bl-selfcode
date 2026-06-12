#!/bin/bash
# utah_tailserve.sh — keep the tailnet HOOKED to Utah.
#
# Idempotently ensure the :8765 tailnet serve proxies the Utah daemon (:8766).
# Self-heals any clobber — e.g. com.ace.phone_proxy re-asserting old-Ace's
# :8765 -> :8765. Run by launchd com.utah.tailserve (RunAtLoad + every 2 min).
# No-op when already correct, so it's cheap to run often.
#
# Exit 0 when hooked/healed/gated (no Tailscale installed); exit 1 only when the
# re-assert FAILED — including a wedged tailscaled. Every Tailscale CLI call is
# BOUNDED by a kill-watchdog: launchd ticks every 2 minutes, so an unbounded
# hang would silently pile up stuck copies forever.
#
# Knobs are env-overridable (UTAH_TS*/UTAH_TAILSERVE_*) so tests drive the
# script against a fake CLI — never the live tailnet.
set -euo pipefail

TS="${UTAH_TS_BIN:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"
LOG="${UTAH_TAILSERVE_LOG:-$HOME/.utah/logs/tailserve.log}"
WANT_TARGET="${UTAH_TAILSERVE_TARGET:-127.0.0.1:8766}"
LISTEN_PORT="${UTAH_TAILSERVE_PORT:-8765}"
TS_TIMEOUT="${UTAH_TS_TIMEOUT:-15}"

[ -x "$TS" ] || exit 0   # gated: machine without Tailscale — nothing to keep hooked

mkdir -p "$(dirname "$LOG")"
note() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
trap 'note "ERR line $LINENO: $BASH_COMMAND (rc=$?)"' ERR

# bounded SECS CMD... — run CMD with a kill-watchdog (macOS bash has no
# `timeout`). Returns CMD's rc, or 137 when the watchdog had to SIGKILL it.
# The watchdog's stdout/stderr go to /dev/null so a command substitution around
# `bounded` doesn't sit waiting on the watchdog's open pipe.
bounded() {
  local secs="$1"; shift
  "$@" &
  local pid=$!
  ( sleep "$secs"; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  local watcher=$!
  local rc=0
  wait "$pid" 2>/dev/null || rc=$?
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true
  return "$rc"
}

status="$(bounded "$TS_TIMEOUT" "$TS" serve status 2>/dev/null || true)"
# -F: the target is a literal host:port — under -E/grep default its dots would
# match ANY character and a lookalike proxy could read as "already hooked".
if printf '%s' "$status" | grep -qF "proxy http://$WANT_TARGET"; then
  exit 0   # already hooked to Utah — nothing to do
fi

# Re-assert Utah's serve (Tailscale must be up; if not, the next tick retries —
# but the failure is LOGGED and the exit code says so, never a silent shrug).
if bounded "$TS_TIMEOUT" "$TS" serve --bg --http="$LISTEN_PORT" "http://${WANT_TARGET}" >/dev/null 2>&1; then
  was="$(printf '%s' "$status" | tr '\n' ' ' | grep -oE 'proxy http://[0-9.:]+' | head -1 || true)"
  note "re-asserted tailserve :$LISTEN_PORT -> ${WANT_TARGET} (was: ${was:-none})"
  exit 0
fi
note "FAILED to re-assert tailserve :$LISTEN_PORT -> ${WANT_TARGET} (tailscaled down or wedged; will retry next tick)"
exit 1

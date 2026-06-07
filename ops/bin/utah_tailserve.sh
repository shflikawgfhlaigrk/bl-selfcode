#!/bin/bash
# utah_tailserve.sh — keep the tailnet HOOKED to Utah.
#
# Idempotently ensure the :8765 tailnet serve proxies the Utah daemon (:8766).
# Self-heals any clobber — e.g. com.ace.phone_proxy re-asserting old-Ace's
# :8765 -> :8765. Run by launchd com.utah.tailserve (RunAtLoad + every 2 min).
# No-op when already correct, so it's cheap to run often.
set -u
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
LOG="$HOME/.utah/logs/tailserve.log"
WANT_TARGET="127.0.0.1:8766"

[ -x "$TS" ] || exit 0

status="$("$TS" serve status 2>/dev/null || true)"
if printf '%s' "$status" | grep -q "$WANT_TARGET"; then
  exit 0   # already hooked to Utah — nothing to do
fi

# Re-assert Utah's serve (Tailscale must be up; if not, the next tick retries).
if "$TS" serve --bg --http=8765 "http://${WANT_TARGET}" >/dev/null 2>&1; then
  was="$(printf '%s' "$status" | tr '\n' ' ' | grep -oE 'proxy http://[0-9.:]+' | head -1)"
  echo "$(date '+%Y-%m-%d %H:%M:%S') re-asserted tailserve :8765 -> ${WANT_TARGET} (was: ${was:-none})" >> "$LOG"
fi

#!/usr/bin/env bash
# sync-launchd.sh — copy ops/launchd/*.plist → ~/Library/LaunchAgents and reload.
#
# Lighter than cleanup-boundaries.sh: no Ace archive, Sovereign repoint, or stack
# restart. Use this after editing repo plists to clear drift.scan() false alarms.
#
# By default only reloads StartInterval cron jobs. KeepAlive roots (supervisor,
# postgres, verify, tailserve) are copied but NOT bootstrapped unless asked — avoids
# killing the live daemon mid-conversation.
#
# Usage:
#   ./ops/sync-launchd.sh                         # dry-run: list semantic drifts
#   ./ops/sync-launchd.sh --apply                 # copy + reload interval jobs
#   ./ops/sync-launchd.sh --apply --reload-critical  # also bootstrap KeepAlive jobs
set -euo pipefail

APPLY=0
RELOAD_CRITICAL=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --reload-critical) RELOAD_CRITICAL=1 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $arg (try --help)" >&2; exit 2 ;;
  esac
done

UTAH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LA="$HOME/Library/LaunchAgents"
GUI="gui/$(id -u)"
PY="${UTAH_VENV:-$HOME/.utah/venv}/bin/python"

log()  { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*"; }
err()  { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }

# KeepAlive / long-running jobs — copy on --apply but skip bootstrap unless --reload-critical.
CRITICAL=(
  com.utah.supervisor
  com.utah.postgres
  com.utah.verify
  com.utah.tailserve
)

is_critical() {
  local label="$1" c
  for c in "${CRITICAL[@]}"; do
    [[ "$label" == "$c" ]] && return 0
  done
  return 1
}

log "=== Utah launchd sync (apply=$APPLY reload_critical=$RELOAD_CRITICAL) ==="
log "Utah root: $UTAH_ROOT"

DRIFTS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && DRIFTS+=("$line")
done < <("$PY" - <<PY
from utah.drift import plist_drift
for f in plist_drift():
    print(f)
PY
)

if ((${#DRIFTS[@]} == 0)); then
  log "no semantic plist drift"
  exit 0
fi

log "semantic drift (${#DRIFTS[@]}):"
for d in "${DRIFTS[@]}"; do
  log "  $d"
  name="${d%%:*}"
  repo_plist="$UTAH_ROOT/ops/launchd/$name"
  inst_plist="$LA/$name"
  if [[ -f "$repo_plist" && -f "$inst_plist" && "$d" == *differs* ]]; then
    diff -u "$inst_plist" "$repo_plist" 2>/dev/null | sed 's/^/    /' || true
  fi
done

if [[ "$APPLY" -ne 1 ]]; then
  log "DRY-RUN — re-run with --apply to copy and reload interval jobs"
  exit 0
fi

synced=0
reloaded=0
skipped_critical=0
for src in "$UTAH_ROOT/ops/launchd"/com.utah.*.plist; do
  [[ -f "$src" ]] || continue
  label="$(basename "$src" .plist)"
  dest="$LA/$(basename "$src")"
  if ! diff -q "$src" "$dest" >/dev/null 2>&1; then
    cp "$src" "$dest"
    synced=$((synced + 1))
    log "copied $label"
  fi
  if is_critical "$label"; then
    if [[ "$RELOAD_CRITICAL" -eq 1 ]]; then
      launchctl bootout "$GUI/$label" 2>/dev/null || true
      launchctl bootstrap "$GUI" "$dest" 2>/dev/null || launchctl load "$dest" 2>/dev/null || true
      reloaded=$((reloaded + 1))
      log "bootstrapped (critical) $label"
    else
      skipped_critical=$((skipped_critical + 1))
      log "skipped bootstrap for critical $label (use --reload-critical)"
    fi
  else
    launchctl bootout "$GUI/$label" 2>/dev/null || true
    launchctl bootstrap "$GUI" "$dest" 2>/dev/null || launchctl load "$dest" 2>/dev/null || true
    reloaded=$((reloaded + 1))
    log "bootstrapped $label"
  fi
done

log "copied=$synced bootstrapped=$reloaded critical_skipped=$skipped_critical"

AFTER=$("$PY" -c "from utah.drift import plist_drift; print(len(plist_drift()))")
log "semantic drift after sync: $AFTER"
log "=== Done ==="

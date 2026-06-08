#!/usr/bin/env bash
# cleanup-boundaries.sh — separate Utah (private Ace) from sellables + archive degraded Ace OS.
#
# What it does (--apply):
#   1. Archives com.ace.* LaunchAgents → ~/Library/LaunchAgents/_archived-ace/
#   2. Bootouts any loaded com.ace.* jobs
#   3. Stops com.sovereign.daemon (port collision with Utah tailnet / wrong localhost URL)
#   4. Moves Sovereign local dashboard to 8775/8776 (sellable template unchanged)
#   5. Bootstraps all com.utah.* plists from ops/launchd/
#   6. Restarts com.utah.supervisor + tailserve
#   7. Prints health check
#
# Usage:
#   ./ops/cleanup-boundaries.sh           # dry-run (default)
#   ./ops/cleanup-boundaries.sh --apply   # execute
set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

UTAH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LA="$HOME/Library/LaunchAgents"
ARCHIVE="$LA/_archived-ace"
GUI="gui/$(id -u)"
SOV_YAML="$HOME/Library/Application Support/Sovereign/sovereign.yaml"

log() { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*"; }
run() {
  if [[ "$APPLY" -eq 1 ]]; then
    log "RUN  $*"
    "$@"
  else
    log "DRY  $*"
  fi
}

log "=== Utah/Ace boundary cleanup (apply=$APPLY) ==="
log "Utah root: $UTAH_ROOT"

# --- 1. Archive Ace launchd plists ---
ace_plists=("$LA"/com.ace.*.plist "$LA"/com.ace.*.plist.*)
ace_count=0
for f in "${ace_plists[@]}"; do
  [[ -e "$f" ]] || continue
  ace_count=$((ace_count + 1))
  label="$(basename "$f" | sed 's/\.plist.*//')"
  if [[ "$APPLY" -eq 1 ]]; then
    launchctl bootout "$GUI/$label" 2>/dev/null || true
    mkdir -p "$ARCHIVE"
    mv "$f" "$ARCHIVE/"
    log "archived $label"
  else
    log "would archive $label"
  fi
done
log "Ace plists found: $ace_count → $ARCHIVE"

# --- 2. Stop Sovereign daemon (port 8765 collision) ---
if launchctl print "$GUI/com.sovereign.daemon" &>/dev/null; then
  run launchctl bootout "$GUI/com.sovereign.daemon"
else
  log "com.sovereign.daemon not loaded"
fi

# --- 3. Repoint Sovereign local install off Utah ports ---
if [[ -f "$SOV_YAML" ]]; then
  if grep -q 'port: 8765' "$SOV_YAML" 2>/dev/null; then
    if [[ "$APPLY" -eq 1 ]]; then
      cp "$SOV_YAML" "$SOV_YAML.bak-pre-utah-cleanup-$(date +%Y%m%d)"
      sed -i '' 's/port: 8765/port: 8775/' "$SOV_YAML"
      sed -i '' 's/ws_port: 8766/ws_port: 8776/' "$SOV_YAML"
      log "Sovereign ports → 8775/8776 in $SOV_YAML"
    else
      log "would move Sovereign dashboard 8765→8775, ws 8766→8776"
    fi
  else
    log "Sovereign yaml already off Utah ports"
  fi
fi

# --- 4. Bootstrap Utah launchd plists ---
for src in "$UTAH_ROOT/ops/launchd"/com.utah.*.plist; do
  [[ -f "$src" ]] || continue
  label="$(basename "$src" .plist)"
  dest="$LA/$(basename "$src")"
  if [[ "$APPLY" -eq 1 ]]; then
    cp "$src" "$dest"
    launchctl bootout "$GUI/$label" 2>/dev/null || true
    launchctl bootstrap "$GUI" "$dest" 2>/dev/null || launchctl load "$dest" 2>/dev/null || true
    log "bootstrapped $label"
  else
    log "would bootstrap $label"
  fi
done

# --- 5. Restart Utah stack ---
if [[ "$APPLY" -eq 1 ]]; then
  run launchctl kickstart -k "$GUI/com.utah.postgres" 2>/dev/null || true
  sleep 2
  run launchctl kickstart -k "$GUI/com.utah.supervisor"
  run launchctl kickstart "$GUI/com.utah.tailserve" 2>/dev/null || true
fi

# --- 6. Health ---
log "=== Health (always probed) ==="
if curl -sf --max-time 5 "http://127.0.0.1:8766/state" >/dev/null 2>&1; then
  python3 - <<'PY'
import json, urllib.request
st = json.load(urllib.request.urlopen("http://127.0.0.1:8766/state", timeout=5))
mem = json.load(urllib.request.urlopen("http://127.0.0.1:8766/memory", timeout=5))
print(f"  deck health: {st.get('health')}")
print(f"  voice: {(st.get('voice') or {}).get('status')}")
print(f"  ledger leads: {(st.get('ledger') or {}).get('leads')}")
print(f"  memory live: {mem.get('live')} / {mem.get('total')}")
PY
  log "Open Utah deck: http://127.0.0.1:8766/"
  if command -v tailscale &>/dev/null; then
    /Applications/Tailscale.app/Contents/MacOS/Tailscale serve status 2>/dev/null | head -3 || true
  fi
else
  log "WARN: Utah deck not responding on :8766"
  log "  After --apply: launchctl kickstart -k $GUI/com.utah.supervisor"
fi

log "=== Done ==="

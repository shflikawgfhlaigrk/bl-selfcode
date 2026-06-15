#!/usr/bin/env bash
# Restored Apex-Prime engine fleet — (re)generate + load the launchd services.
#
# The original AceOS trading engines (in ~/debt) run as their own services on the
# live WealthCharts Chrome feed (CDP :9223, the same feed utah/integrations/wc_feed
# drives), signal-only (AUTO_EXECUTE=false — never auto-trades). utah's engine_bridge
# (com.utah.engine-bridge) polls them and ingests fires into ~/.utah/cache/engine_fires.jsonl.
#
# This restores the engines Michael had before the Utah rewrite flattened them into one
# "research" stub (2026-06-14). Re-run after a reboot or to re-point the fleet.
#
# Usage:  ops/engine-fleet.sh           # (re)load all engines + the bridge
#         ops/engine-fleet.sh status    # show health
set -euo pipefail

UID_NUM="$(id -u)"
DEBT="$HOME/debt"
LOGS="$HOME/.utah/logs"; mkdir -p "$LOGS"
LA="$HOME/Library/LaunchAgents"

# key  dir  dashboard_port  tick_port
FLEET=(
  "bible	Perplexity_Apex_Signal_Bible_Engine	8400	8401"
  "apex	Perplexity_Apex_NoYahoo_Engine_V2	8100	8101"
  "research	Perplexity_Based_Trading_Engine_FIXED_V2	8300	8301"
  "barber	Ace_Barber_Engine	8200	8201"
  "ctx_alpha	Ace_Context_Alpha_Engine	8600	8601"
  "ctx_bravo	Ace_Context_Bravo_Engine	8700	8701"
)

status() {
  for row in "${FLEET[@]}"; do
    IFS=$'\t' read -r key dir dash tick <<<"$row"
    pid="$(launchctl list "com.utah.engine-$key" 2>/dev/null | sed -n 's/.*"PID" = \([0-9]*\).*/\1/p')"
    printf '%-10s :%s  pid=%s\n' "$key" "$dash" "${pid:-DOWN}"
  done
  pid="$(launchctl list com.utah.engine-bridge 2>/dev/null | sed -n 's/.*"PID" = \([0-9]*\).*/\1/p')"
  printf '%-10s        pid=%s\n' "bridge" "${pid:-DOWN}"
}

[ "${1:-}" = "status" ] && { status; exit 0; }

for row in "${FLEET[@]}"; do
  IFS=$'\t' read -r key dir dash tick <<<"$row"
  py="$DEBT/$dir/.venv/bin/python"
  [ -x "$py" ] || { echo "SKIP $key — no venv at $py (run: python3 -m venv && pip install -r engine/requirements.txt)"; continue; }
  plist="$LA/com.utah.engine-$key.plist"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.utah.engine-$key</string>
  <key>ProgramArguments</key><array>
    <string>$py</string><string>-m</string><string>engine.main</string>
  </array>
  <key>WorkingDirectory</key><string>$DEBT/$dir</string>
  <key>EnvironmentVariables</key><dict>
    <key>DATA_FEED</key><string>chrome</string>
    <key>DASHBOARD_YAHOO_SEED</key><string>false</string>
    <key>DISABLE_YAHOO</key><string>true</string>
    <key>CHROME_DEBUG_URL</key><string>http://127.0.0.1:9223/json</string>
    <key>CHROME_CDP_LEVEL</key><string>browser</string>
    <key>CHROME_PAGE_FILTER</key><string>wealth</string>
    <key>DASHBOARD_PORT</key><string>$dash</string>
    <key>TICK_PORT</key><string>$tick</string>
    <key>BROKER</key><string>null</string>
    <key>AUTO_EXECUTE</key><string>false</string>
    <key>ENGINE_KEY</key><string>$key</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/engine-$key.log</string>
  <key>StandardErrorPath</key><string>$LOGS/engine-$key.log</string>
</dict></plist>
EOF
  launchctl bootout "gui/$UID_NUM/com.utah.engine-$key" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$plist" && echo "loaded com.utah.engine-$key (:$dash)"
done

# the Utah-side bridge that ingests their fires
bridge="$LA/com.utah.engine-bridge.plist"
cat > "$bridge" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.utah.engine-bridge</string>
  <key>ProgramArguments</key><array>
    <string>$HOME/.utah/venv/bin/python</string><string>-m</string><string>utah.integrations.engine_bridge</string>
  </array>
  <key>WorkingDirectory</key><string>$HOME/ProjectUtah</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$HOME/ProjectUtah</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/engine-bridge.log</string>
  <key>StandardErrorPath</key><string>$LOGS/engine-bridge.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$UID_NUM/com.utah.engine-bridge" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$bridge" && echo "loaded com.utah.engine-bridge"
echo "--- fleet ---"; status

#!/bin/bash
# Set a Black Label app's icon from a logo PNG (square PNG works best).
# Usage: set-logo.sh "/path/to/Black Label Leads.app" ~/path/to/logo.png
set -e
APP="$1"; LOGO="$2"
if [ ! -d "$APP" ] || [ ! -f "$LOGO" ]; then
  echo "usage: set-logo.sh <App.app> <logo.png>"; exit 1
fi
WORK="$(mktemp -d)/icon.iconset"; mkdir -p "$WORK"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$LOGO" --out "$WORK/icon_${s}x${s}.png" >/dev/null
  d=$((s*2)); sips -z "$d" "$d" "$LOGO" --out "$WORK/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$WORK" -o "$APP/Contents/Resources/app.icns"
# nudge Finder to pick up the new icon
touch "$APP"; touch "$APP/Contents/Info.plist"
echo "✓ icon set for $(basename "$APP")"

#!/usr/bin/env bash
# Mass-export the Utah leads table to CSV (flattened contact fields).
# Usage: ops/export_leads.sh [output.csv]   (default: ~/Desktop/leads-export-YYYY-MM-DD.csv)
set -euo pipefail

OUT="${1:-$HOME/Desktop/leads-export-$(date +%F).csv}"
PSQL="$(ls /opt/homebrew/opt/postgresql@*/bin/psql 2>/dev/null | head -1)"
[ -n "$PSQL" ] || PSQL="psql"

"$PSQL" -h 127.0.0.1 -p 5433 -d utah -c "\copy (
  SELECT id, name, kind, region, source, status,
         contact->>'phone'   AS phone,
         contact->>'email'   AS email,
         contact->>'website' AS website,
         contact->>'address' AS address,
         ts
  FROM leads ORDER BY region, name
) TO '$OUT' WITH (FORMAT csv, HEADER true)"

echo "Exported $(($(wc -l < "$OUT") - 1)) leads -> $OUT"

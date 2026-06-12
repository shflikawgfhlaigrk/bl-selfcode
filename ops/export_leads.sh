#!/usr/bin/env bash
# Mass-export the Utah leads table to CSV (flattened contact fields).
# Usage: ops/export_leads.sh [output.csv]   (default: ~/Desktop/leads-export-YYYY-MM-DD.csv)
#
# Bounded + honest: PGCONNECT_TIMEOUT caps the connect, statement_timeout (via
# PGOPTIONS) caps the copy, and every failure lands on stderr with a non-zero
# exit — a dead/stalled cluster can never hang this script or lie about success.
set -euo pipefail

err()  { printf 'export_leads: %s\n' "$*" >&2; }
fail() { err "$*"; exit 1; }
trap 'err "FAILED at line $LINENO (exit $?)"' ERR

OUT="${1:-$HOME/Desktop/leads-export-$(date +%F).csv}"

DB_HOST="${UTAH_DB_HOST:-127.0.0.1}"
DB_PORT="${UTAH_DB_PORT:-5433}"
DB_NAME="${UTAH_DB_NAME:-utah}"

PSQL="$(ls /opt/homebrew/opt/postgresql@*/bin/psql 2>/dev/null | head -1 || true)"
[ -n "$PSQL" ] || PSQL="$(command -v psql || true)"
[ -n "$PSQL" ] || fail "psql not found (no /opt/homebrew/opt/postgresql@*/bin/psql and none on PATH)"

# Never hang: bounded connect + bounded statement (both env-overridable).
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-5}"
export PGOPTIONS="${PGOPTIONS:--c statement_timeout=300000}"

RESULT="$("$PSQL" -h "$DB_HOST" -p "$DB_PORT" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "\copy (
  SELECT id, name, kind, region, source, status,
         contact->>'phone'   AS phone,
         contact->>'email'   AS email,
         contact->>'website' AS website,
         contact->>'address' AS address,
         ts
  FROM leads ORDER BY region, name
) TO '$OUT' WITH (FORMAT csv, HEADER true)")" \
  || fail "psql export failed (is Utah Postgres at $DB_HOST:$DB_PORT/$DB_NAME up?)"

# Honest count: psql's authoritative COPY tag, NOT wc -l (CSV fields can hold
# embedded newlines, so raw line count overstates the row count).
COUNT="$(printf '%s\n' "$RESULT" | awk '/^COPY [0-9]+$/{n=$2} END{print n}')"
[ -n "$COUNT" ] || fail "could not parse row count from psql output: $RESULT"
[ -s "$OUT" ] || fail "export wrote no data to $OUT"
echo "Exported $COUNT leads -> $OUT"

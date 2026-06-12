#!/usr/bin/env bash
# Utah stack verifier — checks hardware/software claims from the substrate docs.
# Usage: bash ops/verify_stack.sh
set -uo pipefail

REPO="${UTAH_REPO:-$HOME/Desktop/ProjectUtah}"
DEV="$REPO/.venv/bin/python"
RT="${UTAH_PY:-$HOME/.utah/venv/bin/python}"
PG_ISREADY="${PG_ISREADY:-/opt/homebrew/opt/postgresql@17/bin/pg_isready}"
DSN="${UTAH_DSN:-host=/tmp port=5433 dbname=utah}"

hdr() { printf '\n\033[1m===== %s =====\033[0m\n' "$1"; }
yes() { printf '  \033[32mOK\033[0m  %s\n' "$1"; }
nob() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; }

mp() {
  local py=$1 mod=$2
  [ -x "$py" ] || { echo "NO_PY"; return; }
  "$py" -c "import $mod" >/dev/null 2>&1 && echo OK || echo "-"
}

pchk() {
  printf '  %-22s  dev: %-5s  runtime: %-5s\n' "$1" "$(mp "$DEV" "$1")" "$(mp "$RT" "$1")"
}

hdr "Two venvs (dev slim vs runtime daemons)"
if [ -x "$DEV" ]; then
  echo "  dev      : $("$DEV" --version 2>&1)  -> $DEV"
else
  nob "dev venv missing: $DEV"
fi
if [ -x "$RT" ]; then
  echo "  runtime  : $("$RT" --version 2>&1)  -> $RT"
else
  nob "runtime venv missing: $RT"
fi
echo "  launchd python (from com.utah plists):"
grep -hoE "/Users/[^ \"<]*venv/bin/python[0-9.]*" \
  "$HOME"/Library/LaunchAgents/com.utah.*.plist 2>/dev/null \
  | sort | uniq -c | sed 's/^/    /' || echo "    (no plists found)"

hdr "Python deps (core in both; voice/MLX runtime only; no torch)"
echo "  -- core --"
for m in msgspec psycopg pgvector fastembed; do pchk "$m"; done
echo "  -- voice / MLX (runtime only) --"
for m in moonshine_onnx mlx mlx_whisper mlx_lm openwakeword \
         sounddevice soundfile silero_vad onnxruntime; do
  pchk "$m"
done
echo "  -- OLAP tier --"
for m in duckdb pyarrow flatbuffers; do pchk "$m"; done
echo "  -- torch (expect absent) --"
for m in torch torchaudio sentence_transformers; do pchk "$m"; done

hdr "Postgres primary (:5433 + pgvector + schema)"
grep -n "DB_DSN" "$REPO/utah/config.py" | sed 's/^/  /'
if [ -x "$PG_ISREADY" ]; then
  "$PG_ISREADY" -h /tmp -p 5433 -q && yes "pg_isready :5433" || nob "postgres not accepting on /tmp:5433"
elif command -v pg_isready >/dev/null; then
  pg_isready -h /tmp -p 5433 -q && yes "pg_isready :5433" || nob "postgres not accepting on /tmp:5433"
else
  nob "pg_isready not found"
fi
if command -v psql >/dev/null; then
  echo "  server   : $(psql "$DSN" -tAc 'show server_version' 2>/dev/null || echo '?')"
  echo "  pgvector : $(psql "$DSN" -tAc "select extversion from pg_extension where extname='vector'" 2>/dev/null || echo '?')"
  echo "  tables   : $(psql "$DSN" -tAc "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null || echo '?')"
  echo "  row counts:"
  for t in memory leads probate outreach_ledger fires failures; do
    c=$(psql "$DSN" -tAc "select count(*) from $t" 2>/dev/null) || c="<missing>"
    printf '    %-20s %s\n' "$t" "$c"
  done
else
  nob "psql not in PATH"
fi

hdr "DuckDB OLAP tier"
if command -v duckdb >/dev/null; then
  yes "duckdb CLI: $(duckdb --version 2>&1 | head -1)"
else
  nob "duckdb CLI missing"
fi
if [ -f "$REPO/utah/store/__init__.py" ]; then
  sed -n '1,6p' "$REPO/utah/store/__init__.py" | sed 's/^/  /'
fi
echo "  olap wired in code:"
grep -rn "store\.olap\|from utah\.store import olap\|olap\.query" "$REPO/utah" \
  --include='*.py' 2>/dev/null | sed 's/^/    /' || echo "    (none — olap.py stranded)"
# Real round-trip, not just a grep: ATTACH the live primary READ-ONLY through
# utah.store.olap and count a real table. Bounded (connect_timeout on the DSN,
# 30s watchdog) so a stalled primary FAILs this line instead of hanging the sweep.
echo "  olap real round-trip (runtime venv, read-only ATTACH, bounded):"
if [ -x "$RT" ]; then
  if out=$(PYTHONPATH="$REPO" "$RT" -c "
from utah.store import olap
rows = olap.query('SELECT count(*) FROM pg.public.leads', dsn='$DSN', timeout_s=30)
print(rows[0][0])
" 2>&1); then
    yes "olap.query leads count: $out"
  else
    nob "olap round-trip failed: $(printf '%s\n' "$out" | tail -1)"
  fi
else
  nob "runtime venv missing — olap round-trip skipped"
fi

hdr "SQLite retired"
echo "  *.db under ~/.utah and repo (maxdepth 3):"
find "$HOME/.utah" "$REPO" -maxdepth 3 \
  \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) 2>/dev/null \
  | sed 's/^/    /' || echo "    (none)"
echo "  sqlite3.connect in utah/:"
grep -rln 'sqlite3\.connect' "$REPO/utah" 2>/dev/null | sed 's/^/    /' || echo "    (none)"

hdr "Models (~/.utah/models)"
du -sh "$HOME/.utah/models" 2>/dev/null | sed 's/^/  /' || nob "no models dir"
du -sh "$HOME/.utah/models"/* 2>/dev/null | sed 's/^/  /' | head -15

hdr "Homebrew binaries"
for b in whisper-cli ffmpeg ffprobe ollama; do
  p=$(command -v "$b" 2>/dev/null || true)
  [ -n "$p" ] && yes "$b -> $p" || nob "$b missing"
done
echo "  piper:"
for p in "$HOME/.utah/venv/bin/piper" "$REPO/.venv/bin/piper"; do
  [ -x "$p" ] && yes "$p" || nob "missing $p"
done

hdr "Ollama models"
if command -v ollama >/dev/null; then
  ollama list 2>/dev/null | sed 's/^/  /' || nob "ollama list failed"
else
  nob "ollama not installed"
fi

hdr "Daemons + sockets"
if launchctl list 2>/dev/null | grep -q com.utah.supervisor; then
  yes "com.utah.supervisor loaded"
else
  nob "com.utah.supervisor not loaded"
fi
echo "  com.utah jobs:"
launchctl list 2>/dev/null | grep com.utah | sed 's/^/    /' || echo "    (none)"
echo "  unix sockets:"
find "$HOME/.utah/run" -type s 2>/dev/null | sed 's/^/    /' || echo "    (none)"

hdr "Foundation probe"
if [ -x "$RT" ] && [ -d "$REPO/utah" ]; then
  PYTHONPATH="$REPO" "$RT" "$REPO/ops/foundation_check.py" 2>/dev/null | sed 's/^/  /'
  [ -f "$HOME/.utah/run/foundation.json" ] && cat "$HOME/.utah/run/foundation.json" | sed 's/^/  /'
fi

hdr "Hardware"
echo "  chip : $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo '?')"
echo "  arch : $(uname -m)   cores: $(sysctl -n hw.ncpu 2>/dev/null || echo '?')   ram: $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))GB"
echo "  macOS: $(sw_vers -productVersion 2>/dev/null || echo '?')"

printf '\n\033[1mDone.\033[0m Read each section against its claim.\n'

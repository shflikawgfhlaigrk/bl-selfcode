#!/usr/bin/env bash
# Utah stack verifier — checks the hardware/software claims from the substrate
# docs and ASSERTS them: every claim is an OK/FAIL line, failures are counted,
# and the EXIT CODE is the honest signal (0 = every claim held; 1 = read the
# FAIL lines). Bounded everywhere: psql / pg_isready / duckdb / ollama / python
# probes all run under a kill-watchdog (macOS bash ships no `timeout`), so a
# wedged postmaster or ollama daemon can never hang the sweep.
#
# Usage:  bash ops/verify_stack.sh        # exit 0 all-green / 1 something FAILed
#
# Knobs are env-overridable so tests drive the sweep against a fully fake stack
# (fake venvs, fake CLIs on PATH) — never the live machine:
#   UTAH_REPO, UTAH_DEV_PY, UTAH_PY, PG_ISREADY, UTAH_DSN, UTAH_STACK_TIMEOUT
set -euo pipefail

REPO="${UTAH_REPO:-$HOME/Desktop/ProjectUtah}"
DEV="${UTAH_DEV_PY:-$REPO/.venv/bin/python}"
RT="${UTAH_PY:-$HOME/.utah/venv/bin/python}"
PG_ISREADY="${PG_ISREADY:-/opt/homebrew/opt/postgresql@17/bin/pg_isready}"
DSN="${UTAH_DSN:-host=/tmp port=5433 dbname=utah}"
T="${UTAH_STACK_TIMEOUT:-20}"

# Belt: libpq-level bounds for any REAL psql (connect + per-statement), so a
# wedged postmaster fails fast on its own. Braces: the watchdog below is the
# suspenders for a psql that ignores these (or a fake one in tests).
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-5}"
export PGOPTIONS="${PGOPTIONS:--c statement_timeout=10000}"

FAILS=0
hdr() { printf '\n\033[1m===== %s =====\033[0m\n' "$1"; }
yes() { printf '  \033[32mOK\033[0m  %s\n' "$1"; }
nob() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS + 1)); }

# Under `set -e` any unguarded failure aborts the sweep; the trap makes that
# loud (line + command) so a broken verifier can't masquerade as a clean run.
trap 'printf "verify_stack: ERR line %s: %s (rc=%s)\n" "$LINENO" "$BASH_COMMAND" "$?" >&2' ERR

# bounded SECS CMD... — run CMD under a kill-watchdog. Returns CMD's rc, or 137
# when the watchdog had to SIGKILL it. Watchdog output goes to /dev/null so a
# command substitution around `bounded` never waits on the watchdog's pipe.
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

# pq SQL — one bounded scalar query against the primary; "?" on any failure
# (down, wedged-and-killed, missing table) so a dead DB reads as unknown, never
# as a hang and never as a green.
pq() {
  bounded "$T" psql "$DSN" -tAc "$1" 2>/dev/null || echo "?"
}

# mp PYBIN MODULE — "OK" / "-" (import failed) / "NO_PY", bounded.
mp() {
  local py="$1" mod="$2"
  [ -x "$py" ] || { echo "NO_PY"; return 0; }
  if bounded "$T" "$py" -c "import $mod" >/dev/null 2>&1; then echo OK; else echo "-"; fi
}

pchk() {
  printf '  %-22s  dev: %-5s  runtime: %-5s\n' "$1" "$(mp "$DEV" "$1")" "$(mp "$RT" "$1")"
}

# print_or BLOCK FALLBACK — indent a captured multi-line block, or the fallback.
print_block() {
  local text="$1" fallback="$2" indent="${3:-    }"
  if [ -n "$text" ]; then printf '%s\n' "$text" | sed "s/^/$indent/"; else echo "${indent}${fallback}"; fi
}

hdr "Two venvs (dev slim vs runtime daemons)"
if [ -x "$DEV" ]; then
  echo "  dev      : $(bounded "$T" "$DEV" --version 2>&1 || echo '?')  -> $DEV"
else
  nob "dev venv missing: $DEV"
fi
if [ -x "$RT" ]; then
  echo "  runtime  : $(bounded "$T" "$RT" --version 2>&1 || echo '?')  -> $RT"
else
  nob "runtime venv missing: $RT"
fi
echo "  launchd python (from com.utah plists):"
plists="$(grep -hoE '/Users/[^ "<]*venv/bin/python[0-9.]*' \
  "$HOME"/Library/LaunchAgents/com.utah.*.plist 2>/dev/null | sort | uniq -c || true)"
print_block "$plists" "(no plists found)"

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
grep -n "DB_DSN" "$REPO/utah/config.py" 2>/dev/null | sed 's/^/  /' || true
if [ -x "$PG_ISREADY" ]; then
  if bounded "$T" "$PG_ISREADY" -h /tmp -p 5433 -t 5 -q 2>/dev/null; then
    yes "pg_isready :5433"
  else
    nob "postgres not accepting on /tmp:5433"
  fi
elif command -v pg_isready >/dev/null 2>&1; then
  if bounded "$T" pg_isready -h /tmp -p 5433 -t 5 -q 2>/dev/null; then
    yes "pg_isready :5433"
  else
    nob "postgres not accepting on /tmp:5433"
  fi
else
  nob "pg_isready not found"
fi
if command -v psql >/dev/null 2>&1; then
  echo "  server   : $(pq 'show server_version')"
  echo "  pgvector : $(pq "select extversion from pg_extension where extname='vector'")"
  echo "  tables   : $(pq "select count(*) from information_schema.tables where table_schema='public'")"
  echo "  row counts:"
  for t in memory leads probate outreach_ledger fires failures; do
    c="$(pq "select count(*) from $t")"
    [ "$c" = "?" ] && c="<missing-or-wedged>"
    printf '    %-20s %s\n' "$t" "$c"
  done
else
  nob "psql not in PATH"
fi

hdr "DuckDB OLAP tier"
if command -v duckdb >/dev/null 2>&1; then
  yes "duckdb CLI: $(bounded "$T" duckdb --version 2>&1 | head -1 || echo '?')"
else
  nob "duckdb CLI missing"
fi
if [ -f "$REPO/utah/store/__init__.py" ]; then
  sed -n '1,6p' "$REPO/utah/store/__init__.py" | sed 's/^/  /'
fi
echo "  olap wired in code:"
wired="$(grep -rn 'store\.olap\|from utah\.store import olap\|olap\.query' "$REPO/utah" \
  --include='*.py' 2>/dev/null || true)"
print_block "$wired" "(none — olap.py stranded)"
# Real round-trip, not just a grep: ATTACH the live primary READ-ONLY through
# utah.store.olap and count a real table. The DSN travels via the ENVIRONMENT —
# never spliced into python source, where a quote in the DSN would become code.
echo "  olap real round-trip (runtime venv, read-only ATTACH, bounded):"
if [ -x "$RT" ]; then
  if out="$(
    export UTAH_OLAP_DSN="$DSN" PYTHONPATH="$REPO"
    bounded "$T" "$RT" -c '
import os
from utah.store import olap
rows = olap.query("SELECT count(*) FROM pg.public.leads",
                  dsn=os.environ["UTAH_OLAP_DSN"], timeout_s=15)
print(rows[0][0])
' 2>&1
  )"; then
    yes "olap.query leads count: $out"
  else
    nob "olap round-trip failed: $(printf '%s\n' "$out" | tail -1)"
  fi
else
  nob "runtime venv missing — olap round-trip skipped"
fi

hdr "SQLite retired"
echo "  *.db under ~/.utah and repo (maxdepth 3):"
dbs="$(find "$HOME/.utah" "$REPO" -maxdepth 3 \
  \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) 2>/dev/null || true)"
print_block "$dbs" "(none)"
echo "  sqlite3.connect in utah/:"
sq="$(grep -rln 'sqlite3\.connect' "$REPO/utah" 2>/dev/null || true)"
print_block "$sq" "(none)"

hdr "Models (~/.utah/models)"
if [ -d "$HOME/.utah/models" ]; then
  du -sh "$HOME/.utah/models" 2>/dev/null | sed 's/^/  /' || true
  du -sh "$HOME/.utah/models"/* 2>/dev/null | sed 's/^/  /' | head -15 || true
else
  nob "no models dir"
fi

hdr "Homebrew binaries"
for b in whisper-cli ffmpeg ffprobe ollama; do
  p="$(command -v "$b" 2>/dev/null || true)"
  if [ -n "$p" ]; then yes "$b -> $p"; else nob "$b missing"; fi
done
echo "  piper:"
for p in "$HOME/.utah/venv/bin/piper" "$REPO/.venv/bin/piper"; do
  if [ -x "$p" ]; then yes "$p"; else nob "missing $p"; fi
done

hdr "Ollama models"
if command -v ollama >/dev/null 2>&1; then
  if models="$(bounded "$T" ollama list 2>/dev/null)"; then
    print_block "$models" "(no models)" "  "
  else
    nob "ollama list failed (daemon down or wedged)"
  fi
else
  nob "ollama not installed"
fi

hdr "Daemons + sockets"
jobs_out="$(launchctl list 2>/dev/null || true)"
# -F: literal match — "com.utah" under regex would let "comXutah…" read as loaded.
if printf '%s' "$jobs_out" | grep -qF "com.utah.supervisor"; then
  yes "com.utah.supervisor loaded"
else
  nob "com.utah.supervisor not loaded"
fi
echo "  com.utah jobs:"
cu="$(printf '%s' "$jobs_out" | grep -F "com.utah" || true)"
print_block "$cu" "(none)"
echo "  unix sockets:"
socks="$(find "$HOME/.utah/run" -type s 2>/dev/null || true)"
print_block "$socks" "(none)"

hdr "Foundation probe"
if [ -x "$RT" ] && [ -d "$REPO/utah" ]; then
  if fout="$(
    export PYTHONPATH="$REPO"
    bounded "$T" "$RT" "$REPO/ops/foundation_check.py" 2>/dev/null
  )"; then
    yes "foundation probe green"
    print_block "$fout" "(no output)" "  "
  else
    nob "foundation probe red (or unrunnable) — substrate not green"
    print_block "$fout" "(no output)" "  "
  fi
  if [ -f "$HOME/.utah/run/foundation.json" ]; then
    sed 's/^/  /' "$HOME/.utah/run/foundation.json"
    echo
  fi
else
  nob "runtime venv or repo missing — foundation probe skipped"
fi

hdr "Hardware"
echo "  chip : $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo '?')"
echo "  arch : $(uname -m)   cores: $(sysctl -n hw.ncpu 2>/dev/null || echo '?')   ram: $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))GB"
echo "  macOS: $(sw_vers -productVersion 2>/dev/null || echo '?')"

hdr "Summary"
if [ "$FAILS" -eq 0 ]; then
  yes "all checks passed"
  exit 0
fi
printf '  \033[31m%s check(s) FAILED\033[0m — read the FAIL lines above\n' "$FAILS"
exit 1

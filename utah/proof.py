"""Proof Ledger — the proof spine.

Truth is a re-runnable check that passes against the real artifact; nothing else counts.
Fail-closed: anything not freshly proven (with every dependency green) reads RED. The tier
is only ever set by an actual proof run — there is no "mark proven" button.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
import time
import urllib.request

from utah import config
from utah.db_pool import get_pool

PROOF_KINDS = ("pytest", "sql", "shell", "http")
GREEN_TIERS = ("proven", "promoted")

_DDL = """
CREATE TABLE IF NOT EXISTS proof_ledger (
  id text PRIMARY KEY,
  claim text NOT NULL, system text NOT NULL, artifact text NOT NULL,
  proof_kind text NOT NULL, proof_cmd text, stress_cmd text,
  depends_on text[] NOT NULL DEFAULT '{}',
  freshness_sla interval NOT NULL DEFAULT '24 hours',
  tier text NOT NULL DEFAULT 'unproven',          -- raw marker; effective tier computed on read
  last_run timestamptz, last_result text, last_output text,
  last_proved_at timestamptz, stress_proved_at timestamptz,
  owner text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS proof_runs (
  id bigserial PRIMARY KEY,
  proof_id text NOT NULL REFERENCES proof_ledger(id) ON DELETE CASCADE,
  ts timestamptz NOT NULL DEFAULT now(), phase text NOT NULL DEFAULT 'proof',
  result text NOT NULL, output text NOT NULL DEFAULT '', duration_ms integer NOT NULL DEFAULT 0
);
"""


def _pool():
    return get_pool(config.DB_DSN)


def _ensure_schema() -> None:
    with _pool().connection() as c:
        c.execute(_DDL)


@dataclasses.dataclass(frozen=True)
class ProofSpec:
    id: str
    claim: str
    system: str
    artifact: str
    proof_kind: str = "sql"
    proof_cmd: str | None = None      # None = dormant skeleton row (RED, no proof written yet)
    stress_cmd: str | None = None
    depends_on: tuple[str, ...] = ()
    freshness_sla: str = "24 hours"
    owner: str = ""


def register(spec: ProofSpec) -> None:
    """Insert or update a claim's DEFINITION. Never touches earned proof state
    (tier/last_result/last_proved_at), so re-seeding the skeleton can't erase a proof."""
    _ensure_schema()
    with _pool().connection() as c:
        c.execute(
            """
            INSERT INTO proof_ledger
              (id,claim,system,artifact,proof_kind,proof_cmd,stress_cmd,depends_on,freshness_sla,owner)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::interval,%s)
            ON CONFLICT (id) DO UPDATE SET
              claim=EXCLUDED.claim, system=EXCLUDED.system, artifact=EXCLUDED.artifact,
              proof_kind=EXCLUDED.proof_kind, proof_cmd=EXCLUDED.proof_cmd,
              stress_cmd=EXCLUDED.stress_cmd, depends_on=EXCLUDED.depends_on,
              freshness_sla=EXCLUDED.freshness_sla, owner=EXCLUDED.owner, updated_at=now()
            """,
            (spec.id, spec.claim, spec.system, spec.artifact, spec.proof_kind,
             spec.proof_cmd, spec.stress_cmd, list(spec.depends_on), spec.freshness_sla, spec.owner),
        )


def effective_tier(proof_id: str, _seen: frozenset = frozenset()) -> str:
    """The truth of a claim right now: fail-closed, freshness-aware, dependency-aware."""
    if proof_id in _seen:                       # dependency cycle -> fail-closed
        return "unproven"
    with _pool().connection() as c:
        row = c.execute(
            """
            SELECT last_result,
                   last_proved_at IS NOT NULL AS proved,
                   (last_proved_at IS NOT NULL AND now()-last_proved_at < freshness_sla) AS fresh,
                   (stress_proved_at IS NOT NULL AND now()-stress_proved_at < freshness_sla) AS stress_fresh,
                   proof_cmd, depends_on
            FROM proof_ledger WHERE id=%s
            """,
            (proof_id,),
        ).fetchone()
    if not row:
        return "unproven"
    last_result, proved, fresh, stress_fresh, proof_cmd, deps = row
    if last_result == "reviewed":
        return "reviewed"
    if not proof_cmd:                           # dormant skeleton row
        return "unproven"
    if last_result in ("fail", "error"):
        return "failing"
    if last_result != "pass" or not proved:
        return "unproven"
    if not fresh:
        return "stale"
    seen = _seen | {proof_id}
    for d in deps or []:
        if effective_tier(d, seen) not in GREEN_TIERS:
            return "unproven"                   # cannot be trusted atop a red dependency
    return "promoted" if stress_fresh else "proven"


class ProofBlocked(Exception):
    """Raised by the gate when work depends on something not freshly proven."""


def require_proven(proof_id: str) -> None:
    try:
        tier = effective_tier(proof_id)
    except Exception as exc:                      # truth uncomputable -> block (fail-closed)
        raise ProofBlocked(f"{proof_id}: truth uncomputable ({exc}) — fail-closed") from exc
    if tier not in GREEN_TIERS:
        raise ProofBlocked(f"{proof_id} is {tier} — unproven work cannot be built upon")


def _run_one(kind: str, cmd: str, timeout: float = 120) -> tuple[str, str]:
    try:
        if kind == "pytest":
            p = subprocess.run([sys.executable, "-m", "pytest", *cmd.split(), "-q"],
                               capture_output=True, text=True, timeout=timeout)
            return ("pass" if p.returncode == 0 else "fail"), (p.stdout + p.stderr)[-4000:]
        if kind == "shell":
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return ("pass" if p.returncode == 0 else "fail"), (p.stdout + p.stderr)[-4000:]
        if kind == "sql":
            with _pool().connection() as c:
                r = c.execute(cmd).fetchone()
            return ("pass" if (r and r[0]) else "fail"), f"row={r!r}"
        if kind == "http":
            with urllib.request.urlopen(cmd, timeout=timeout) as resp:
                code = resp.getcode()
            return ("pass" if 200 <= code < 300 else "fail"), f"HTTP {code}"
        return "error", f"unknown proof_kind {kind!r}"
    except Exception as exc:                       # a crashed proof IS a result, never an exception
        return "error", f"{type(exc).__name__}: {exc}"[:4000]


def run(proof_id: str, phase: str = "proof") -> tuple[str, str]:
    _ensure_schema()
    with _pool().connection() as c:
        row = c.execute("SELECT proof_kind, proof_cmd, stress_cmd FROM proof_ledger WHERE id=%s",
                        (proof_id,)).fetchone()
    if not row:
        raise KeyError(proof_id)
    kind, proof_cmd, stress_cmd = row
    cmd = stress_cmd if phase == "stress" else proof_cmd
    if not cmd:
        return "error", "no command for phase"
    t0 = time.monotonic()
    result, output = _run_one(kind, cmd)
    dur = int((time.monotonic() - t0) * 1000)
    with _pool().connection() as c:
        c.execute("INSERT INTO proof_runs (proof_id,phase,result,output,duration_ms) VALUES (%s,%s,%s,%s,%s)",
                  (proof_id, phase, result, output, dur))
        if phase == "proof":
            c.execute(
                """UPDATE proof_ledger SET last_run=now(), last_result=%s, last_output=%s,
                   last_proved_at=CASE WHEN %s='pass' THEN now() ELSE last_proved_at END,
                   updated_at=now() WHERE id=%s""",
                (result, output, result, proof_id))
        else:
            c.execute(
                """UPDATE proof_ledger SET stress_proved_at=CASE WHEN %s='pass' THEN now()
                   ELSE stress_proved_at END, updated_at=now() WHERE id=%s""",
                (result, proof_id))
    return result, output


def run_scheduled(ids: list[str] | None = None) -> dict:
    """Re-run every (or selected) proof that has a command; record + return a tally.
    Never raises — a re-verify sweep that dies of what it measures reports nothing."""
    _ensure_schema()
    with _pool().connection() as c:
        if ids:
            rows = c.execute(
                "SELECT id FROM proof_ledger WHERE id = ANY(%s) AND proof_cmd IS NOT NULL",
                (ids,)).fetchall()
        else:
            rows = c.execute("SELECT id FROM proof_ledger WHERE proof_cmd IS NOT NULL").fetchall()
    tally = {"ran": 0, "pass": 0, "fail": 0, "error": 0}
    for (pid,) in rows:
        try:
            res, _ = run(pid)
            tally["ran"] += 1
            tally[res] = tally.get(res, 0) + 1
        except Exception:                          # one bad proof never stops the sweep
            tally["error"] += 1
    return tally

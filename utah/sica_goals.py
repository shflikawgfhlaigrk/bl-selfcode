"""SICA goal source — WHAT the autonomous loop optimizes.

Replaces archive-mimicry (trivial `_meta` files) with REAL, domain-targeted
improvement tasks. Each cycle rotates across domains and asks the brain for ONE
concrete, tier-aware task grounded in LIVE signals:

  baseline  — code/test health: fix a flaky/failing test, add missing coverage,
              clear a failure-log entry. (leaf Tier-A / spine Tier-C.)
  leads     — the revenue pipeline: lead volume / sources / dedup / contact
              enrichment, grounded in the Postgres leads ledger + product/leads.py.
              (Tier-B → stays a reviewed proposal, never auto-merged.)
  autonomy  — the self-improver itself: the meta-loop / scoring / overseer
              (sica_loop|sica_autonomy|sica_overseer|sica_goals — the NON-safety
              parts; the Tier-D core selfcode/config/brain stay off-limits).
              "how to be autonomous": make it pick better tasks, score, compound.

Every signal reader is defensive (never raises) and injected so this is
unit-proven without DB/brain.
"""
from __future__ import annotations

import json
import logging

from utah import config, sica
from utah.daemon import runtime

log = logging.getLogger("utah.sica_goals")

DOMAINS = ("baseline", "leads", "autonomy")
CYCLE_N = runtime.RUN_DIR / "selfcode-cycle.n"
VERIFY_JSON = runtime.RUN_DIR / "verify.json"


def pick_domain(n: int) -> str:
    """Round-robin so baseline/leads/autonomy all get attention over time."""
    return DOMAINS[n % len(DOMAINS)]


def next_cycle_index() -> int:
    """Read + increment a persistent cycle counter (drives the domain rotation)."""
    try:
        n = int(json.loads(CYCLE_N.read_text()).get("n", 0))
    except Exception:  # noqa: BLE001 - absent/corrupt → start at 0
        n = 0
    try:
        CYCLE_N.parent.mkdir(parents=True, exist_ok=True)
        CYCLE_N.write_text(json.dumps({"n": n + 1}))
    except Exception as exc:  # noqa: BLE001
        log.warning("cycle counter write failed: %s", exc)
    return n


# ── live signal readers (defensive; injectable) ──────────────────────────────
def _db_query(sql: str):
    import psycopg
    with psycopg.connect(config.DB_DSN, connect_timeout=8) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def _leads_signal(db_query=None) -> str:
    q = db_query or _db_query
    try:
        by_src = q("SELECT source, count(*) FROM leads GROUP BY source ORDER BY 2 DESC")
        total = sum(n for _, n in by_src)
        with_contact = q("SELECT count(*) FROM leads WHERE contact IS NOT NULL AND contact <> '{}'::jsonb")[0][0]
        probate = q("SELECT count(*) FROM probate")[0][0]
        srcs = ", ".join(f"{s}={n}" for s, n in by_src) or "none"
        return (f"leads total={total} by source [{srcs}]; with_contact={with_contact}; "
                f"probate_rows={probate}. Capability: utah/product/leads.py (OSM/Overpass, "
                f"national-chain + has-website filters, UNIQUE(name,region) dedup).")
    except Exception as exc:  # noqa: BLE001
        return f"(leads signal unavailable: {type(exc).__name__}); capability: utah/product/leads.py"


def _baseline_signal(read_text=None) -> str:
    rt = read_text or (lambda p: p.read_text() if p.exists() else "")
    try:
        verify = json.loads(rt(VERIFY_JSON) or "{}")
        state = verify.get("state", "unknown")
        detail = str(verify.get("detail", verify.get("failing", "")))[:200]
        return (f"verifier state={state} detail={detail!r}. Improve code/test health: "
                f"fix a flaky/failing test, add coverage for an untested branch, or clear "
                f"a real failure-log entry. Keep changes leaf-level (Tier-A) where possible.")
    except Exception as exc:  # noqa: BLE001
        return f"(baseline signal unavailable: {type(exc).__name__}); improve test coverage/health."


def _autonomy_signal(archive=None) -> str:
    arch = archive or sica.Archive()
    try:
        entries = arch.entries()
        best = max((e.get("utility", 0) for e in entries), default=0)
        fails = sum(1 for e in entries if not e.get("passed"))
        return (f"self-coding archive: {len(entries)} attempts, best_utility={best}, "
                f"{fails} failed. Improve the autonomy machinery (utah/sica_loop.py, "
                f"utah/sica_autonomy.py, utah/sica_overseer.py, utah/sica_goals.py) — better "
                f"task selection, scoring, overseer, or compounding. Do NOT touch the Tier-D "
                f"safety core (selfcode.py/config.py/brain.py).")
    except Exception as exc:  # noqa: BLE001
        return f"(autonomy signal unavailable: {type(exc).__name__})"


def gather_signals(domain: str, **inject) -> str:
    if domain == "leads":
        return _leads_signal(db_query=inject.get("db_query"))
    if domain == "autonomy":
        return _autonomy_signal(archive=inject.get("archive"))
    return _baseline_signal(read_text=inject.get("read_text"))


_PROMPT = (
    "You are Utah's autonomous self-improver. Domain THIS cycle: {domain}.\n"
    "Live signals:\n{signals}\n\n"
    "Propose the SINGLE next concrete improvement task IN THIS DOMAIN: one sentence, "
    "actionable, small, and SAFE. Touch as few files as possible. NEVER edit the safety "
    "core (utah/selfcode.py, utah/config.py, utah/brain.py, utah/daemon/peercred.py, "
    "lifecycle.py, governor.py). Reply with ONLY the task text."
)


def build_prompt(domain: str, signals: str) -> str:
    return _PROMPT.format(domain=domain, signals=signals)


def next_task(domain: str, *, brain_fn, **inject) -> str:
    """Domain signals → prompt → brain → one grounded task (stripped)."""
    signals = gather_signals(domain, **inject)
    return (brain_fn(build_prompt(domain, signals)) or "").strip()


__all__ = ["DOMAINS", "pick_domain", "next_cycle_index", "gather_signals",
           "build_prompt", "next_task", "CYCLE_N"]

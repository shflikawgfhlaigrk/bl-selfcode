"""Think-on-fire — Ace's live read on every graded fire, grounded in the migrated corpus.

Michael's ask (2026-06-10): "is Ace giving live assessments vs the thousands of logged
trades?" — the answer was no: old Ace's 4,513-row ``engine_fire_commentary`` corpus sat
unmigrated in ``~/.ace/ace.db`` and Utah had no commentary lane. This module is that lane:

1. :func:`migrate_legacy` — ONE-TIME forward migration of the historical corpus into the
   ledger's ``trade_lore`` table (same one-shot pattern as the WC chrome-profile
   migration: read the legacy store once, never depend on ``~/.ace`` at runtime; the
   UNIQUE(engine, ts, kind) key makes re-runs no-ops).
2. :func:`assess_fire` — PURE: composes the grounded prompt (the fire's real graded
   outcome + the engine's real scorecard + a sample of the historical voice) and shapes
   the brain's 2-3 sentence read. Honest by construction: every number in the context is
   a ledger fact; a brain failure returns None (never a fabricated read).
3. :func:`run_assessments` — bounded cron hook (rides ``com.utah.grade-fires``): for up
   to N newly-graded fires, write the read onto ``fires.assessment`` exactly once.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("utah.product.trade_lore")

#: Legacy corpus location — read ONCE by the migration, never by the runtime loop.
#: Env-overridable (UTAH_LEGACY_ACE_DB) so the path is configuration, not a constant.
LEGACY_DB = os.environ.get("UTAH_LEGACY_ACE_DB", os.path.expanduser("~/.ace/ace.db"))

#: Per-cron-run cap on brain calls: each read is a real `claude -p` invocation.
ASSESS_PER_RUN = 3

#: The framing the old corpus carried on every row — preserved verbatim.
DISCLAIMER = "This is data, not a directive."


def migrate_legacy(ledger, db_path: str = LEGACY_DB, limit: int = 10000) -> dict:
    """Forward-migrate ``engine_fire_commentary`` → ``trade_lore``. Re-runnable
    (dedup on engine+ts+kind). Returns counts; never raises."""
    import contextlib
    import sqlite3

    out = {"read": 0, "migrated": 0, "skipped": 0}
    if not os.path.exists(db_path):
        out["error"] = f"legacy db not found: {db_path}"
        return out
    try:
        ledger.init_schema()
        # closing() releases the read-only handle even when the query raises (a
        # bare connect+execute leaked it on the bad-schema path); timeout bounds
        # the wait on a writer-locked legacy file instead of hanging the cron.
        with contextlib.closing(
                sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)) as src:
            rows = src.execute(
                "SELECT fire_ts, engine, fire_kind, commentary, confidence "
                "FROM engine_fire_commentary WHERE commentary IS NOT NULL "
                "AND length(commentary) > 20 ORDER BY fire_ts LIMIT ?",
                (max(0, int(limit)),)).fetchall()
    except Exception as exc:  # noqa: BLE001 — a broken legacy store just means no lore
        out["error"] = str(exc)
        return out
    keys: set = set()
    for ts, engine, kind, content, conf in rows:
        out["read"] += 1
        keys.add((engine or "unknown", ts, kind or ""))   # the (engine,ts,kind) dedup key
        try:
            if ledger.add_lore(ts, engine or "unknown", kind or "", content, conf):
                out["migrated"] += 1
            else:
                out["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad row never aborts the migration
            log.debug("lore row skipped: %s", exc)

    # Reconciliation: the target must hold EXACTLY the source's distinct dedup keys.
    # Any gap not accounted for by source duplicates is an unexplained shortfall — a
    # failed migration surfaced honestly, never a silent "looks done".
    source_rows = out["read"]
    source_distinct = len(keys)
    source_dupes = source_rows - source_distinct
    try:
        target_count = int(ledger.lore_count())
    except Exception as exc:  # noqa: BLE001 — can't verify -> say so, don't fake ok
        out["reconcile"] = {"ok": False, "error": f"target count unreadable: {exc}",
                            "source_rows": source_rows, "source_distinct": source_distinct,
                            "source_dupes": source_dupes}
        return out
    out["reconcile"] = {
        "ok": target_count >= source_distinct,   # every distinct source key landed
        "source_rows": source_rows,
        "source_distinct": source_distinct,
        "source_dupes": source_dupes,
        "skipped": out["skipped"],
        "target_count": target_count,
    }
    if not out["reconcile"]["ok"]:
        from utah import failures
        failures.record("trade_lore", "migration_shortfall",
                        f"target {target_count} < distinct source keys {source_distinct} "
                        f"(read {source_rows}, dupes {source_dupes})")
    return out


def _prompt(fire: dict, scorecard: dict, lore: list[dict]) -> tuple[str, str]:
    """(question, context) for the brain — every number a ledger fact."""
    voice = "\n".join(f"- ({r.get('engine')}/{r.get('kind')}) {r.get('content')}"
                      for r in lore[:5]) or "- (no historical commentary yet)"
    wr = scorecard.get("win_rate")
    if not isinstance(wr, (int, float)) or isinstance(wr, bool):
        wr = None   # jsonb drift ('n/a', None, lists) must never crash the read mid-cron
    context = (
        f"FIRE (graded, real): engine={fire.get('engine')} {fire.get('direction')} "
        f"entry={fire.get('entry')} symbol={fire.get('symbol') or '?'} "
        f"outcome={fire.get('outcome')} pnl={fire.get('pnl')} pts (paper).\n"
        f"ENGINE SCORECARD (all graded fires): {scorecard.get('graded')} graded, "
        f"{scorecard.get('wins')} wins"
        + (f" ({wr:.0%})" if wr is not None else "")
        + f", net {scorecard.get('net_pnl')} pts.\n"
        f"HISTORICAL READS (Ace's own past commentary on this fleet):\n{voice}"
    )
    question = (
        "Give your 2-3 sentence read on this graded fire — what the outcome says about "
        "the setup and the engine's current record. Use ONLY the numbers in the context. "
        f"End with: {DISCLAIMER}"
    )
    return question, context


def assess_fire(fire: dict, scorecard: dict, lore: list[dict], think_fn) -> str | None:
    """One grounded read. Returns None on any brain failure/refusal — never fabricates."""
    question, context = _prompt(fire, scorecard, lore)
    try:
        text = (think_fn(question, context) or "").strip()
    except Exception as exc:  # noqa: BLE001 — a brain hiccup just postpones the read
        log.debug("assess_fire brain failed: %s", exc)
        return None
    if not text or text.lower().startswith("i don't know"):
        return None
    if DISCLAIMER.lower() not in text.lower():
        text = f"{text} {DISCLAIMER}"
    return text


def run_assessments(ledger=None, limit: int = ASSESS_PER_RUN, think_fn=None) -> dict:
    """Cron hook (rides com.utah.grade-fires): write Ace's read onto up to *limit*
    newly-graded fires. Bounded, best-effort, never raises, never re-writes."""
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    if think_fn is None:
        from utah import brain
        think_fn = brain.think
    out = {"checked": 0, "assessed": 0, "skipped": 0}
    limit = max(0, int(limit))   # a negative caller limit must never reach the store
    try:
        fires = ledger.fires_missing_assessment(limit=limit)
    except Exception as exc:  # noqa: BLE001 — store down: report, don't crash the cron
        out["error"] = str(exc)
        return out
    for fire in fires:
        out["checked"] += 1
        try:
            text = assess_fire(fire, ledger.engine_scorecard(fire["engine"]),
                               ledger.lore_for(fire["engine"]), think_fn)
            if text and ledger.set_fire_assessment(fire["id"], text):
                out["assessed"] += 1
                log.info("think-on-fire #%s (%s): %s", fire["id"], fire["engine"],
                         text[:90])
            else:
                out["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — one fire never aborts the run
            out["skipped"] += 1
            log.debug("assessment skipped for fire %s: %s", fire.get("id"), exc)
    return out


__all__ = ["migrate_legacy", "assess_fire", "run_assessments", "ASSESS_PER_RUN",
           "DISCLAIMER", "LEGACY_DB"]

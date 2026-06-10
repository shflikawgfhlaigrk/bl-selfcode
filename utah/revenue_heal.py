"""Revenue self-heal — "anytime the leads don't generate, fix your OWN code to generate it."

When a revenue producer goes dark (no new rows within its expected window, or a run that
yields zero), Ace does not shrug and wait for Michael — he files a concrete self-code repair
task against the producer's OWN module. The SICA loop picks the task up
(:func:`sica_discover.next_pending_task`), codes a fix under the suite gate, and lands it on
the live tree via durable propagation. Fully autonomous; no human in the loop.

Read-only DB truth (never fabricates rows). Conservative thresholds so a naturally slow day
never triggers a needless self-code run — only a real outage does.
"""
from __future__ import annotations

import logging
from typing import Callable

from utah import config, failures, sica_discover

log = logging.getLogger("utah.revenue_heal")

#: producer -> (ledger table, the module Ace must fix, max idle HOURS before it's an outage).
#: leads runs daily and must always produce; probate is lumpier (county filings) → generous.
#: ``sent`` is the OUTCOME producer (B12): it watches CONVERSION (real emails/texts sent),
#: not just generation — the post-mortem's "measure outcomes, not activity" put in the
#: machine. A scraper that fills the lead pile while NOTHING is sent is the exact failure
#: that left Ace at $0; this trips on it and files a repair against the SENDER.
PRODUCERS: dict[str, tuple[str, str, float]] = {
    "leads":   ("leads",   "utah/product/leads.py",   30.0),
    "probate": ("probate", "utah/product/probate.py", 72.0),
    "sent":    ("mail_ledger", "utah/product/outreach.py", 26.0),
}

#: The outcome window (hours): within a normal business day the sender must have sent at
#: least one real message. 26h tolerates one quiet overnight without false-tripping.
OUTCOME_WINDOW_H: float = 26.0


def _hours_since_last(table: str) -> float | None:
    """Hours since the newest row in *table*; None if unreachable/empty (cannot assess →
    never false-trigger). *table* comes from the fixed PRODUCERS map, never user input."""
    import psycopg

    try:
        with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=8) as conn:
            row = conn.execute(
                f"select extract(epoch from (now() - max(ts))) / 3600 from {table}"  # noqa: S608
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 — DB down is its own failure class, not a revenue outage
        log.debug("revenue_heal: cannot read %s: %s", table, exc)
        return None
    return float(row[0]) if row and row[0] is not None else None


def _repair_task(name: str, module: str, idle_h: float) -> str:
    when = f"in {idle_h:.0f}h" if idle_h else "on its last run"
    if name == "sent":
        # The OUTCOME repair (B12): money isn't FLOWING, regardless of how full the lead
        # pile is. Point the coder at the SEND last-mile, not a scraper.
        return (
            f"REVENUE OUTCOME OUTAGE: NO real outreach has been SENT {when} (0 rows in "
            f"`mail_ledger`). Leads may be piling up, but $0 is moving — the exact failure "
            f"that left Ace dead. Diagnose why the sender stopped: an exhausted EMAILABLE "
            f"pool (add email enrichment so phone-only leads become reachable), a send path "
            f"silently gating, the business-hours window, or a crash in `{module}`. Fix it so "
            f"compliant outreach actually goes out every business hour. Add a regression test "
            f"that fails when a scheduled run sends zero with a non-empty contactable pool. "
            f"Never fake a send — make the real send happen."
        )
    return (
        f"REVENUE OUTAGE: `{module}` has produced NO new {name} {when} — the producer has "
        f"gone dark. It MUST generate {name} on every scheduled run. Diagnose the REAL reason "
        f"it stopped (exhausted frontier/cursor, upstream fetch failing, an over-strict "
        f"filter, or a crash) and fix `{module}` so it generates again. Add a regression test "
        f"in tests/ that fails on the no-yield path. Never fabricate rows — fix the real "
        f"generator so it produces genuine data again."
    )


def _count_since(table: str, hours: float) -> int | None:
    """Rows in *table* newer than *hours* ago; None if unreachable (cannot assess →
    never falsely report '$0'). *table* is a fixed literal here, never user input."""
    import psycopg

    try:
        with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=8) as conn:
            row = conn.execute(
                f"select count(*) from {table} "  # noqa: S608 — table is a fixed literal
                f"where ts > now() - make_interval(hours => %s)",
                (hours,),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:  # noqa: BLE001 — missing table / DB down → unassessable
        log.debug("revenue_heal: cannot count %s: %s", table, exc)
        return None


def outcome_gate(window_h: float | None = None) -> dict:
    """THE OUTCOME GATE (B12 / post-mortem rule #1). Reports whether the BUSINESS is
    producing real outcomes — money flowing — not whether the machine is busy. ``ok`` is
    True only when at least one real send (``mail_ledger``) OR sale (``sales``, once Stripe
    is wired) landed in the window. While ``ok`` is False the system is NOT "done", no
    matter how green the substrate or how many self-coding cycles ran. This is the gate the
    post-mortem demanded that was never put in the machine — now it is, queryable by the
    deck and the autonomy loop. Read-only; never fabricates."""
    window_h = OUTCOME_WINDOW_H if window_h is None else window_h
    sends = _count_since("mail_ledger", window_h)
    sales = _count_since("sales", window_h)          # table may not exist yet → None
    if sends is None and sales is None:
        return {"ok": False, "assessable": False, "sends": None, "sales": None,
                "window_h": window_h, "reason": "cannot read revenue ledgers (DB unreachable)"}
    s_send = sends or 0
    s_sale = sales or 0
    ok = s_send > 0 or s_sale > 0
    return {
        "ok": ok, "assessable": True, "sends": s_send, "sales": s_sale,
        "window_h": window_h,
        "reason": ("revenue flowing" if ok else
                   f"NO outcome in {window_h:.0f}h — {s_send} sends, {s_sale} sales: "
                   f"the system is NOT done while $0 is moving"),
    }


def is_revenue_green() -> bool:
    """True iff a real revenue outcome landed inside the window — the one-line gate the
    deck/autonomy consult so a $0 system is never reported as 'green'."""
    return bool(outcome_gate().get("ok"))


def scan(*, age_fn: Callable[[str], float | None] | None = None,
         file_fn=None, record_fn=None, producers=None) -> list[dict]:
    """One revenue self-heal sweep: for every producer gone dark, file a self-code repair
    task (deduped) so Ace fixes his own generator. Boundaries injected for unit-proof.
    Never raises. Returns the actions taken (empty when everything is healthy)."""
    age_of = age_fn or _hours_since_last
    file_task = file_fn or sica_discover.file_task
    record = record_fn or failures.record
    producers = producers or PRODUCERS
    actions: list[dict] = []
    for name, (table, module, max_idle_h) in producers.items():
        try:
            idle = age_of(table)
            if idle is None or idle <= max_idle_h:
                continue  # healthy or unassessable — never false-trigger a self-code run
            filed = file_task("revenue", _repair_task(name, module, idle))
            record(name, "stale_producer",
                   f"{name}: no new rows in {idle:.0f}h (>{max_idle_h:.0f}h) — "
                   f"filed self-code repair (new={filed.get('filed')})")
            actions.append({"producer": name, "idle_h": round(idle, 1),
                            "filed": bool(filed.get("filed"))})
        except Exception as exc:  # noqa: BLE001 — one producer must not abort the sweep
            log.warning("revenue_heal %s failed: %s", name, exc)
    return actions


def check_producer(name: str, result: dict, *, file_fn=None, record_fn=None) -> dict:
    """Inline variant for a cron that has the run RESULT in hand: file a repair IFF the
    producer RAN but yielded ZERO. A gated/skipped run is not a generation failure (never
    self-code a deliberately-gated producer)."""
    result = result or {}
    if (result.get("gated") or result.get("skipped")
            or result.get("ran") is False or "reason" in result):
        return {"healed": False, "reason": "gated/skipped — not a generation failure"}
    _table, module, _ = PRODUCERS.get(name, (name, f"utah/product/{name}.py", 30.0))
    produced = next((int(result[k]) for k in ("new", "count", "found")
                     if isinstance(result.get(k), int)), 0)
    if produced > 0:
        return {"healed": False, "produced": produced, "reason": "healthy"}
    filed = (file_fn or sica_discover.file_task)("revenue", _repair_task(name, module, 0.0))
    (record_fn or failures.record)(name, "zero_yield",
        f"{name} produced 0 new — filed self-code repair (new={filed.get('filed')})")
    return {"healed": True, "filed": bool(filed.get("filed")), "rec": filed.get("rec")}


__all__ = ["PRODUCERS", "OUTCOME_WINDOW_H", "scan", "check_producer",
           "outcome_gate", "is_revenue_green"]

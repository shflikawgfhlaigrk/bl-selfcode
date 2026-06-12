"""Conversational ACTION capability — make a chat/voice COMMAND actually EXECUTE.

"How many leads do we have?" is a *question* → the LEADS read capability counts them.
"Rerun the leads" / "run the 500 leads again" is a *command* → this module RUNS the
real finder and reports the REAL net-new count, never a fabricated "done".

The bug this fixes: a run/rerun verb ("rerun the lead scout") matched the LEADS *read*
capability's ``\\blead[_\\s-]?scout\\b`` and only reported counts — it never executed.
``Route.ACTION`` is checked BEFORE the read capabilities for run/rerun verbs, so a
command runs the work and a question still just counts.

Each intent maps to the SAME real capability the cron/RPC fires — there is no second,
fake implementation:

* ``scout_leads``    → :func:`utah.product.leads.run_scheduled`     (find no-website SMBs)
* ``scout_probate``  → :func:`utah.product.probate.run_scheduled`   (capture GA probate)
* ``outreach``       → :func:`utah.product.outreach.run_scheduled`  (SEND, within the
                       existing CAN-SPAM / business-hours / deliverability send-gates)
* ``run_engines``    → :func:`utah.product.trading.run`             (WC-feed gated)
* ``research``       → :func:`utah.product.researcher.research`     (web → grounded facts)

HONESTY LAW: every number in a reply is the REAL value the capability returned this run
(``new``/``sent``/``fires``/``stored``) plus the real before→after ledger totals; a
failure says so plainly and surfaces the real reason — never a painted success number.
The data-populating actions fire immediately (no permission prompt); the SEND action
runs inside the code's own legal send-gates and reports what they did.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("utah.actions")


#: A run/rerun VERB — the thing that distinguishes a command from a question. Anchored
#: so a bare "leads" (a noun) is never an action; only an explicit imperative is.
_RUN_VERB = re.compile(
    r"\b(re-?run|re-?do|kick\s*off|fire\s*off|trigger|execute|"
    r"run|scout|work|find|pull|fetch|refresh|populate)\b",
    re.I,
)
#: "…again" / "one more time" also makes a noun-led phrase a command ("the leads again").
_AGAIN = re.compile(r"\b(again|one more time|once more|another (run|pass|round))\b", re.I)

#: Each capability noun → intent key. Order matters and is checked top-to-bottom:
#: ``research`` is FIRST (so "run research on probate law" is research, not probate —
#: the explicit "research" verb-noun wins over the topic word "probate"); then
#: probate/outreach/engines, and the generic "leads" LAST so a more specific noun wins.
_NOUNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bresearch\b", re.I), "research"),
    (re.compile(r"\bprobate\b", re.I), "scout_probate"),
    (re.compile(r"\b(outreach|cold\s*(email|outreach)|work\s+(the\s+)?leads?|"
                r"pitch(es)?|send\s+(the\s+)?(emails?|pitches?))\b", re.I), "outreach"),
    (re.compile(r"\b(engines?|trading|fires?|signals?)\b", re.I), "run_engines"),
    (re.compile(r"\b(leads?|lead[_\s-]?scout|prospects?|finder|frontier|"
                r"the\s+\d+\s+leads?)\b", re.I), "scout_leads"),
]

#: Question phrasings — a COUNT/READ question, never a run command. "how many leads did
#: lead_scout find" contains the verb "find" but is asking the READ capability for a
#: number, not telling it to run. These keep is_action False so the LEADS read still wins.
_QUESTION = re.compile(
    r"^\s*(how many|how much|what'?s|what is|what are|how'?s|"
    r"do we have|did|does|is there|are there|count of|number of)\b",
    re.I,
)


def is_action(text: str) -> bool:
    """True when *text* is a COMMAND to run a capability (a run/rerun verb — or an
    '…again' — over a known capability noun). A pure question ("how many leads did
    lead_scout find") has a verb but ASKS for a number; it is excluded so the LEADS
    *read* capability still wins. This is what the router checks before the read."""
    t = text or ""
    if _QUESTION.match(t):
        return False
    has_trigger = bool(_RUN_VERB.search(t) or _AGAIN.search(t))
    return has_trigger and classify(t) is not None


def classify(text: str) -> str | None:
    """Map a command to its capability intent key, or ``None`` if no capability noun is
    present. Pure + table-driven (unit-tested), so the router/core never embed the map."""
    t = text or ""
    for pat, intent in _NOUNS:
        if pat.search(t):
            return intent
    return None


def _fmt_total(n: int | None) -> str:
    return "?" if n is None else f"{n:,}"


def _leads_reply(text: str, run_leads, total_leads) -> str:
    before = _safe_total(total_leads)
    res = run_leads()
    new = int(res.get("new", 0))
    after = _safe_total(total_leads)
    found = res.get("found")
    tail = ""
    if before is not None and after is not None:
        tail = f", {before:,}->{after:,}"
    elif after is not None:
        tail = f", total now {after:,}"
    extra = f" (scanned {res.get('tiles_scanned')} tiles, {found} found)" if found is not None else ""
    return f"Re-ran the lead scout: +{new:,} net-new{tail}.{extra}".rstrip()


def _probate_reply(text: str, run_probate, total_probate) -> str:
    before = _safe_total(total_probate)
    res = run_probate()
    new = int(res.get("new", 0))
    after = _safe_total(total_probate)
    region = res.get("region") or ""
    tail = f", {before:,}->{after:,}" if before is not None and after is not None else (
        f", total now {after:,}" if after is not None else "")
    where = f" in {region}" if region else ""
    if res.get("error"):
        return f"Re-ran probate but the GPN source failed: {res['error']}."
    return f"Re-ran probate: +{new:,} net-new{where}{tail}.".rstrip()


def _outreach_reply(text: str, run_outreach) -> str:
    res = run_outreach()
    sent = int(res.get("sent", 0))
    queued = int(res.get("queued", 0))
    channel = res.get("channel") or res.get("campaign") or ""
    if res.get("skipped") or (sent == 0 and res.get("reason")):
        reason = res.get("reason") or "the send-gate held it"
        return f"Ran outreach: didn't send (0) — {reason}."
    via = f" via {channel}" if channel else ""
    q = f", {queued} queued" if queued else ""
    return f"Ran outreach: sent {sent:,}{via}{q}."


def _engines_reply(text: str, run_engines) -> str:
    res = run_engines()
    fires = int(res.get("fires", 0))
    if res.get("gated"):
        return ("Ran the engines: 0 fires — gated on the live WealthCharts feed "
                "(Michael's WC login). No fire is faked.")
    if res.get("error"):
        return f"Ran the engines: 0 fires — feed error: {res['error']}."
    if fires == 0:
        sup = res.get("suppressed")
        why = f" ({sup})" if sup else " — no signal this pass"
        return f"Ran the engines: 0 fires{why}."
    return f"Ran the engines: {fires:,} fire(s) recorded."


def _research_reply(text: str, run_research) -> str:
    query = _research_query(text)
    res = run_research(query)
    stored = int(res.get("stored", 0))
    facts = int(res.get("facts", 0))
    sources = int(res.get("sources", 0))
    if res.get("error"):
        return f"Ran research on {query!r}: couldn't — {res['error']}."
    return (f"Ran research on {query!r}: {sources} source(s), "
            f"{facts} fact(s) found, {stored} learned (stored).")


def _research_query(text: str) -> str:
    """Pull the topic out of 'run research on <topic>' / 'research <topic>'."""
    m = re.search(r"\bresearch(?:\s+on|\s+about|\s+into)?\s+(.+)$", text or "", re.I)
    q = (m.group(1).strip() if m else "").strip(" .?!")
    return q or (text or "").strip()


def _safe_total(total_fn) -> int | None:
    """A best-effort ledger COUNT for before/after framing — never fails the action.
    A dead count read just drops the before->after tail; the capability's own ``new``
    (the real net-new) is still reported."""
    if total_fn is None:
        return None
    try:
        return int(total_fn())
    except Exception as exc:  # noqa: BLE001 — framing is best-effort, the action ran
        log.debug("action total read failed (dropping framing): %s", exc)
        return None


# -- real-capability binders (the production defaults; tests inject fakes) ----------

def _real_run_leads() -> dict:
    from utah.product import leads
    from utah.product.ledger import get_ledger

    return leads.run_scheduled(ledger=get_ledger())


def _real_run_probate() -> dict:
    from utah.product import probate
    from utah.product.ledger import get_ledger

    return probate.run_scheduled(ledger=get_ledger())


def _real_run_outreach() -> dict:
    from utah.product import outreach
    from utah.product.ledger import SMB_OUTREACH_CAMPAIGN, get_ledger

    # auto channel (email-first, text fallback). run_scheduled enforces the
    # business-hours + deliverability send-gates in code — we don't bypass them.
    return outreach.run_scheduled(SMB_OUTREACH_CAMPAIGN, ledger=get_ledger(), channel="auto")


def _real_run_engines() -> dict:
    from utah.product import trading
    from utah.product.ledger import get_ledger

    return trading.run(get_ledger())


def _real_run_research(query: str) -> dict:
    from utah.product import researcher

    return researcher.research(query)


def _real_total_leads() -> int:
    from utah.product import leads_status

    return int(leads_status.counts().get("total", 0))


def _real_total_probate() -> int:
    from utah.product import leads_status

    return int(leads_status.counts().get("probate", 0))


def run(
    text: str,
    *,
    run_leads=None,
    run_probate=None,
    run_outreach=None,
    run_engines=None,
    run_research=None,
    total_leads=None,
    total_probate=None,
) -> str:
    """Execute the capability *text* commands and return a speakable reply reporting the
    REAL result. Every executor is injectable (tests pass fakes; production binds the
    real capability). HONEST on failure: a capability exception is caught and the real
    reason is surfaced — never a fabricated success."""
    intent = classify(text)
    if intent is None:
        return "I didn't catch which thing to run — try 'rerun the leads', 'run outreach', 'rerun probate', or 'run the engines'."

    try:
        if intent == "scout_leads":
            return _leads_reply(text, run_leads or _real_run_leads,
                                total_leads if total_leads is not None else _real_total_leads)
        if intent == "scout_probate":
            return _probate_reply(text, run_probate or _real_run_probate,
                                  total_probate if total_probate is not None else _real_total_probate)
        if intent == "outreach":
            return _outreach_reply(text, run_outreach or _real_run_outreach)
        if intent == "run_engines":
            return _engines_reply(text, run_engines or _real_run_engines)
        if intent == "research":
            return _research_reply(text, run_research or _real_run_research)
    except Exception as exc:  # noqa: BLE001 — a failed run is REPORTED honestly, never painted
        log.warning("action %r failed: %s", intent, exc)
        from utah import failures

        try:
            failures.record("actions", f"{intent}_failed", str(exc)[:200])
        except Exception:  # noqa: BLE001 — recording is best-effort
            pass
        return f"I couldn't run {_human(intent)} — it failed: {exc}"

    return "I didn't catch which thing to run."


def _human(intent: str) -> str:
    return {
        "scout_leads": "the lead scout",
        "scout_probate": "probate",
        "outreach": "outreach",
        "run_engines": "the engines",
        "research": "research",
    }.get(intent, intent)


__all__ = ["is_action", "classify", "run"]

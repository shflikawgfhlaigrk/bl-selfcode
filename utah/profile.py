"""Profile + librarian capability — Ace's user_profile/librarian transition here (NOT agents):
remember durable facts about Michael (source='user', decay-protected) and browse what Utah
knows. Memory-backed (Postgres), real, ungated. Both boundaries are injectable for tests and
NEVER raise into their callers: blank input is refused before it can pollute memory (the
mic-noise confabulation guard), and a dead backend degrades to an honest empty/error result.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.profile")

#: Upper bound on a librarian browse — recall fans out across lanes per hit requested,
#: and no caller (deck, voice) ever renders more than a page.
MAX_BROWSE_K = 50
#: What a browse falls back to when the caller's ``k`` is junk (the deck passes
#: query-string values) — one honest page rather than a raise or a zero.
DEFAULT_BROWSE_K = 8


def is_blank(text: str | None) -> bool:
    """Return True when *text* is None or only whitespace."""
    return text is None or not text.strip()


def _clamp_k(k) -> int:
    """Coerce *k* to an int in ``1..MAX_BROWSE_K``; junk (None, 'garbage') degrades to
    ``DEFAULT_BROWSE_K``. int('garbage') used to raise BEFORE the browse boundary try."""
    try:
        k = int(k)
    except (TypeError, ValueError):
        k = DEFAULT_BROWSE_K
    return max(1, min(k, MAX_BROWSE_K))


def _row(hit) -> dict:
    """One librarian row from one recall hit — degrades PER HIT (a single malformed
    score must not blank the whole shelf): junk score → 0.0, content → str always."""
    try:
        score = round(float(getattr(hit, "score", 0.0) or 0.0), 2)
    except (TypeError, ValueError):
        score = 0.0
    content = getattr(hit, "content", None)
    return {"content": str(hit) if content is None else str(content), "score": score}


def remember_profile(fact: str, *, store=None) -> dict:
    """Store a durable profile fact about Michael (high confidence, source='user').

    Never raises. A blank *fact* is refused before it reaches memory — storing
    mic-noise as durable identity facts is the exact confabulation failure the
    memory gate exists to prevent. A store failure is documented to the failure
    log and reported as ``{"stored": False, "error": ...}``.
    """
    if is_blank(fact):
        return {"stored": False, "error": "blank fact — nothing to remember"}
    if store is None:
        from utah import memory
        store = lambda f: memory.store(f, source="user", confidence=0.9)  # noqa: E731
    try:
        res = store(fact)
        return {"stored": True, "id": getattr(res, "id", None)}
    except Exception as exc:  # noqa: BLE001 — boundary: any backend failure reports, never raises
        from utah import failures
        failures.record("profile", "store_failed", str(exc))
        return {"stored": False, "error": str(exc)}


def browse(query: str, *, k: int = 8, recall=None) -> list[dict]:
    """Librarian: browse what Utah knows about *query* (recall from memory).

    Never raises: a blank query is an empty shelf (no recall fan-out), ``k`` is
    coerced + clamped to ``1..MAX_BROWSE_K`` (junk → ``DEFAULT_BROWSE_K``), a hit
    with a malformed score degrades PER HIT to score 0.0 (one bad row never blanks
    the shelf), and a dead recall backend degrades to ``[]`` with a logged warning.
    The deck renders an empty shelf, never a stack trace.
    """
    if is_blank(query):
        return []
    k = _clamp_k(k)
    if recall is None:
        from utah import memory
        recall = memory.recall
    try:
        hits = recall(query, k) or []
        return [_row(h) for h in hits]
    except Exception as exc:  # noqa: BLE001 — boundary: browse degrades, never breaks the caller
        log.warning("profile.browse degraded to empty for %r: %s", query[:60], exc)
        return []


__all__ = ["remember_profile", "browse", "is_blank", "MAX_BROWSE_K", "DEFAULT_BROWSE_K"]

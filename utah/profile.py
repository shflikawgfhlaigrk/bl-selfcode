"""Profile + librarian capability — Ace's user_profile/librarian transition here (NOT agents):
remember durable facts about Michael (source='user', decay-protected) and browse what Utah
knows. Memory-backed (Postgres), real, ungated. Both injectable for tests.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.profile")


def remember_profile(fact: str, *, store=None) -> dict:
    """Store a durable profile fact about Michael (high confidence, source='user')."""
    if store is None:
        from utah import memory
        store = lambda f: memory.store(f, source="user", confidence=0.9)  # noqa: E731
    try:
        res = store(fact)
        return {"stored": True, "id": getattr(res, "id", None)}
    except Exception as exc:  # noqa: BLE001
        from utah import failures
        failures.record("profile", "store_failed", str(exc))
        return {"stored": False, "error": str(exc)}


def browse(query: str, *, k: int = 8, recall=None) -> list[dict]:
    """Librarian: browse what Utah knows about *query* (recall from memory)."""
    if recall is None:
        from utah import memory
        recall = memory.recall
    hits = recall(query, k) or []
    return [{"content": getattr(h, "content", str(h)),
             "score": round(getattr(h, "score", 0.0), 2)} for h in hits]


def is_blank(text: str | None) -> bool:
    """Return True when *text* is None or only whitespace."""
    return text is None or not text.strip()


__all__ = ["remember_profile", "browse", "is_blank"]

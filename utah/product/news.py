"""News capability — Ace's news transitions here (NOT an agent): pull recent news on a
topic and remember the facts. Thin, honest wrapper over the researcher capability (DDG →
fetch → grounded extraction → memory) with a news-oriented query. Real, ungated.

LIVE wire: ``utah.router`` routes news intents ("news about X", "the headlines") to
``Route.NEWS`` and ``utah.core._capability_reply`` dispatches them to :func:`answer`,
which extracts the topic and serves :func:`headlines` as a grounded CAPABILITY reply —
real web facts or an honest "couldn't pull news", never an invented headline.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("utah.product.news")

#: The query subject when a bare news request ("the headlines") names no topic.
FALLBACK_TOPIC = "today's top stories"

#: Pulls the subject out of a news turn: "news about X" / "headlines for X" /
#: "what's happening with X". Mirrors the route patterns in ``utah.router._NEWS``.
_TOPIC = re.compile(
    r"(?:news|headlines?)\s+(?:about|on|for|regarding)\s+(?P<after_news>.+)|"
    r"what(?:'?s|\s+is)\s+happening\s+with\s+(?P<after_happening>.+)",
    re.I,
)


def extract_topic(text: str) -> str:
    """The news subject in *text* ("latest news on tesla" → "tesla"), with trailing
    punctuation and a leading article stripped; :data:`FALLBACK_TOPIC` when the turn
    names no subject ("give me the headlines") — never an empty query."""
    m = _TOPIC.search(text or "")
    topic = next((g for g in (m.groups() if m else ()) if g), "")
    topic = re.sub(r"[\s?.!]+$", "", topic).strip()
    topic = re.sub(r"^the\s+", "", topic, flags=re.I).strip()
    return topic or FALLBACK_TOPIC


def headlines(topic: str, *, k: int = 3, research=None) -> dict:
    """Research recent news on *topic* and store the facts in memory. ``research`` is
    injectable (defaults to researcher.research). Returns the research result."""
    if research is None:
        from utah.product import researcher
        research = researcher.research
    return research(f"latest news about {topic}", k=k)


def answer(text: str, *, research=None) -> str:
    """A grounded, speakable reply to a news question (the ``Route.NEWS`` dispatch):
    extract the topic, pull real headlines via :func:`headlines`, and report the
    extracted facts. A blocked search / empty web / zero grounded facts degrades to an
    honest "couldn't pull news" with the reason — this lane exists precisely so a model
    NEVER invents a headline. Never raises (capability boundary)."""
    topic = extract_topic(text)
    try:
        result = headlines(topic, research=research) or {}
    except Exception as exc:  # noqa: BLE001 — a research blowup must never crash a turn
        log.warning("news: research failed for %r: %s", topic, exc)
        result = {"sources": 0, "facts": 0, "stored": 0, "error": str(exc)}
    facts = [f for f in result.get("fact_list") or [] if f]
    if not facts:
        reason = result.get("error") or (
            f"{result.get('sources', 0)} sources fetched, no solid facts extracted")
        log.warning("news: nothing grounded for %r (%s)", topic, reason)
        return (f"I couldn't pull real news on {topic} right now ({reason}), so I "
                "won't make up headlines — ask again in a bit.")
    bullets = "\n".join(f"- {f}" for f in facts[:5])
    return (f"Here's what's actually being reported on {topic} "
            f"({result.get('sources', 0)} sources, {result.get('stored', 0)} facts "
            f"remembered):\n{bullets}")


__all__ = ["headlines", "answer", "extract_topic", "FALLBACK_TOPIC"]

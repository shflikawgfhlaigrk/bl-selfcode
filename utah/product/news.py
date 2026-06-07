"""News capability — Ace's news transitions here (NOT an agent): pull recent news on a
topic and remember the facts. Thin, honest wrapper over the researcher capability (DDG →
fetch → grounded extraction → memory) with a news-oriented query. Real, ungated.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.product.news")


def headlines(topic: str, *, k: int = 3, research=None) -> dict:
    """Research recent news on *topic* and store the facts in memory. ``research`` is
    injectable (defaults to researcher.research). Returns the research result."""
    if research is None:
        from utah.product import researcher
        research = researcher.research
    return research(f"latest news about {topic}", k=k)


__all__ = ["headlines"]

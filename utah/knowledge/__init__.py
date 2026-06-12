"""Curated, durable knowledge packs seeded into memory as grounded facts.

These are *grounding*, not model recall: the local quick model half-hallucinated
the Mark Douglas rules in testing, so the real content is admitted as
``source="fact"`` and served by recall. Idempotent (memory dedup).

:data:`PACKS` enumerates every pack; :func:`answer` fans a query across them and
returns the first verbatim match (``None`` when no pack claims the query — the
router falls through to the next tier). Submodules resolve lazily (PEP 562) so
importing the package costs nothing until a pack is actually consulted, and one
broken pack degrades to no-match instead of crashing the knowledge route.
"""
from __future__ import annotations

import logging
from importlib import import_module
from types import ModuleType

log = logging.getLogger("utah.knowledge")

#: Every curated pack, by submodule name. Each pack exposes ``matches(query)`` and
#: ``answer(query) -> str | None`` (verbatim text, never a model's recall).
PACKS: tuple[str, ...] = ("douglas",)

__all__ = ["PACKS", "answer", *PACKS]


def answer(query: str | None) -> str | None:
    """The first pack's verbatim answer for *query*, else ``None`` (no pack claims
    it — the caller escalates to the next tier). A pack that fails to import or
    answer is logged and skipped: one bad pack never takes down the route."""
    if not query:
        return None
    for name in PACKS:
        try:
            pack = import_module(f".{name}", __name__)
            out = pack.answer(query)
        except Exception as exc:  # noqa: BLE001 — boundary: degrade to no-match
            log.warning("knowledge pack %r failed (skipped): %s", name, exc)
            continue
        if out:
            return out
    return None


def __getattr__(name: str) -> ModuleType:
    """Lazy pack access — ``utah.knowledge.douglas`` imports on first touch."""
    if name in PACKS:
        return import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

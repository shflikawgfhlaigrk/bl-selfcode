"""Hot-loaded agent: free space on the main volume.

A self-contained capability the daemon hot-loads from ``agents/``. It answers
disk / storage / free-space questions with the REAL number from
``shutil.disk_usage("/")`` — never a model guess — and declines everything else
by returning ``None`` so it can never hijack general chat.

Contract (shared by every hot-loaded agent):
  * ``KEYWORDS`` — the trigger vocabulary the loader may surface or index.
  * ``run(ctx)`` — answer the turn, or return ``None`` to decline (pass through).

``ctx`` is whatever the dispatcher hands an agent: a bare string, a mapping with
a ``text``/``message``/``query`` field, or an object exposing one of those as an
attribute. We read the turn defensively so the caller's shape never matters.

Stdlib only, single file by design.
"""
from __future__ import annotations

import re
import shutil
from typing import Any

#: Trigger vocabulary — disk / storage / free-space intent. Every phrase already
#: implies storage, so a bare "space"/"room" in ordinary chat does NOT match:
#: this agent must decline anything it isn't sure about rather than hijack it.
KEYWORDS: tuple[str, ...] = (
    "disk space",
    "disk usage",
    "disk free",
    "free disk",
    "disk capacity",
    "free space",
    "space free",
    "space left",
    "space remaining",
    "remaining space",
    "available space",
    "out of space",
    "running out of space",
    "storage space",
    "storage left",
    "free storage",
    "available storage",
    "how much space",
    "how much storage",
    "how much disk",
    "how much free",
    "drive space",
    "hard drive space",
)

#: Word-boundary trigger built from KEYWORDS. Plain substring matching would let
#: "interspace" or "spaceship" fire the agent; the \b anchors keep it honest.
_TRIGGER = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(k) for k in KEYWORDS),
    re.I,
)

_GB = 1024 ** 3
#: Common ``ctx`` field/attribute names the dispatcher might carry the turn in.
_TEXT_FIELDS = ("text", "message", "query", "prompt", "input", "content")


def _text(ctx: Any) -> str:
    """Pull the user's turn out of whatever shape ``ctx`` arrives in."""
    if ctx is None:
        return ""
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, dict):
        for key in _TEXT_FIELDS:
            val = ctx.get(key)
            if isinstance(val, str) and val:
                return val
        return ""
    for attr in _TEXT_FIELDS:
        val = getattr(ctx, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


def matches(text: str) -> bool:
    """True iff the turn is a disk / storage / free-space question."""
    return bool(_TRIGGER.search(text or ""))


def run(ctx: Any) -> str | None:
    """Answer a disk/storage question with the REAL free space on ``/``.

    Returns a one-line GB summary on a match, or ``None`` to decline — so the
    dispatcher falls through to general chat — when the turn isn't about disk
    space. The agent never hijacks an unrelated question.
    """
    if not matches(_text(ctx)):
        return None

    usage = shutil.disk_usage("/")
    free_gb = usage.free / _GB
    total_gb = usage.total / _GB
    used_gb = usage.used / _GB
    pct_free = (usage.free / usage.total * 100.0) if usage.total else 0.0

    return (
        f"💾 Main volume (/): {free_gb:.1f} GB free "
        f"of {total_gb:.1f} GB ({used_gb:.1f} GB used, {pct_free:.0f}% free)."
    )

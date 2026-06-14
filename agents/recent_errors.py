"""Hot-loaded agent: recent ERROR / WARNING lines across the daemon logs.

A self-contained capability the daemon hot-loads from ``agents/``. It answers
"what's broken / recent errors / what is failing / show me errors / any errors"
questions with the REAL tail of ``~/.utah/logs/*.err`` and ``*.log`` — never a
model guess — and declines everything else by returning ``None`` so it can never
hijack general chat.

What it does on a match: gather the recent ERROR / WARNING lines from the tail
of every log, keep the last ~30, collapse near-duplicate lines (timestamps,
counters, hex/UUIDs masked out) into counts, and return a short plain-text
summary — most frequent first, no asterisks.

Contract (shared by every hot-loaded agent):
  * ``KEYWORDS`` — the trigger vocabulary the loader may surface or index.
  * ``run(ctx)`` — answer the turn, or return ``None`` to decline (pass through).

``ctx`` is whatever the dispatcher hands an agent: a bare string, a mapping with
a ``text``/``message``/``query`` field, or an object exposing one of those as an
attribute. We read the turn defensively so the caller's shape never matters.

Stdlib only (glob, pathlib, re, collections), single file by design.
"""
from __future__ import annotations

import glob
import pathlib
import re
from collections import Counter, OrderedDict
from typing import Any

#: Trigger vocabulary — "what is broken / failing / recent errors" intent. Every
#: phrase is multi-word and already implies a system-health question; the bare
#: word "error" is deliberately NOT here so ordinary chat ("how do I handle
#: errors in Python") never fires this agent. Apostrophes are stripped from both
#: the turn and these keywords before matching, so "what's broken" hits
#: "whats broken". Keep entries lowercase and apostrophe-free.
KEYWORDS: tuple[str, ...] = (
    "recent errors",
    "recent error",
    "latest errors",
    "any errors",
    "any error",
    "any warnings",
    "any warning",
    "show me errors",
    "show me the errors",
    "show errors",
    "show me whats broken",
    "whats broken",
    "what is broken",
    "what broke",
    "anything broken",
    "is anything broken",
    "whats failing",
    "what is failing",
    "what failed",
    "anything failing",
    "what went wrong",
    "errors in the logs",
    "error logs",
    "log errors",
    "any log errors",
    "system errors",
)

#: Word-boundary trigger built from KEYWORDS. Plain substring matching would let
#: an unrelated phrase fire the agent; the \b anchors keep it honest. Matching is
#: done against an apostrophe-stripped copy of the turn (see ``_clean``).
_TRIGGER = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(k) for k in KEYWORDS),
    re.I,
)

#: Lines worth surfacing: anything mentioning ERROR or WARNING as a whole word.
_LEVEL = re.compile(r"\b(?:ERROR|WARNING)\b", re.I)

#: Common ``ctx`` field/attribute names the dispatcher might carry the turn in.
_TEXT_FIELDS = ("text", "message", "query", "prompt", "input", "content")

_LOG_DIR = pathlib.Path("~/.utah/logs").expanduser()
_GLOBS = ("*.err", "*.log")
_MAX_LINES = 30          # how many recent ERROR/WARNING lines to summarize
_TAIL_BYTES = 256 * 1024  # how much of each log's end to scan
_TOP_GROUPS = 8          # how many collapsed groups to print
_SAMPLE_WIDTH = 160      # max width of a printed sample line


def _clean(text: str) -> str:
    """Lowercase-fold apostrophes out of the turn for keyword matching."""
    return (text or "").replace("’", "").replace("'", "")


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
    """True iff the turn is a "what's broken / recent errors" question."""
    return bool(_TRIGGER.search(_clean(text)))


def _tail_lines(path: pathlib.Path) -> list[str]:
    """Return the last ``_TAIL_BYTES`` of ``path`` as decoded lines.

    Reads from the end so a multi-megabyte log costs only a small seek+read.
    Any unreadable file (gone, permissions, binary) yields no lines.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            chunk = fh.read()
    except OSError:
        return []
    text = chunk.decode("utf-8", "replace")
    if size > _TAIL_BYTES:
        # First slice is probably a partial line — drop it.
        text = text.split("\n", 1)[-1]
    return text.splitlines()


def _collect() -> list[tuple[str, str]]:
    """Gather recent ``(source, line)`` ERROR/WARNING pairs, oldest → newest.

    Files are ordered by modification time so the combined tail approximates
    global recency; we then keep only the last ``_MAX_LINES`` pairs.
    """
    paths: dict[str, pathlib.Path] = {}
    for pattern in _GLOBS:
        for hit in glob.glob(str(_LOG_DIR / pattern)):
            paths[hit] = pathlib.Path(hit)

    def _mtime(p: pathlib.Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    pairs: list[tuple[str, str]] = []
    for path in sorted(paths.values(), key=_mtime):
        source = path.name
        for line in _tail_lines(path):
            if _LEVEL.search(line):
                stripped = line.strip()
                if stripped:
                    pairs.append((source, stripped))
    return pairs[-_MAX_LINES:]


def _key(line: str) -> str:
    """Normalize a line so near-duplicates collapse to one count.

    Strips a leading timestamp / ``[tag]`` prefix and masks the parts that vary
    run-to-run — hex addresses, UUIDs, and every bare number — so the same error
    with a different pid / offset / time keys identically.
    """
    s = line
    s = re.sub(r"^\W*\d{4}-\d{2}-\d{2}[ T][\d:,.]+\S*\s*", "", s)  # ISO timestamp
    s = re.sub(r"^\s*\[[^\]]*\]\s*", "", s)                        # leading [tag]
    s = re.sub(r"0x[0-9a-fA-F]+", "0x?", s)                        # hex address
    s = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "<uuid>",
        s,
    )
    s = re.sub(r"\d+", "#", s)                                     # any number
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _summarize(pairs: list[tuple[str, str]]) -> str:
    """Collapse ``pairs`` into a most-frequent-first plain-text summary."""
    counts: Counter[str] = Counter()
    sample: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
    for source, line in pairs:
        key = _key(line)
        counts[key] += 1
        sample[key] = (source, line)  # keep the most recent occurrence

    n_files = len({src for src, _ in pairs})
    header = (
        f"Recent errors — {len(pairs)} ERROR/WARNING line"
        f"{'' if len(pairs) == 1 else 's'} across {n_files} log"
        f"{'' if n_files == 1 else 's'} "
        f"({len(counts)} distinct):"
    )

    lines = [header]
    # Most frequent first; ties broken by most-recent occurrence (insertion order
    # in ``sample`` is oldest→newest, so reverse the index for recency).
    order = list(sample.keys())
    recency = {key: i for i, key in enumerate(order)}
    ranked = sorted(counts, key=lambda k: (counts[k], recency[k]), reverse=True)
    for key in ranked[:_TOP_GROUPS]:
        source, line = sample[key]
        text = line if len(line) <= _SAMPLE_WIDTH else line[: _SAMPLE_WIDTH - 1] + "…"
        lines.append(f"  {counts[key]}x  [{source}] {text}")

    extra = len(ranked) - _TOP_GROUPS
    if extra > 0:
        lines.append(f"  …and {extra} more distinct line{'' if extra == 1 else 's'}.")
    return "\n".join(lines)


def run(ctx: Any) -> str | None:
    """Answer a "what's broken / recent errors" question from the REAL logs.

    Returns a short plain-text summary on a match, or ``None`` to decline — so
    the dispatcher falls through to general chat — when the turn isn't about
    recent errors. The agent never hijacks an unrelated question.
    """
    if not matches(_text(ctx)):
        return None

    pairs = _collect()
    if not pairs:
        return (
            f"No ERROR or WARNING lines in the recent tail of "
            f"{_LOG_DIR}/*.err and *.log — logs look clean."
        )
    return _summarize(pairs)

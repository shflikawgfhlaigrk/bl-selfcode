"""Lightweight entity extraction for the GraphRAG boost (proper-noun spans).

This is the cheap, free hot-path extractor: capitalized spans minus a noise
list, with leading/trailing noise tokens stripped from multi-word spans so that
sentence-position capitals ("Where Does Michael ...") don't poison the graph.
Brain-assisted extraction is the future upgrade; the supersede entity-path and
the recall boost both run on this today, so it must be deterministic and total
(never raises on any string input).
"""
from __future__ import annotations

import re

_CAP = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*)\b")

#: Capitalized words that are sentence machinery, not entities.
_NOISE = frozenset(
    {
        "A", "An", "The", "I", "It", "He", "She", "They", "We", "You",
        "My", "Your", "His", "Her", "Its", "Our", "Their", "Me", "Him", "Them",
        "This", "That", "These", "Those",
        "What", "Who", "Whom", "Whose", "Which", "Where", "When", "Why", "How",
        "Is", "Are", "Was", "Were", "Be", "Been", "Am",
        "Do", "Does", "Did", "Done",
        "Can", "Could", "Will", "Would", "Should", "Shall", "May", "Might", "Must",
        "Yes", "No", "Not", "Ok", "OK", "Okay",
        "And", "Or", "But", "If", "So", "Then", "Now", "Also", "Just",
        "Please", "Remember", "Tell", "Note", "Hi", "Hello", "Hey",
        "Thanks", "Thank", "Q", "Q:", "Answer",
    }
)


def _strip_noise(tokens: list[str]) -> list[str]:
    """Drop noise tokens from both ends of a capitalized span."""
    start, end = 0, len(tokens)
    while start < end and tokens[start] in _NOISE:
        start += 1
    while end > start and tokens[end - 1] in _NOISE:
        end -= 1
    return tokens[start:end]


def extract(text: str) -> list[str]:
    """Return unique entity names found in *text*, in order of appearance.

    Single noise words are dropped; multi-word spans are trimmed of leading and
    trailing noise tokens ("Remember Michael" -> "Michael"). Single-character
    leftovers are discarded. Never raises.
    """
    if not text:
        return []
    out: list[str] = []
    for match in _CAP.finditer(text):
        tokens = _strip_noise(match.group(1).split())
        if not tokens:
            continue
        name = " ".join(tokens)
        if len(name) > 1:
            out.append(name)
    return list(dict.fromkeys(out))  # de-dup, keep order


def normalize(name: str) -> str:
    """Canonical comparison form of an entity name (case/whitespace folded)."""
    return " ".join(name.split()).casefold()


def normalized_set(text: str) -> set[str]:
    """Normalized entity names of *text* — the form used for graph matching."""
    return {normalize(e) for e in extract(text)}

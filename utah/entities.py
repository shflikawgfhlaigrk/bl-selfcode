"""Lightweight entity extraction for the GraphRAG boost (proper-noun spans).

This is the cheap, free hot-path extractor: capitalized spans minus a noise
list, with leading/trailing noise tokens stripped from multi-word spans so that
sentence-position capitals ("Where Does Michael ...") don't poison the graph.
Brain-assisted extraction is the future upgrade; the supersede entity-path and
the recall boost both run on this today, so it must be deterministic and total
(never raises on any string input).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("utah.entities")

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


#: Pluggable extractor — defaults to the regex proper-noun pass. A real NER (spaCy, or a
#: brain-assisted pass) drops in here via :func:`set_extractor` to de-noise the graph,
#: WITHOUT a heavy model dep on the hot path by default (the spec's no-torch/no-spacy line).
#: One-line upgrade, no caller changes — recall/supersede/eval all run through ``extract``.
_extractor = None


def set_extractor(fn) -> None:
    """Inject a real NER (``fn(text) -> list[str]``). ``None`` restores the regex default."""
    global _extractor
    _extractor = fn


def _regex_extract(text: str) -> list[str]:
    out: list[str] = []
    for match in _CAP.finditer(text):
        tokens = _strip_noise(match.group(1).split())
        if not tokens:
            continue
        name = " ".join(tokens)
        if len(name) > 1:
            out.append(name)
    return list(dict.fromkeys(out))  # de-dup, keep order


def extract(text: str) -> list[str]:
    """Return unique entity names found in *text*, in order of appearance.

    Uses the injected extractor (a real NER) when one is set, else the regex pass: single
    noise words dropped; multi-word spans trimmed of leading/trailing noise tokens
    ("Remember Michael" -> "Michael"); single-char leftovers discarded. Never raises — a
    custom extractor that throws falls back to the regex pass so the graph never breaks.
    """
    if not text:
        return []
    if _extractor is not None:
        try:
            return list(dict.fromkeys(e for e in (_extractor(text) or []) if e and len(e) > 1))
        except Exception:  # noqa: BLE001 — a bad NER must never break recall/supersede
            log.warning("injected entity extractor failed — falling back to regex pass",
                        exc_info=True)
    return _regex_extract(text)


def normalize(name: str) -> str:
    """Canonical comparison form of an entity name (case/whitespace folded)."""
    return " ".join(name.split()).casefold()


def normalized_set(text: str) -> set[str]:
    """Normalized entity names of *text* — the form used for graph matching."""
    return {normalize(e) for e in extract(text)}

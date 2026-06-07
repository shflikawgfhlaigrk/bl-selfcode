"""Social fast-path — deterministic instant replies for greetings/acks/chitchat.

A pure "hello", "thanks", or "good night" carries no task and asserts no fact, so
it must never pay a model round-trip. Measured before this path: "hello" → 9.6 s
(local 3B cold-load), "thanks" → 23 s (escalated to the Claude brain). Here the
reply is canned (no model, no memory write, no fabrication) and returns in
microseconds.

Two safety rules:
* **Whole-message match.** The matchers are anchored to the entire (trimmed)
  message, so "hello, can you debug X" is NOT social — it still routes to the brain.
* **No ambiguous answers.** Bare "yes"/"no"/"ok" usually *continue* a thread
  (answering the assistant), so they are deliberately excluded — hijacking them
  with a canned "got it" would break continuity.

Variation without RNG: a stable hash of the message picks among a category's
replies, so the path is deterministic (test- and resume-stable) yet not robotic.
"""
from __future__ import annotations

import hashlib
import re

_EDGE = r"[\s!.,?…~❤️👍🙏]*"          # leading/trailing whitespace, punctuation, emoji


def _whole(*alts: str) -> re.Pattern[str]:
    """A matcher anchored to the WHOLE trimmed message (edge punctuation allowed)."""
    return re.compile(rf"^{_EDGE}(?:{'|'.join(alts)}){_EDGE}$", re.I)


# category -> whole-message matcher. Ordered; first match wins in :func:`classify`.
_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greeting", _whole(
        r"hi+", r"hey+", r"hello+", r"yo", r"sup", r"hiya", r"howdy", r"heya",
        r"good\s+morning", r"good\s+afternoon", r"good\s+evening",
        r"morning", r"evening", r"greetings",
        r"(hi|hey|hello|yo)\s+ace", r"ace")),
    ("howareyou", _whole(
        r"how\s+are\s+(you|ya|things\s+with\s+you)", r"how\s+are\s+you\s+doing",
        r"how'?s\s+it\s+going", r"how'?re\s+you", r"how\s+you\s+doing",
        r"what'?s\s+up", r"whats\s+up", r"you\s+(there|up|awake|alive|good)")),
    ("thanks", _whole(
        r"thanks?", r"thank\s+you", r"thanks?\s+so\s+much", r"thank\s+you\s+so\s+much",
        r"thx", r"ty", r"cheers", r"much\s+appreciated", r"appreciate\s+it",
        r"appreciated", r"thanks?\s+(a\s+lot|man|dude|bud|buddy)")),
    ("farewell", _whole(
        r"bye+", r"goodbye", r"see\s+ya", r"see\s+you( later)?", r"later", r"cya",
        r"good\s*night", r"night", r"gn", r"talk\s+(to\s+you\s+)?later", r"ttyl",
        r"catch\s+you\s+later", r"peace")),
    ("ack", _whole(
        r"cool", r"nice", r"great", r"awesome", r"perfect", r"sweet", r"excellent",
        r"got\s+it", r"gotcha", r"sounds\s+good", r"sg", r"makes\s+sense",
        r"np", r"no\s+problem", r"will\s+do", r"roger", r"copy\s+that")),
)

_REPLIES: dict[str, tuple[str, ...]] = {
    "greeting": ("Hey — what do you need?", "Hi Michael. What's up?",
                 "Hey. What can I do?"),
    "howareyou": ("Good — here and listening. What do you need?",
                  "All good. What's up?", "Running fine. What can I do for you?"),
    "thanks": ("Anytime.", "You got it.", "Sure thing."),
    "farewell": ("Later.", "Talk soon.", "See ya."),
    "ack": ("Got it.", "👍", "Cool."),
}


def classify(text: str) -> str | None:
    """The social category of a whole-message turn, or ``None`` if it isn't social."""
    t = (text or "").strip()
    if not t:
        return None
    for name, pattern in _CATEGORIES:
        if pattern.match(t):
            return name
    return None


def matches(text: str) -> bool:
    """True if *text* is a whole-message social turn (greeting/ack/thanks/etc.)."""
    return classify(text) is not None


def reply(text: str) -> str | None:
    """A deterministic canned reply for a social turn, or ``None`` if not social.
    No model, no fact, no memory write — just a pleasantry, in microseconds."""
    cat = classify(text)
    if cat is None:
        return None
    options = _REPLIES[cat]
    # Stable, RNG-free variation: pick by a hash of the normalized message.
    idx = int(hashlib.sha1(text.strip().lower().encode()).hexdigest(), 16) % len(options)
    return options[idx]


__all__ = ["classify", "matches", "reply"]

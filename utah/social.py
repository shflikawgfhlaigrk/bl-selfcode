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

# Optional praise after "thanks ace" — whole-message only, so "thanks ace, debug X" stays
# a normal turn. Covers J-049 ("Thanks ace, you're the best" → SOCIAL, not CORE recall).
_ACE_PRAISE = (
    r"you'?re\s+(the\s+)?(best|greatest|awesome|amazing|goated|the\s+goat|a\s+legend|so\s+good)"
    r"|(?:i\s+)?(?:love|appreciate)\s+you"
    r"|so\s+much|a\s+lot|man|dude|bud|buddy|mate|bro"
)
_ACE_THANKS_TAIL = rf"(?:[\s,!.?…~❤️👍🙏]+(?:{_ACE_PRAISE}))?[\s!.?…~❤️👍🙏]*"


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
        r"appreciated", r"thanks?\s+(a\s+lot|man|dude|bud|buddy)",
        rf"thanks?\s+ace{_ACE_THANKS_TAIL}",
        rf"thank\s+you(?:\s+so\s+much)?\s+ace{_ACE_THANKS_TAIL}",
        r"thx\s+ace", r"ty\s+ace")),
    ("farewell", _whole(
        r"bye+", r"goodbye", r"see\s+ya", r"see\s+you( later)?", r"later", r"cya",
        r"good\s*night", r"night", r"gn", r"talk\s+(to\s+you\s+)?later", r"ttyl",
        r"catch\s+you\s+later", r"peace")),
    ("ack", _whole(
        r"cool", r"nice", r"great", r"awesome", r"perfect", r"sweet", r"excellent",
        r"got\s+it", r"gotcha", r"sounds\s+good", r"sg", r"makes\s+sense",
        r"np", r"no\s+problem", r"will\s+do", r"roger", r"copy\s+that")),
)

#: Greeting templates carry a ``{sal}`` slot filled with a time-of-day salutation —
#: a small human touch (a person knows whether it's morning or night) and warmer than
#: a flat "what do you need?". Addressed to Michael by name.
_GREETING_TEMPLATES: tuple[str, ...] = (
    "{sal}, Michael. What are we hitting?",
    "{sal}, Michael — what's up?",
    "{sal}. What do you need?",
)

_REPLIES: dict[str, tuple[str, ...]] = {
    "howareyou": ("Good — locked in with you. What's up?",
                  "All good here. What are we hitting?", "Solid. What do you need?"),
    "thanks": ("Anytime.", "You got it.", "Course — that's what I'm here for."),
    "farewell": ("Later, Michael.", "Talk soon.", "Go get 'em."),
    "ack": ("Got it.", "👍", "On it."),
}


def _salutation(hour: int) -> str:
    """The time-of-day salutation for *hour* (0–23, Michael's timezone)."""
    if 5 <= hour < 12:
        return "Morning"
    if 12 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 22:
        return "Evening"
    return "Hey"  # late night / very early — a salutation would be odd


def _now_hour() -> int:
    """The current hour in Michael's timezone; system-local on any failure."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from utah import config

    try:
        return datetime.now(ZoneInfo(config.TIMEZONE)).hour
    except Exception:  # noqa: BLE001 — bad tz / clock: degrade to system-local
        return datetime.now().hour


def classify(text: str) -> str | None:
    """The social category of a whole-message turn, or ``None`` if it isn't social.

    Defensive on type: this sits in front of the brain on EVERY turn, so a
    non-string (whatever the transport decoded) means "not social" — it must
    never raise into the routing hot path."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t:
        return None
    for name, pattern in _CATEGORIES:
        if pattern.match(t):
            return name
    return None


def matches(text: str) -> bool:
    """True if *text* is a whole-message social turn (greeting/ack/thanks/etc.)."""
    return classify(text) is not None


#: Bare ack tokens excluded from :func:`matches` globally (they continue a thread),
#: but LOCAL_QUICK garbles them into meta-commentary when a thread is active — see J-050.
_THREADED_BARE_ACK = _whole(r"ok(?:ay)?", r"k", r"kk")


def is_threaded_bare_ack(text: str) -> bool:
    """True for minimal ack tokens safe to answer with a canned reply in-thread."""
    if not isinstance(text, str):
        return False
    t = text.strip()
    return bool(t and _THREADED_BARE_ACK.match(t))


def threaded_ack_reply(text: str) -> str | None:
    """A deterministic canned ack for :func:`is_threaded_bare_ack` — no model."""
    if not is_threaded_bare_ack(text):
        return None
    seed = int(hashlib.sha1(text.strip().lower().encode()).hexdigest(), 16)
    options = _REPLIES["ack"]
    return options[seed % len(options)]


def reply(text: str, *, hour: int | None = None) -> str | None:
    """A deterministic canned reply for a social turn, or ``None`` if not social.
    No model, no fact, no memory write — just a pleasantry, in microseconds.

    ``hour`` (0–23) selects the greeting's time-of-day salutation; defaults to the
    current hour in Michael's timezone. Injectable so the path stays test- and
    resume-stable (the only non-pure input is the clock, and it is overridable)."""
    cat = classify(text)
    if cat is None:
        return None
    # Stable, RNG-free variation: pick by a hash of the normalized message.
    seed = int(hashlib.sha1(text.strip().lower().encode()).hexdigest(), 16)
    if cat == "greeting":
        sal = _salutation(_now_hour() if hour is None else hour)
        template = _GREETING_TEMPLATES[seed % len(_GREETING_TEMPLATES)]
        return template.format(sal=sal)
    # .get + fallback: if _CATEGORIES and _REPLIES ever drift (a category added
    # without replies), degrade to a safe canned line — this runs in front of the
    # brain on EVERY turn, so a KeyError here would break the routing hot path.
    options = _REPLIES.get(cat) or ("Got it.",)
    return options[seed % len(options)]


__all__ = ["classify", "is_threaded_bare_ack", "matches", "reply", "threaded_ack_reply"]

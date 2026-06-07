"""Wake-word gate. The wake word is "ace" (or "hey ace"). A turn only fires when
the transcript contains it as a standalone token — 'face'/'place'/'space'/'ace-high'
must NOT fire. The command is the rest of the utterance with the wake word removed.

STT robustness — "ace" is a single short syllable (vowel-initial, ~300ms), the
hardest kind of token for an on-device recognizer to catch alone, so Moonshine pads
it into a plausible phrase: "save ace", "say ace", "hey ace", or pluralizes it to
"aces"/"ace's". The gate absorbs those: an optional short FILLER word immediately
before "ace" is part of the WAKE (never the command — bare "save ace" is a bare wake,
not the command "save"), and the plural/possessive tails fire too. The near-miss
guard ('face'/'place'/'space'/'ace-high') is unchanged: a filler is only consumed
when it is a whole word sitting right before a standalone "ace".
"""
from __future__ import annotations

import re

#: Words the STT commonly pads the bare wake with. A whole filler word right before
#: "ace" is swallowed into the wake so it never leaks out as a junk command.
_FILLER = r"(?:hey|hay|say|says|save|saved|ok|okay|oh|a|ay|eh)"

#: Optional filler + "ace"/"aces"/"ace's", bounded by start/whitespace on the left and
#: whitespace/terminal-punctuation/apostrophe/end on the right — a hyphen ("ace-high")
#: deliberately does NOT match, and 'face'/'place'/'space' lack the required boundary.
_WAKE = re.compile(
    rf"(?:^|\s)(?:{_FILLER}\s+)?aces?(?:['’]s)?(?=\s|['’,.!?:;]|$)",
    re.IGNORECASE,
)
_STRIP = " ,.:;!?-'’\t\n"


def extract_command(transcript: str) -> str | None:
    """Return the command (utterance minus the wake word + any STT filler padding),
    or ``None`` if the wake word is absent. ``""`` means wake fired with no command
    (bare "ace", or a mishearing like "save ace"/"aces")."""
    t = (transcript or "").strip()
    if not t:
        return None
    m = _WAKE.search(t)
    if not m:
        return None
    after = t[m.end():].lstrip(_STRIP)
    if after:
        return after
    return t[: m.start()].strip(_STRIP)  # wake at end → command precedes it

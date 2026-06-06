"""Wake-word gate. The wake word is "ace" (or "hey ace"). A turn only fires when
the transcript contains it as a standalone token — 'face'/'place'/'space'/'ace-high'
must NOT fire. The command is the rest of the utterance with the wake word removed.
"""
from __future__ import annotations

import re

# "ace"/"hey ace" bounded by start/whitespace on the left and whitespace/terminal
# punctuation/end on the right — a hyphen ("ace-high") deliberately does NOT match.
_WAKE = re.compile(r"(?:^|\s)(?:hey\s+)?ace(?=\s|[,.!?:;]|$)", re.IGNORECASE)
_STRIP = " ,.:;!?-\t\n"


def extract_command(transcript: str) -> str | None:
    """Return the command (utterance minus the wake word), or ``None`` if the
    wake word is absent. ``""`` means wake fired with no command (bare "ace")."""
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

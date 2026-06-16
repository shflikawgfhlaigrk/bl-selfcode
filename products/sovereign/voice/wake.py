"""Wake-word gate. The wake word is "ace" / "utah" (or "hey ace", "hey utah"). A turn
only fires when the transcript contains it as a standalone token — 'face'/'place'/
'space'/'ace-high' must NOT fire. The command is the rest of the utterance with the
wake word removed.

STT robustness — "ace" is a single short syllable (vowel-initial, ~300ms), the
hardest kind of token for an on-device recognizer to catch alone, so Moonshine pads
it into a plausible phrase: "save ace", "say ace", "hey ace", or pluralizes it to
"aces"/"ace's". The gate absorbs those: an optional short FILLER word immediately
before "ace" is part of the WAKE (never the command — bare "save ace" is a bare wake,
not the command "save"), and the plural/possessive tails fire too. The near-miss
guard ('face'/'place'/'space'/'ace-high') is unchanged: a filler is only consumed
when it is a whole word sitting right before a standalone "ace".

Moonshine also drops the wake entirely and mishears the leading syllable as "is"
("ace, what's the weather" → "is what's the weather"). A conservative leading-"is"
fallback fires only when the remainder starts with a clear command verb.
"""
from __future__ import annotations

import re

#: Words the STT commonly pads the bare wake with. A whole filler word right before
#: "ace" is swallowed into the wake so it never leaks out as a junk command.
_FILLER = r"(?:hey|hay|say|says|save|saved|ok|okay|oh|a|ay|eh)"

#: Optional filler + wake name ("ace"/"utah" + common STT plural/possessive tails).
_WAKE_NAMES = r"aces?(?:['’]s)?|utah"
_WAKE = re.compile(
    rf"(?:^|\s)(?:{_FILLER}\s+)?(?:{_WAKE_NAMES})(?=\s|['’,.!?:;]|$)",
    re.IGNORECASE,
)
#: Leading "is" mishearing when Moonshine drops "ace" but the user clearly asked a question.
_MISHEARD_IS = re.compile(
    r"^is\s+(what|how|who|when|where|why|tell|give|summarize|check|show|read)\b",
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
        mm = _MISHEARD_IS.match(t)
        if mm:
            return t[mm.start(1):].lstrip(_STRIP)  # treat leading "is" as dropped wake
        return None
    after = t[m.end():].lstrip(_STRIP)
    if after:
        return after
    return t[: m.start()].strip(_STRIP)  # wake at end → command precedes it


def resolve_command(transcript: str, *, audio_wake: bool = False,
                    wake_confidence: float | None = None,
                    button_barge: bool = False) -> str | None:
    """Stage B wake gate — decide whether an utterance is addressed to Utah.

    * ``audio_wake=False`` (default): transcript regex only — room speech ignored.
    * ``audio_wake=True``: openWakeWord armed this segment (Stage A). Command must
      contain a literal "ace"/"utah" token (ace-only mode after 2026-06-12) — a
      high ``wake_confidence`` does NOT waive the token, so room speech that slips
      past Stage A is still dropped at Stage B. An empty transcript is a bare wake.
    * ``button_barge=True``: deck ◼ BARGE armed capture — accept speech without ace.
    """
    cmd = extract_command(transcript)
    if cmd is not None:
        return cmd
    if button_barge:
        t = (transcript or "").strip()
        return t if t else None
    if not audio_wake:
        return None
    t = (transcript or "").strip()
    if not t:
        return ""  # bare audio wake — orb pulse only
    return None


__all__ = ["extract_command", "resolve_command"]

"""Wake-word gate: the transcript must contain "ace" / "hey ace" as a WORD
(not 'face'/'place'/'space'); the command is what the user said with the wake
word stripped. No wake -> no turn (the mic never acts on un-addressed speech)."""
from __future__ import annotations

from utah.voice import wake


def test_command_after_wake():
    assert wake.extract_command("ace what time is it") == "what time is it"


def test_hey_ace_prefix():
    assert wake.extract_command("hey ace what is project utah") == "what is project utah"


def test_wake_midsentence():
    assert wake.extract_command("okay ace how are you") == "how are you"


def test_wake_at_end_uses_preceding_command():
    assert wake.extract_command("what is the weather ace") == "what is the weather"


def test_no_wake_returns_none():
    assert wake.extract_command("tell me about the weather") is None


def test_near_miss_words_do_not_fire():
    for s in ["the face is here", "find a place to eat", "look at space", "ace-high flush"]:
        assert wake.extract_command(s) is None, s


def test_bare_wake_is_empty_command_not_none():
    assert wake.extract_command("ace") == ""
    assert wake.extract_command("hey ace") == ""


def test_case_insensitive_and_punctuation():
    assert wake.extract_command("Ace, what's the date?") == "what's the date?"


def test_empty_or_blank_is_none():
    assert wake.extract_command("") is None
    assert wake.extract_command("   ") is None

"""Social fast-path — a greeting/ack/thanks must answer instantly (no model).

Before this, "hello" cost 9.6s (local 3B cold-load) and "thanks" 23s (escalated
to the Claude brain). A pure social turn carries no task and no factual claim, so
it is answered by a deterministic canned reply in microseconds. Matching is anchored
to the WHOLE message so "hello, debug X" is NOT social, and ambiguous answers
("yes"/"no"/"ok") are deliberately excluded so they keep continuing the thread.
"""
from __future__ import annotations

import pytest

from utah import social


@pytest.mark.parametrize("t", [
    "hi", "hello", "hey", "yo", "hiya", "howdy", "hey ace", "Hello!", "  hi  ",
    "good morning", "good evening", "morning",
    "thanks", "thank you", "thanks so much", "thx", "ty", "cheers", "appreciate it",
    "thanks ace", "thank you ace", "Thanks ace, you're the best",
    "bye", "goodbye", "see ya", "good night", "later",
    "how are you", "how's it going", "what's up", "you there",
    "cool", "nice", "great", "awesome", "perfect", "got it", "sounds good", "no problem",
])
def test_social_matches(t):
    assert social.matches(t)


@pytest.mark.parametrize("t", [
    "what's the weather", "debug the parser", "who won the super bowl",
    "hello can you debug the parser", "good morning what's the weather",
    "thanks for fixing the weather bug", "explain recursion",
    "thanks ace, can you debug the parser",
    "yes", "no", "ok", "okay", "yeah", "k",   # ambiguous answers — must NOT be hijacked
])
def test_non_social_does_not_match(t):
    assert not social.matches(t)


@pytest.mark.parametrize("t", ["hi", "thanks", "bye", "how are you", "cool"])
def test_reply_is_short_nonempty_and_deterministic(t):
    r1 = social.reply(t)
    r2 = social.reply(t)
    assert r1 and isinstance(r1, str) and len(r1) <= 80
    assert r1 == r2                       # deterministic — no RNG (resume/test-stable)


@pytest.mark.parametrize("hour,word", [(8, "Morning"), (14, "Afternoon"), (20, "Evening")])
def test_greeting_is_time_of_day_aware(hour, word):
    """A greeting leads with the salutation for the actual time of day — a small human
    touch (a person knows if it's morning or night). hour is injectable for tests."""
    assert social.reply("hey", hour=hour).startswith(word)


def test_greeting_addresses_michael_by_name():
    """Warmth: a greeting is addressed to Michael, not a generic 'what do you need?'."""
    assert "Michael" in social.reply("hello", hour=9)


def test_reply_is_none_for_non_social():
    assert social.reply("debug the parser") is None
    assert social.reply("who won the 2020 world series") is None


@pytest.mark.parametrize("t", ["ok", "okay", "k", "kk", "  OK  "])
def test_threaded_bare_ack_detected(t):
    assert social.is_threaded_bare_ack(t)


@pytest.mark.parametrize("t", ["yes", "no", "cool", "got it", "hello", "ok sure"])
def test_threaded_bare_ack_not_detected(t):
    assert not social.is_threaded_bare_ack(t)


def test_threaded_ack_reply_is_short_and_deterministic():
    r1 = social.threaded_ack_reply("ok")
    r2 = social.threaded_ack_reply("ok")
    assert r1 and len(r1) <= 20
    assert r1 == r2
    assert social.threaded_ack_reply("debug the parser") is None

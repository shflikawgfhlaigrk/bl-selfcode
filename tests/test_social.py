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


def test_reply_is_none_for_non_social():
    assert social.reply("debug the parser") is None
    assert social.reply("who won the 2020 world series") is None

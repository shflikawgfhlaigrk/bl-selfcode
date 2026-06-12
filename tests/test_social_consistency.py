"""Social fast-path data consistency + hot-path guards.

classify/reply run IN FRONT of the brain on every turn (router + core), so the
category tables must stay mutually consistent, and a table gap must degrade to
a safe canned line — never a KeyError into the routing hot path.
"""
from __future__ import annotations

import pytest

from utah import social


# ── table consistency: every category resolvable to a reply ──────────────────
def test_every_category_has_a_reply_source():
    for name, _pattern in social._CATEGORIES:
        if name == "greeting":
            assert social._GREETING_TEMPLATES          # greeting rides its own templates
        else:
            assert social._REPLIES.get(name), f"category {name!r} has no replies"


def test_every_greeting_template_carries_the_salutation_slot():
    for t in social._GREETING_TEMPLATES:
        assert "{sal}" in t


def test_reply_survives_a_category_without_a_reply_table(monkeypatch):
    """If _CATEGORIES and _REPLIES ever drift (a category added without replies),
    reply() must degrade to a safe canned line — not KeyError mid-route."""
    monkeypatch.setattr(social, "_REPLIES",
                        {k: v for k, v in social._REPLIES.items() if k != "ack"})
    r = social.reply("cool")
    assert isinstance(r, str) and r


# ── matcher edges ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("t,cat", [
    ("goodnight", "farewell"),                  # \s* — no-space compound still matches
    ("hi!!! 👍", "greeting"),                   # trailing emoji/punctuation is edge noise
    ("thanks 🙏", "thanks"),
    ("Hey Ace", "greeting"),
    ("copy that.", "ack"),
])
def test_edge_punctuation_and_compounds_classify(t, cat):
    assert social.classify(t) == cat


@pytest.mark.parametrize("t", [
    "ace, fix the deck",                        # wake word + task → NOT social
    "thanks but the deck is broken",
    "good morning — run the brief",
    "hello world program in python",
])
def test_task_bearing_messages_never_hijacked(t):
    assert social.classify(t) is None
    assert social.reply(t) is None


# ── reply behavior pins ───────────────────────────────────────────────────────
def test_non_greeting_replies_ignore_the_hour():
    """Only greetings are time-of-day aware; thanks/ack/farewell are hour-stable."""
    for t in ("thanks", "cool", "bye", "how are you"):
        assert social.reply(t, hour=3) == social.reply(t, hour=15)


def test_greeting_late_night_has_no_false_salutation():
    r = social.reply("hello", hour=2)
    assert r.startswith("Hey")                  # 2am is not "Morning"


def test_all_canned_replies_are_short_single_lines():
    for options in social._REPLIES.values():
        for r in options:
            assert len(r) <= 80 and "\n" not in r
    for t in social._GREETING_TEMPLATES:
        assert len(t) <= 80 and "\n" not in t

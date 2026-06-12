"""Social fast-path coverage: category mapping, salutation boundaries, reply
membership, clock fallback, and non-string hardening.

The fast-path sits IN FRONT of the brain on every turn (router checks it
first), so a malformed input must degrade to "not social" — never raise into
the routing hot path — and every canned reply must come from the fixed,
fabrication-free reply tables.
"""
from __future__ import annotations

import pytest

from utah import social


# ── classify: category mapping (not just boolean matches) ────────────────────
@pytest.mark.parametrize("text,cat", [
    ("hello", "greeting"),
    ("good morning", "greeting"),
    ("ace", "greeting"),                 # the wake word alone is a greeting
    ("how are you", "howareyou"),
    ("what's up", "howareyou"),
    ("thanks a lot", "thanks"),
    ("ty", "thanks"),
    ("good night", "farewell"),
    ("ttyl", "farewell"),
    ("sounds good", "ack"),
    ("copy that", "ack"),
])
def test_classify_maps_to_the_right_category(text, cat):
    assert social.classify(text) == cat


@pytest.mark.parametrize("bad", [None, "", "   ", "\n\t"])
def test_classify_empty_inputs_are_not_social(bad):
    assert social.classify(bad) is None


@pytest.mark.parametrize("bad", [42, 3.14, [], {}, b"hello", object()])
def test_classify_non_string_input_never_raises(bad):
    """The router hands classify whatever the transport decoded; a non-string
    must mean 'not social', never an AttributeError in the routing hot path."""
    assert social.classify(bad) is None
    assert social.matches(bad) is False
    assert social.reply(bad) is None


# ── replies come from the fixed tables (no fabrication, no drift) ─────────────
@pytest.mark.parametrize("text,cat", [
    ("thanks", "thanks"), ("bye", "farewell"), ("how are you", "howareyou"),
    ("cool", "ack"),
])
def test_reply_is_one_of_the_canned_options(text, cat):
    assert social.reply(text) in social._REPLIES[cat]


def test_greeting_reply_is_a_filled_template():
    r = social.reply("hello", hour=9)
    assert "{sal}" not in r              # the slot is always filled
    assert r.startswith("Morning")


def test_different_texts_in_a_category_can_vary():
    """Hash-stable variation: distinct messages may select distinct replies, but
    each individual message is always answered identically."""
    replies = {social.reply(t) for t in ("thanks", "thank you", "thx", "ty", "cheers")}
    assert len(replies) >= 2             # variation exists across messages
    for t in ("thanks", "thank you", "thx"):
        assert social.reply(t) == social.reply(t)   # ... but stays per-message stable


# ── salutation boundaries (every band edge) ──────────────────────────────────
@pytest.mark.parametrize("hour,word", [
    (0, "Hey"), (4, "Hey"),              # late night — no salutation pretence
    (5, "Morning"), (11, "Morning"),
    (12, "Afternoon"), (16, "Afternoon"),
    (17, "Evening"), (21, "Evening"),
    (22, "Hey"), (23, "Hey"),
])
def test_salutation_band_edges(hour, word):
    assert social._salutation(hour) == word


def test_reply_defaults_to_the_live_clock(monkeypatch):
    """hour=None pulls the current hour in Michael's timezone via _now_hour."""
    monkeypatch.setattr(social, "_now_hour", lambda: 14)
    assert social.reply("hello").startswith("Afternoon")


def test_now_hour_degrades_to_system_local_on_bad_tz(monkeypatch):
    """A bad configured timezone must not break greetings — degrade to local."""
    from utah import config
    monkeypatch.setattr(config, "TIMEZONE", "Not/AZone", raising=False)
    h = social._now_hour()
    assert isinstance(h, int) and 0 <= h <= 23

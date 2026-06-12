"""Advisory personas: grounded brain-backed advice that degrades honestly.

The boundary contract under test: ``advise`` NEVER raises, never presents a
dead brain (None / empty reply) as an answer, and always reports which persona
actually framed the prompt.
"""
from __future__ import annotations

from utah import advisory


class _Reply:
    def __init__(self, text="Do the next rep.", source_value="brain"):
        self.text = text
        self.source = type("S", (), {"value": source_value})()


def test_every_persona_frames_the_prompt():
    for name, framing in advisory.PERSONAS.items():
        seen = {}

        def tell(prompt):
            seen["prompt"] = prompt
            return _Reply()

        r = advisory.advise(name, "what next?", tell=tell)
        assert r["persona"] == name      # known personas (incl. the lawdie alias) echo back
        assert seen["prompt"].startswith(framing)
        assert "what next?" in seen["prompt"]


def test_unknown_persona_falls_back_to_coach_and_says_so():
    """An unknown persona must not be echoed back as if it framed the answer —
    the report names the persona that ACTUALLY ran (coach)."""
    r = advisory.advise("astronaut", "ok?", tell=lambda p: _Reply())
    assert r["persona"] == "coach"


def test_persona_name_is_case_insensitive():
    seen = {}

    def tell(prompt):
        seen["prompt"] = prompt
        return _Reply()

    r = advisory.advise("PLANNER", "plan my week", tell=tell)
    assert r["persona"] == "planner"
    assert seen["prompt"].startswith(advisory.PERSONAS["planner"])


def test_none_reply_is_honest_unavailable_not_the_string_none():
    """A tell() that returns None used to surface answer='None' (str(None)) with
    source='brain' — a fabricated-looking reply from a dead lane."""
    r = advisory.advise("coach", "anything?", tell=lambda p: None)
    assert r["source"] == "unavailable"
    assert r["answer"] != "None"


def test_empty_reply_text_degrades_to_i_dont_know():
    r = advisory.advise("coach", "anything?", tell=lambda p: _Reply(text="   "))
    assert r["answer"] == "I don't know."


def test_blank_question_is_rejected_not_sent_to_the_brain():
    calls = []
    r = advisory.advise("coach", "   ", tell=lambda p: calls.append(p) or _Reply())
    assert r["source"] == "invalid"
    assert calls == []          # the brain lane is never spent on an empty question


def test_plain_string_reply_still_works():
    r = advisory.advise("concierge", "book it", tell=lambda p: "Booked for 7pm.")
    assert r["answer"] == "Booked for 7pm."
    assert r["source"] == "brain"


def test_brain_raising_is_honest_unavailable():
    def boom(p):
        raise RuntimeError("brain down")

    r = advisory.advise("navigator", "route?", tell=boom)
    assert r["source"] == "unavailable"
    assert "unavailable" in r["answer"]

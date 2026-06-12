"""Advisory input gates beyond tests/test_advisory.py: persona resolution and
the invalid-question gate compose correctly, and odd reply shapes stay honest."""
from __future__ import annotations

from utah import advisory


class _Reply:
    def __init__(self, text, source_value="brain"):
        self.text = text
        self.source = type("S", (), {"value": source_value})()


def test_none_persona_resolves_to_coach():
    r = advisory.advise(None, "next move?", tell=lambda p: _Reply("Lift."))
    assert r["persona"] == "coach"
    assert r["answer"] == "Lift."


def test_blank_question_with_unknown_persona_reports_coach_and_invalid():
    calls: list = []
    r = advisory.advise("warlock", "", tell=lambda p: calls.append(p))
    assert r["persona"] == "coach" and r["source"] == "invalid"
    assert calls == []


def test_whitespace_only_string_reply_degrades_to_i_dont_know():
    r = advisory.advise("planner", "plan it", tell=lambda p: "  \n ")
    assert r["answer"] == "I don't know."


def test_reply_source_value_is_propagated():
    r = advisory.advise("coach", "go?", tell=lambda p: _Reply("Go.", source_value="memory"))
    assert r["source"] == "memory"


def test_question_whitespace_is_trimmed_before_framing():
    seen = {}

    def tell(prompt):
        seen["prompt"] = prompt
        return _Reply("ok")

    advisory.advise("coach", "   trim me   ", tell=tell)
    assert seen["prompt"].endswith("trim me")

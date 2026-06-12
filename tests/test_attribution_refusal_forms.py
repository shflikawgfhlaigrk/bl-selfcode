"""Refusal-form edge cases for the structural no-fab gate.

The live brain emits typographic apostrophes ("I don’t know."). The refusal
short-circuit must recognize that form even when the rest of the sentence
carries a number — otherwise an honest refusal gets scanned and 'downgraded'
to itself, and coverage metrics lie."""
from __future__ import annotations

from utah import attribution


def test_curly_apostrophe_refusal_with_a_number_is_trivially_supported():
    """The hostile shape: a refusal that MENTIONS a number ("the 4321 figure
    isn't in my context"). With the straight-quote-only check this was scanned
    and flagged unsupported — an honest refusal failing its own honesty gate."""
    r = attribution.check_attribution(
        "I don’t know. The 4321 figure isn’t in anything I have.", "")
    assert r.supported is True
    assert r.unsupported_numbers == []
    assert r.number_coverage == 1.0


def test_straight_quote_refusal_with_a_number_is_trivially_supported():
    r = attribution.check_attribution("I don't know. Was it 4321?", "")
    assert r.supported is True and r.unsupported_numbers == []


def test_uppercase_curly_refusal_is_supported():
    r = attribution.check_attribution("I DON’T KNOW. Maybe 9999?", "")
    assert r.supported is True


def test_i_do_not_know_long_form_with_number_is_supported():
    r = attribution.check_attribution("I do not know — 8765 isn't in context.", "")
    assert r.supported is True


def test_soft_refusal_with_a_guess_is_still_gated():
    """'Not in the context. As a rough estimate, 4321.' is a refusal to
    brain.is_refusal but it SMUGGLES a number — attribution must keep flagging
    it so strict mode can strip the guess. Only true 'I don't know' forms
    short-circuit."""
    r = attribution.check_attribution(
        "Not in the context. As a rough estimate, 4321 points.", "")
    assert r.supported is False and "4321" in r.unsupported_numbers


def test_vet_answer_strict_passes_curly_refusal_through_unchanged():
    out, r = attribution.vet_answer("I don’t know. Want me to find it?", "",
                                    strict=True)
    assert out == "I don’t know. Want me to find it?"
    assert r.supported is True

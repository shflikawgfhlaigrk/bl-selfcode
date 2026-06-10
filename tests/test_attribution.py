"""Structural no-fabrication proof (audit TIER3): an answer's salient numbers must trace
to the CONTEXT. Advisory by default; strict mode downgrades an unsupported answer."""
from __future__ import annotations

from utah import attribution


def test_supported_when_numbers_are_in_context():
    r = attribution.check_attribution("Kilimanjaro is 5895 meters tall.",
                                      "Mount Kilimanjaro height is 5895 m.")
    assert r.supported is True and r.unsupported_numbers == []


def test_unsupported_when_a_number_is_not_in_context():
    r = attribution.check_attribution("The price is $1700.", "I build websites for $700.")
    assert r.supported is False and "1700" in r.unsupported_numbers


def test_money_and_commas_normalize():
    r = attribution.check_attribution("It costs $1,700.", "The figure is 1700 dollars.")
    assert r.supported is True


def test_small_numbers_are_not_checked():
    # "5 rules" shouldn't trip the gate (single digit, structural not factual)
    r = attribution.check_attribution("There are 5 rules.", "context with no numbers")
    assert r.supported is True and r.unsupported_numbers == []


def test_refusal_is_trivially_supported():
    r = attribution.check_attribution("I don't know. Want me to find it?", "")
    assert r.supported is True and r.number_coverage == 1.0


def test_vet_answer_strict_downgrades_unsupported():
    out, r = attribution.vet_answer("The market rose 4321 points.", "no such number", strict=True)
    assert out == "I don't know." and r.supported is False


def test_vet_answer_advisory_keeps_answer():
    out, r = attribution.vet_answer("The market rose 4321 points.", "no such number", strict=False)
    assert out == "The market rose 4321 points." and r.supported is False

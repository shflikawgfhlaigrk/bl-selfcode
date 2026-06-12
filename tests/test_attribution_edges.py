"""Attribution edge cases: the structural no-fab gate under hostile input.

Complements tests/test_attribution.py with the boundary shapes the live brain
actually emits: typographic apostrophes in refusals, mixed supported/unsupported
numbers, entity advisories, money/percent normalization, and None inputs.
"""
from __future__ import annotations

from utah import attribution


# ── refusal detection ─────────────────────────────────────────────────────────

def test_curly_apostrophe_refusal_is_trivially_supported():
    """The CLI brain emits typographic apostrophes ("I don’t know."); the gate must
    treat that as the same refusal, not scan it for fabricated numbers."""
    r = attribution.check_attribution("I don’t know. Want me to dig in?", "")
    assert r.supported is True and r.number_coverage == 1.0


def test_uppercase_refusal_is_supported():
    r = attribution.check_attribution("I DON'T KNOW.", "")
    assert r.supported is True


def test_none_inputs_are_trivially_supported():
    r = attribution.check_attribution(None, None)
    assert r.supported is True
    assert r.unsupported_numbers == [] and r.unsupported_entities == []


# ── number normalization ─────────────────────────────────────────────────────

def test_percent_matches_bare_number_in_context():
    r = attribution.check_attribution("Win rate was 85%.", "the backtest shows 85 wins per 100")
    assert r.supported is True


def test_mixed_numbers_compute_partial_coverage():
    r = attribution.check_attribution(
        "Revenue was $700 in May and $9999 in June.", "we charge $700 per site")
    assert r.supported is False
    assert r.unsupported_numbers == ["9999"]
    assert r.number_coverage == 0.5


def test_decimal_matches_dotless_context_form():
    r = attribution.check_attribution("It runs at 1.5 per core.", "load shed threshold is 15")
    assert r.supported is True          # 1.5 ↔ 15 dotless equivalence (documented tradeoff)


def test_repeated_number_is_checked_once():
    r = attribution.check_attribution("It is 4321 — yes, 4321.", "")
    assert r.unsupported_numbers == ["4321"]
    assert r.number_coverage == 0.0


def test_year_in_context_supports_year_in_answer():
    r = attribution.check_attribution("Filed in 2026.", "probate filed 2026-06-11")
    assert r.supported is True


# ── entity advisory (never the hard gate) ────────────────────────────────────

def test_unsupported_entity_is_advisory_only():
    r = attribution.check_attribution("Talk to Zebulon about it.", "the plan is simple")
    assert r.supported is True                       # entities never hard-gate
    assert "Zebulon" in r.unsupported_entities
    assert r.entity_coverage < 1.0


def test_persona_entities_are_always_known():
    r = attribution.check_attribution("Michael and Ace agreed.", "no names here")
    assert r.unsupported_entities == []


# ── vet_answer composition ───────────────────────────────────────────────────

def test_vet_answer_supported_passes_through_in_strict_mode():
    out, r = attribution.vet_answer("The site costs $700.", "we charge $700", strict=True)
    assert out == "The site costs $700." and r.supported is True


def test_vet_answer_custom_refusal_text():
    out, _ = attribution.vet_answer("Up 4321 points.", "", strict=True, refusal="(unverified)")
    assert out == "(unverified)"

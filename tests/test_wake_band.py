"""Wake confidence band (2026-06-10): the fix for 'both false fires AND misses'.
A medium-confidence audio wake whose transcript lacks a real 'ace' token is room
speech — drop it. A high-confidence wake trusts STT that dropped the short syllable."""
from __future__ import annotations

from utah import config
from utah.voice import wake


def test_explicit_ace_always_fires_regardless_of_confidence():
    # Stage B text gate: a real 'ace' token fires even with no/low audio confidence.
    assert wake.resolve_command("ace what's the weather") == "what's the weather"
    assert wake.resolve_command("ace what's the weather", audio_wake=True,
                                wake_confidence=0.70) == "what's the weather"


def test_medium_confidence_without_ace_token_is_dropped():
    # TV/room speech: armed at 0.80 (below trust), transcript has no 'ace' → NOT a command.
    assert config.WAKE_TRUST_THRESHOLD > 0.80
    assert wake.resolve_command("the game is on at seven", audio_wake=True,
                                wake_confidence=0.80) is None


def test_high_confidence_trusts_dropped_syllable():
    # Real call where Moonshine dropped 'ace': high audio confidence → accept as command.
    assert wake.resolve_command("what's our lead count", audio_wake=True,
                                wake_confidence=0.96) == "what's our lead count"


def test_none_confidence_stays_permissive_for_legacy_callers():
    assert wake.resolve_command("what's our lead count", audio_wake=True) == "what's our lead count"


def test_empty_transcript_is_bare_wake_not_dropped():
    # Empty after a high wake = bare wake (orb pulse / 'Yeah?'), distinct from a drop.
    assert wake.resolve_command("", audio_wake=True, wake_confidence=0.96) == ""
    assert wake.resolve_command("", audio_wake=True, wake_confidence=0.75) == ""

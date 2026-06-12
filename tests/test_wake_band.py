"""Wake confidence band — ace-only mode after 2026-06-12."""
from __future__ import annotations

from utah.voice import wake


def test_explicit_ace_always_fires_regardless_of_confidence():
    assert wake.resolve_command("ace what's the weather") == "what's the weather"
    assert wake.resolve_command("ace what's the weather", audio_wake=True,
                                wake_confidence=0.70) == "what's the weather"


def test_audio_wake_without_ace_token_is_dropped():
    assert wake.resolve_command("the game is on at seven", audio_wake=True,
                                wake_confidence=0.96) is None


def test_empty_transcript_is_bare_wake_not_dropped():
    assert wake.resolve_command("", audio_wake=True, wake_confidence=0.96) == ""

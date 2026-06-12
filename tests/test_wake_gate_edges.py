"""Wake-gate edges: the confidence-band boundary itself, fillers with the 'utah'
synonym, None/garbage transcripts, punctuation straight after the wake, and the
text gate overriding a low audio confidence when a literal token is present."""
from __future__ import annotations

from utah import config
from utah.voice import wake


def test_confidence_exactly_at_threshold_fires():
    # the band is `< threshold` drops — AT the threshold must trust the wake
    th = config.WAKE_TRUST_THRESHOLD
    assert wake.resolve_command("what's the lead count", audio_wake=True,
                                wake_confidence=th) == "what's the lead count"


def test_confidence_just_below_threshold_drops_room_speech():
    th = config.WAKE_TRUST_THRESHOLD
    assert wake.resolve_command("the game is on tonight", audio_wake=True,
                                wake_confidence=th - 0.01) is None


def test_low_confidence_with_literal_token_still_fires():
    # the TEXT gate wins regardless of the audio band — "ace" was literally said
    assert wake.resolve_command("ace check the engines", audio_wake=True,
                                wake_confidence=0.10) == "check the engines"


def test_none_transcript_is_no_wake():
    assert wake.extract_command(None) is None
    assert wake.resolve_command(None) is None


def test_none_transcript_after_audio_wake_is_bare_wake():
    assert wake.resolve_command(None, audio_wake=True) == ""


def test_filler_with_utah_synonym():
    assert wake.extract_command("okay utah what's new") == "what's new"
    assert wake.extract_command("say utah") == ""


def test_punctuation_straight_after_wake():
    assert wake.extract_command("ace: status report") == "status report"
    assert wake.extract_command("ace! run the brief") == "run the brief"


def test_multiline_transcript_finds_the_wake():
    assert wake.extract_command("uh huh\nace what's the weather") == "what's the weather"


def test_near_miss_words_never_fire_even_with_audio_wake_medium_band():
    # 'space' is not a wake; with a medium-confidence arm the whole utterance drops
    th = config.WAKE_TRUST_THRESHOLD
    assert wake.resolve_command("look at space tonight", audio_wake=True,
                                wake_confidence=th - 0.05) is None


def test_command_on_both_sides_prefers_text_after_the_wake():
    assert wake.extract_command("so anyway ace what time is it") == "what time is it"


def test_public_surface():
    assert set(wake.__all__) == {"extract_command", "resolve_command"}

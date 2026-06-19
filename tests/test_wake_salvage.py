"""Stage-B salvage (2026-06-19): when openWakeWord ALREADY confirmed "hey ace"
(audio_wake=True) but the STT mangled the wake token, recover the command instead of
discarding it — WITHOUT executing room speech that merely false-tripped Stage A.

The discriminator is POSITION: a mangled wake leaves garble BEFORE the command
("KAs. What is the time?"); room speech STARTS with the command word ("what's the
lead count"). This locks Michael's "first 2 worked then KAs got dropped" fix.
"""
from __future__ import annotations

from utah.voice import wake


def test_mangled_ace_with_leading_garble_is_salvaged():
    assert wake.resolve_command("KAs. What is the time?", audio_wake=True) == "What is the time?"


def test_doubled_garble_then_question_is_salvaged():
    out = wake.resolve_command("Hey, hey, what's... What is 2 plus 2?", audio_wake=True)
    assert out and "2 plus 2" in out


def test_clean_ace_token_still_extracts_normally():
    assert wake.resolve_command("Hey Ace, what time is it?", audio_wake=True) == "what time is it?"


def test_room_speech_starting_with_command_word_is_dropped():
    # No leading garble — the command word is first → this is room speech past Stage A.
    assert wake.resolve_command("what's the lead count", audio_wake=True,
                                wake_confidence=0.96) is None


def test_room_speech_weak_verb_is_dropped():
    assert wake.resolve_command("the game is on at seven", audio_wake=True,
                                wake_confidence=0.96) is None


def test_no_audio_wake_still_strict():
    # Without Stage A, only a literal token fires — salvage never applies.
    assert wake.resolve_command("what is the weather", audio_wake=False) is None

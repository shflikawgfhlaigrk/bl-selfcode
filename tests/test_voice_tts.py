"""TTS playback must produce CLEAN audio. The static the live test surfaced came
from ``sd.play(int16_array)`` — an unproven, module-global stream that's fragile on
dtype, on output-device selection, and on concurrency (voice + chat speaking at once
corrupt the shared stream → garble). speak() now synthesizes the PROVEN-clean WAV
(``synthesize_wav`` — the round-trip path) and plays it through the macOS reference
player, serialized so two speakers never overlap. The player is injected here so the
test proves the wiring without blasting audio; REAL Piper does all the synthesis.
"""
from __future__ import annotations

import os
import wave

import subprocess

from utah.voice import tts as tts_mod
from utah.voice.tts import PiperTTS, _drain_sentences


def test_afplay_is_time_bounded_so_a_hung_player_cannot_deafen_the_mic(monkeypatch):
    """ROOT CAUSE of chronic mic_silent: a hung afplay held the exclusive play-lock, and the
    always-on voice loop drops every mic frame while is_anything_playing() is True → deaf until
    restart. _afplay must pass a positive timeout to subprocess.run AND swallow TimeoutExpired
    (afplay is killed → the lock releases via the play loop → the mic recovers)."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["timeout"] = kw.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(tts_mod.subprocess, "run", fake_run)
    tts_mod._afplay("/tmp/whatever.wav")                  # MUST NOT raise (else the lock leaks)
    assert seen["cmd"][0] == "afplay"
    assert isinstance(seen["timeout"], (int, float)) and seen["timeout"] > 0


def _assert_clean_wav(path: str) -> None:
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == 22050   # Piper en_GB-cori-high rate
        assert wf.getsampwidth() == 2       # 16-bit
        assert wf.getnchannels() == 1       # mono
        assert wf.getnframes() > 0          # real audio, not empty


def test_speak_synthesizes_real_wav_and_plays_it():
    played: list[str] = []

    def rec(path: str) -> None:
        _assert_clean_wav(path)             # real Piper wrote a valid WAV
        played.append(path)

    PiperTTS(player=rec).speak("hello")
    assert len(played) == 1
    assert not os.path.exists(played[0])    # temp WAV cleaned up after playback


def test_speak_empty_text_does_not_play():
    played: list[str] = []
    PiperTTS(player=lambda p: played.append(p)).speak("   ")
    assert played == []


# ----- sentence drainer: the unit that makes "speak the first sentence now" safe ----

def test_drain_splits_complete_sentences_and_holds_the_remainder():
    sents, rest = _drain_sentences("First one. Second one! Third")
    assert sents == ["First one.", "Second one!"]
    assert rest == " Third"                      # incomplete tail held back


def test_drain_does_not_split_inside_a_number():
    sents, rest = _drain_sentences("Pi is 3.14 roughly")
    assert sents == []                           # "3.14" is not a sentence boundary
    assert rest == "Pi is 3.14 roughly"


def test_drain_holds_a_terminator_at_the_very_end():
    # a "." at the buffer end might be mid-token or continue next chunk → hold it
    sents, rest = _drain_sentences("Done.")
    assert sents == []
    assert rest == "Done."


def test_drain_splits_on_newlines():
    sents, rest = _drain_sentences("line one\nline two\n")
    assert sents == ["line one", "line two"]
    assert rest == ""


# ----- streaming speak: first sentence reaches the player before the stream ends ----

def test_speak_stream_pipelines_sentences_in_order():
    played: list[str] = []

    def rec(path: str) -> None:
        _assert_clean_wav(path)                  # real Piper wrote each sentence
        played.append(path)

    tts = PiperTTS(player=rec)
    # feed three sentences across awkward chunk boundaries (as the brain streams)
    full = tts.speak_stream(["First sen", "tence. Second one. ", "Third and last."])
    assert len(played) == 3                       # one clean WAV per sentence
    assert all(not os.path.exists(p) for p in played)   # every temp WAV cleaned up
    assert full == "First sentence. Second one. Third and last."


def test_speak_stream_fires_on_start_once_at_first_audio():
    starts: list[int] = []
    tts = PiperTTS(player=lambda p: None)
    tts.speak_stream(["Alpha. ", "Beta. ", "Gamma."], on_start=lambda: starts.append(1))
    assert starts == [1]                          # fired exactly once, not per sentence


def test_speak_stream_whitespace_only_plays_nothing():
    played: list[str] = []
    PiperTTS(player=lambda p: played.append(p)).speak_stream(["   ", "\n", "  "])
    assert played == []


def test_speak_stream_survives_a_bad_synth_clip():
    """A synth failure on one sentence is logged and skipped — the rest still play."""
    played: list[str] = []
    tts = PiperTTS(player=lambda p: played.append(p))
    real_synth = tts.synth_wav
    calls = {"n": 0}

    def flaky(text: str, path: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synth blew up on the first clip")
        real_synth(text, path)

    tts.synth_wav = flaky  # type: ignore[method-assign]
    full = tts.speak_stream(["Boom here. ", "But this one works."])
    assert len(played) == 1                       # only the good sentence played
    assert full == "Boom here. But this one works."   # spoken text still reports both

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

from utah.voice.tts import PiperTTS


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

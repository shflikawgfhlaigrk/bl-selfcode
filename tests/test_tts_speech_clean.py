"""Voice must NEVER speak markdown formatting aloud — Piper read ``*`` as the literal
word "asterisk", so a bolded/bulleted answer became "asterisk ... asterisk". clean_for_speech
strips the offenders at the single synth chokepoint; this locks it so the bug can't return."""
from __future__ import annotations

from utah.voice.tts import clean_for_speech, PiperTTS


def test_asterisks_are_removed():
    assert "*" not in clean_for_speech("**Bold** and *italic* text")
    assert clean_for_speech("**Hello**") == "Hello"


def test_bullets_and_headers_dropped():
    out = clean_for_speech("# Title\n* one\n* two")
    assert "*" not in out and "#" not in out
    assert "Title" in out and "one" in out and "two" in out


def test_code_ticks_and_strikethrough_removed():
    assert "`" not in clean_for_speech("run `pytest` now")
    assert clean_for_speech("~~old~~ new") == "old new"


def test_identifiers_and_csharp_survive():
    # NOT markdown — must be left intact so the spoken term isn't mangled.
    assert clean_for_speech("the file_name field") == "the file_name field"
    assert clean_for_speech("written in C# language") == "written in C# language"


def test_empty_and_none_safe():
    assert clean_for_speech("") == ""
    assert clean_for_speech(None) is None


def test_synth_wav_cleans_before_synthesis():
    """The chokepoint: synth_wav must hand CLEAN text to the voice — no asterisks reach
    Piper. A fake voice captures exactly what it was asked to synthesize."""
    captured = {}

    class _FakeVoice:
        def synthesize_wav(self, text, wf):
            captured["text"] = text
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(22050)
            wf.writeframes(b"")               # valid (empty) WAV so close() succeeds

    tts = PiperTTS.__new__(PiperTTS)          # skip __init__ (no model load)
    tts._voice = _FakeVoice()
    import threading
    tts._lock = threading.Lock()

    import wave, tempfile, os
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        tts.synth_wav("**buy** the `dip`", path)
    finally:
        os.remove(path)
    assert "*" not in captured["text"] and "`" not in captured["text"]
    assert captured["text"] == "buy the dip"

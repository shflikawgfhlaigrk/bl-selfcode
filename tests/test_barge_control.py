"""Button barge control file — deck → voice loop."""
from __future__ import annotations

from utah.voice.barge_control import consume_button_barge, request_button_barge


def test_request_and_consume_once(tmp_path, monkeypatch):
    from utah.voice import barge_control

    flag = tmp_path / "voice_barge.request"
    monkeypatch.setattr(barge_control, "BARGE_REQUEST", flag)
    assert consume_button_barge() is False
    request_button_barge()
    assert flag.is_file()
    assert consume_button_barge() is True
    assert consume_button_barge() is False


def test_button_barge_accepts_speech_without_ace():
    from utah.voice import wake

    assert wake.resolve_command("stop that", button_barge=True) == "stop that"
    assert wake.resolve_command("ace what's up", button_barge=True) == "what's up"


def test_ace_only_audio_wake_drops_missing_token():
    from utah.voice import wake

    assert wake.resolve_command("what's our lead count", audio_wake=True,
                                wake_confidence=0.96) is None

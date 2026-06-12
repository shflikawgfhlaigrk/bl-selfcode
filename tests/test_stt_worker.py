"""STT worker protocol — the killable subprocess that keeps the Whisper model warm.

The contract the parent (:class:`utah.voice.stt.SubprocessSTT`) relies on, pinned
here against the REAL ``main()`` loop with an injected engine (the engine is the
only fake — the protocol, warm-up, and error paths are the system under test):

* line 1 is ``{"ready": true}`` ONLY after the model has actually transcribed a
  real (silent) clip — a bare attribute touch loads nothing, so "ready" before a
  warm-up transcribe meant the first live turn ate the cold Metal compile inside
  the parent's hang timeout → kill → respawn loop (cold forever);
* every wav path line gets exactly one ``{"text": ...}`` JSON line back;
* a clip that makes the engine raise yields ``{"text": ""}`` — the worker survives;
* an engine that cannot even load reports ``{"ready": false}`` honestly (rc 1).
"""
from __future__ import annotations

import io
import json
import os
import wave

import pytest

from utah.voice import stt_worker


class FakeEngine:
    """Records transcribe calls; raises on demand for specific paths."""

    def __init__(self, text: str = "hello", boom_on: str | None = None,
                 always_boom: bool = False) -> None:
        self.text = text
        self.boom_on = boom_on
        self.always_boom = always_boom
        self.calls: list[str] = []

    def transcribe(self, wav_path: str) -> str:
        self.calls.append(wav_path)
        if self.always_boom or wav_path == self.boom_on:
            raise RuntimeError("engine exploded")
        return self.text


def _run(stdin_text: str, engine) -> tuple[int, list[dict]]:
    out = io.StringIO()
    rc = stt_worker.main(stdin=io.StringIO(stdin_text), stdout=out,
                         make_engine=lambda: engine)
    lines = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    return rc, lines


def test_protocol_ready_then_one_text_line_per_path():
    eng = FakeEngine(text="what time is it")
    rc, lines = _run("/a.wav\n/b.wav\n", eng)
    assert rc == 0
    assert lines[0] == {"ready": True}
    assert lines[1:] == [{"text": "what time is it"}, {"text": "what time is it"}]
    # both real paths reached the engine, in order
    assert eng.calls[-2:] == ["/a.wav", "/b.wav"]


def test_blank_lines_are_skipped_not_answered():
    eng = FakeEngine()
    rc, lines = _run("\n   \n/x.wav\n", eng)
    assert rc == 0
    assert lines == [{"ready": True}, {"text": "hello"}]


def test_ready_is_sent_only_after_a_real_warmup_transcribe():
    """The model must be WARM before {"ready": true} goes out — the parent's boot
    wait (STT_WORKER_BOOT_S) exists to cover the cold load/compile. A worker that
    reports ready without having transcribed anything pushes the cold compile into
    the first live turn, which the parent kills at STT_HANG_TIMEOUT_S."""
    eng = FakeEngine()
    rc, lines = _run("/real.wav\n", eng)
    assert rc == 0 and lines[0] == {"ready": True}
    # The FIRST engine call is the warm-up clip — before any stdin path.
    assert len(eng.calls) == 2
    warmup = eng.calls[0]
    assert warmup != "/real.wav"
    # The warm-up clip is a genuine WAV the engine could decode, not a phantom path.
    # (main() removes it afterwards, so we re-create it via the helper to inspect.)


def test_warmup_clip_is_a_valid_silent_wav(tmp_path):
    path = stt_worker._write_silent_wav(str(tmp_path / "warm.wav"))
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == 16_000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() > 0
    os.remove(path)


def test_warmup_clip_is_cleaned_up():
    eng = FakeEngine()
    _run("", eng)
    assert len(eng.calls) == 1                      # warm-up only
    assert not os.path.exists(eng.calls[0])         # temp clip removed


def test_one_bad_clip_yields_empty_text_and_the_worker_survives():
    eng = FakeEngine(boom_on="/bad.wav")
    rc, lines = _run("/bad.wav\n/good.wav\n", eng)
    assert rc == 0
    assert lines == [{"ready": True}, {"text": ""}, {"text": "hello"}]


def test_engine_factory_failure_reports_not_ready_and_exits_nonzero():
    def boom():
        raise ImportError("mlx_whisper missing")

    out = io.StringIO()
    rc = stt_worker.main(stdin=io.StringIO(""), stdout=out, make_engine=boom)
    first = json.loads(out.getvalue().splitlines()[0])
    assert rc == 1
    assert first["ready"] is False
    assert "mlx_whisper" in first["error"]


def test_broken_model_reports_not_ready_honestly():
    """An engine that cannot transcribe even silence is DEAD — the worker must say
    so ({"ready": false}), not lie 'ready' and fail every real turn after."""
    eng = FakeEngine(always_boom=True)
    rc, lines = _run("/x.wav\n", eng)
    assert rc == 1
    assert lines[0]["ready"] is False
    assert lines[0].get("error")                    # the reason travels to the parent


def test_default_engine_selection_respects_config(monkeypatch):
    from utah import config

    monkeypatch.setattr(config, "STT_ENGINE", "moonshine")
    from utah.voice import stt

    assert isinstance(stt_worker._engine(), stt.MoonshineSTT)
    monkeypatch.setattr(config, "STT_ENGINE", "whisper")
    assert isinstance(stt_worker._engine(), stt.MLXWhisperSTT)


def test_never_subprocess_stt_inside_the_worker(monkeypatch):
    """SubprocessSTT inside the worker would recurse (worker spawning workers)."""
    from utah import config
    from utah.voice import stt

    for engine_name in ("moonshine", "whisper", "whispercpp", ""):
        monkeypatch.setattr(config, "STT_ENGINE", engine_name)
        assert not isinstance(stt_worker._engine(), stt.SubprocessSTT)


def test_output_lines_are_strict_json():
    eng = FakeEngine(text='tricky "quoted" \n newline')
    rc, lines = _run("/x.wav\n", eng)
    assert rc == 0
    assert lines[1]["text"] == 'tricky "quoted" \n newline'  # JSON-safe round-trip


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

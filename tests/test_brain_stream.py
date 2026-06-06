"""Streaming brain: decode the Claude CLI stream-json, split elicited
``<thinking>…</thinking>`` reasoning from the answer, and yield ordered
(channel, chunk) events so the deck can render reasoning live like Claude.
Every failure mode still raises :class:`BrainUnavailable` — nothing fabricated.
"""
from __future__ import annotations

import json

import pytest

from utah import brain, config
from tests.fakes import ScriptedStreamRunner


# --- stream-json line builders (mirror the real CLI shape) -------------------

def _delta_text(text: str) -> str:
    return json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 1,
        "delta": {"type": "text_delta", "text": text}}})


def _start_block(kind: str, index: int) -> str:
    return json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": index,
        "content_block": {"type": kind}}})


def _delta_thinking(text: str) -> str:
    return json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "thinking_delta", "thinking": text}}})


# --- decode_stream: NDJSON stream-json -> ('text'|'thinking', chunk) ----------

def test_decode_stream_yields_text_deltas():
    lines = [_start_block("text", 1), _delta_text("Hello "), _delta_text("world")]
    assert list(brain.decode_stream(lines)) == [("text", "Hello "), ("text", "world")]


def test_decode_stream_yields_native_thinking_deltas():
    lines = [_start_block("thinking", 0), _delta_thinking("let me reason")]
    assert list(brain.decode_stream(lines)) == [("thinking", "let me reason")]


def test_decode_stream_ignores_system_and_result_and_blank_lines():
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        "",
        _start_block("text", 1),
        _delta_text("hi"),
        json.dumps({"type": "result", "terminal_reason": "completed"}),
    ]
    assert list(brain.decode_stream(lines)) == [("text", "hi")]


def test_decode_stream_tolerates_malformed_json_line():
    lines = ["{not valid json", _start_block("text", 1), _delta_text("ok")]
    assert list(brain.decode_stream(lines)) == [("text", "ok")]


# --- split_thinking: <thinking>…</thinking> state machine --------------------

def _split(chunks):
    return list(brain.split_thinking(chunks))


def test_split_extracts_thinking_then_answer():
    assert _split(["<thinking>reasoned</thinking>the answer"]) == [
        ("thinking", "reasoned"), ("answer", "the answer")]


def test_split_handles_tags_across_chunks():
    events = _split(["<thi", "nking>rea", "son</thin", "king>ans", "wer"])
    thinking = "".join(t for c, t in events if c == "thinking")
    answer = "".join(t for c, t in events if c == "answer")
    assert thinking == "reason"
    assert answer == "answer"


def test_split_no_tags_is_all_answer():
    assert _split(["just the answer, no tags"]) == [("answer", "just the answer, no tags")]


def test_split_strips_leading_whitespace_before_open_tag():
    events = _split(["  \n<thinking>r</thinking>a"])
    thinking = "".join(t for c, t in events if c == "thinking")
    answer = "".join(t for c, t in events if c == "answer")
    assert thinking == "r"
    assert answer == "a"


# --- think_stream: prompt + flags + composition ------------------------------

def _stream_lines_for(thinking: str, answer: str) -> list[str]:
    """One realistic text stream: the model emits <thinking>…</thinking>answer."""
    body = f"<thinking>{thinking}</thinking>{answer}"
    return [_start_block("text", 1)] + [_delta_text(ch) for ch in body]


def test_think_stream_routes_thinking_then_answer():
    runner = ScriptedStreamRunner(_stream_lines_for("recalled 2 hits", "Utah is the rebuild"))
    brain.set_stream_runner(runner)
    events = list(brain.think_stream("what is utah?", "- Utah is the rebuild"))
    thinking = "".join(t for c, t in events if c == "thinking")
    answer = "".join(t for c, t in events if c == "answer")
    assert thinking == "recalled 2 hits"
    assert answer == "Utah is the rebuild"


def test_think_stream_prompt_has_nofab_thinking_instruction_and_context():
    runner = ScriptedStreamRunner(_stream_lines_for("x", "y"))
    brain.set_stream_runner(runner)
    list(brain.think_stream("where does Michael live?", "- Michael lives in Utah"))
    prompt = runner.last_prompt
    assert brain.NO_FAB in prompt
    assert brain.THINK_INSTRUCTION in prompt
    assert "- Michael lives in Utah" in prompt
    assert "where does Michael live?" in prompt


def test_think_stream_uses_stream_json_flags():
    runner = ScriptedStreamRunner(_stream_lines_for("x", "y"))
    brain.set_stream_runner(runner)
    list(brain.think_stream("q"))
    argv = runner.last_argv
    assert "--output-format" in argv and "stream-json" in argv
    assert "--include-partial-messages" in argv


def test_think_stream_raises_brain_unavailable_on_runner_failure():
    runner = ScriptedStreamRunner(brain.BrainUnavailable("cli gone"))
    brain.set_stream_runner(runner)
    with pytest.raises(brain.BrainUnavailable, match="cli gone"):
        list(brain.think_stream("q"))


def test_think_stream_native_thinking_block_passes_through():
    """If the CLI ever emits a real thinking block, it routes to 'thinking'."""
    lines = [_start_block("thinking", 0), _delta_thinking("native reasoning"),
             _start_block("text", 1), _delta_text("the answer")]
    runner = ScriptedStreamRunner(lines)
    brain.set_stream_runner(runner)
    events = list(brain.think_stream("q"))
    assert ("thinking", "native reasoning") in events
    answer = "".join(t for c, t in events if c == "answer")
    assert answer == "the answer"


def test_set_stream_runner_none_restores_subprocess_stream_runner():
    brain.set_stream_runner(ScriptedStreamRunner([]))
    brain.set_stream_runner(None)
    assert brain._stream_runner is brain._subprocess_stream_runner


# --- the real streaming subprocess runner (no Claude CLI needed) -------------

def test_subprocess_stream_runner_yields_stdout_lines():
    out = list(brain._subprocess_stream_runner(
        ["python3", "-c", "print('one'); print('two')"], 10))
    assert [s.strip() for s in out] == ["one", "two"]


def test_subprocess_stream_runner_missing_binary_is_structured():
    with pytest.raises(brain.BrainUnavailable, match="not found"):
        list(brain._subprocess_stream_runner(["/nonexistent/claude-xyz"], 5))


def test_subprocess_stream_runner_nonzero_exit_is_structured():
    with pytest.raises(brain.BrainUnavailable, match="exited"):
        list(brain._subprocess_stream_runner(
            ["python3", "-c", "import sys; sys.exit(3)"], 10))

"""core.tell_stream — the streaming turn: recall → ground → stream the brain's
reasoning+answer → remember. Same no-fabrication contract as core.tell, but it
yields ordered (channel, chunk) events so chat AND voice can show reasoning live.
"""
from __future__ import annotations

import json

from utah import brain, core, memory
from utah.objects import Hit
from tests.fakes import ScriptedStreamRunner


def _delta(text: str) -> str:
    return json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 1,
        "delta": {"type": "text_delta", "text": text}}})


def _start(kind: str, i: int) -> str:
    return json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": i, "content_block": {"type": kind}}})


def _brain_lines(thinking: str, answer: str) -> list[str]:
    body = f"<thinking>{thinking}</thinking>{answer}"
    return [_start("text", 1)] + [_delta(c) for c in body]


def _hit(content: str) -> Hit:
    return Hit(id=1, content=content, source="fact", score=1.0, sim=0.9)


def test_tell_stream_streams_thinking_then_answer_via_brain(monkeypatch):
    monkeypatch.setattr(memory, "recall", lambda t, *a, **k: [_hit("Utah is the rebuild")])
    monkeypatch.setattr(memory, "store", lambda *a, **k: None)
    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("recalled 1 hit", "Utah is the rebuild.")))
    evs = list(core.tell_stream("what is utah?"))
    thinking = "".join(t for k, t in evs if k == "thinking")
    answer = "".join(t for k, t in evs if k == "answer")
    assert "recalled 1 hit" in thinking
    assert "Utah is the rebuild." in answer
    assert ("source", "brain") in evs
    assert any(k == "done" for k, _ in evs)


def test_tell_stream_always_reasons_with_memory_as_context(monkeypatch):
    """Memory FEEDS cognition — it never short-circuits the stream. Even with a
    strong recall hit, the brain runs and reasoning is shown live (like Claude);
    the recalled memory is handed to the brain as grounding context, not returned
    verbatim. This is the fix for 'it showed the path instead of thinking'."""
    monkeypatch.setattr(memory, "recall",
                        lambda t, *a, **k: [_hit("Michael lives in Newnan, Georgia")])
    monkeypatch.setattr(memory, "store", lambda *a, **k: None)
    r = ScriptedStreamRunner(_brain_lines("the context says Newnan", "Newnan, Georgia."))
    brain.set_stream_runner(r)
    evs = list(core.tell_stream("where does Michael live?"))
    thinking = "".join(t for k, t in evs if k == "thinking")
    answer = "".join(t for k, t in evs if k == "answer")
    assert ("source", "brain") in evs                       # brain ran — no short-circuit
    assert ("source", "memory") not in evs
    assert "the context says Newnan" in thinking            # reasoning IS shown
    assert "Newnan, Georgia." in answer
    assert "Michael lives in Newnan, Georgia" in r.last_prompt  # memory fed as context


def test_tell_stream_remembers_the_turn_after_brain(monkeypatch):
    stored = {}
    monkeypatch.setattr(memory, "recall", lambda t, *a, **k: [])
    monkeypatch.setattr(memory, "store", lambda content, **k: stored.update(content=content, source=k.get("source")))
    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("reason", "The answer.")))
    list(core.tell_stream("q?"))
    assert "The answer." in stored.get("content", "")
    assert stored.get("source") == "turn"


def test_tell_stream_does_not_remember_a_refusal(monkeypatch):
    calls = {"store": 0}
    monkeypatch.setattr(memory, "recall", lambda t, *a, **k: [])
    monkeypatch.setattr(memory, "store", lambda *a, **k: calls.__setitem__("store", calls["store"] + 1))
    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("hmm", "I don't know.")))
    list(core.tell_stream("q?"))
    assert calls["store"] == 0


def test_tell_stream_degrades_when_brain_unavailable(monkeypatch):
    monkeypatch.setattr(memory, "recall", lambda t, *a, **k: [])
    monkeypatch.setattr(memory, "store", lambda *a, **k: None)
    brain.set_stream_runner(ScriptedStreamRunner(brain.BrainUnavailable("cli gone")))
    evs = list(core.tell_stream("q?"))
    answer = "".join(t for k, t in evs if k == "answer").lower()
    assert "unavailable" in answer or "don't know" in answer
    assert ("source", "unavailable") in evs


def test_tell_stream_carries_recent_conversation(monkeypatch):
    """A follow-up turn must see the prior turn(s) — a real conversation thread,
    not isolated one-shots. The brain prompt for turn 2 includes turn 1's Q+A."""
    core.reset_conversation()
    monkeypatch.setattr(memory, "recall", lambda t, *a, **k: [])
    monkeypatch.setattr(memory, "store", lambda *a, **k: None)

    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("t", "Utah is the rebuild of AceOS.")))
    list(core.tell_stream("what is utah"))

    r2 = ScriptedStreamRunner(_brain_lines("t", "Because the base was unproven."))
    brain.set_stream_runner(r2)
    list(core.tell_stream("why does that matter"))

    prompt2 = r2.last_prompt
    assert "what is utah" in prompt2
    assert "Utah is the rebuild of AceOS." in prompt2
    core.reset_conversation()


def test_reset_conversation_clears_the_thread(monkeypatch):
    core.reset_conversation()
    monkeypatch.setattr(memory, "recall", lambda t, *a, **k: [])
    monkeypatch.setattr(memory, "store", lambda *a, **k: None)
    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("t", "A1.")))
    list(core.tell_stream("first question here"))
    core.reset_conversation()
    r2 = ScriptedStreamRunner(_brain_lines("t", "A2."))
    brain.set_stream_runner(r2)
    list(core.tell_stream("second"))
    assert "first question here" not in r2.last_prompt


def test_tell_stream_empty_input_is_handled():
    evs = list(core.tell_stream("   "))
    assert ("source", "unavailable") in evs
    assert any(k == "done" for k, _ in evs)

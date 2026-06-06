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
    monkeypatch.setattr(memory, "answer", lambda t, *a, **k: (None, [_hit("Utah is the rebuild")]))
    monkeypatch.setattr(memory, "store", lambda *a, **k: None)
    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("recalled 1 hit", "Utah is the rebuild.")))
    evs = list(core.tell_stream("what is utah?"))
    thinking = "".join(t for k, t in evs if k == "thinking")
    answer = "".join(t for k, t in evs if k == "answer")
    assert "recalled 1 hit" in thinking
    assert "Utah is the rebuild." in answer
    assert ("source", "brain") in evs
    assert any(k == "done" for k, _ in evs)


def test_tell_stream_returns_memory_answer_without_calling_brain(monkeypatch):
    monkeypatch.setattr(memory, "answer", lambda t, *a, **k: ("Newnan, Georgia", [_hit("Michael lives in Newnan, Georgia")]))
    def _no_brain(*a, **k):
        raise AssertionError("brain must not be called when memory answers")
    monkeypatch.setattr(brain, "think_stream", _no_brain)
    evs = list(core.tell_stream("where does Michael live?"))
    answer = "".join(t for k, t in evs if k == "answer")
    assert "Newnan" in answer
    assert ("source", "memory") in evs


def test_tell_stream_remembers_the_turn_after_brain(monkeypatch):
    stored = {}
    monkeypatch.setattr(memory, "answer", lambda t, *a, **k: (None, []))
    monkeypatch.setattr(memory, "store", lambda content, **k: stored.update(content=content, source=k.get("source")))
    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("reason", "The answer.")))
    list(core.tell_stream("q?"))
    assert "The answer." in stored.get("content", "")
    assert stored.get("source") == "turn"


def test_tell_stream_does_not_remember_a_refusal(monkeypatch):
    calls = {"store": 0}
    monkeypatch.setattr(memory, "answer", lambda t, *a, **k: (None, []))
    monkeypatch.setattr(memory, "store", lambda *a, **k: calls.__setitem__("store", calls["store"] + 1))
    brain.set_stream_runner(ScriptedStreamRunner(_brain_lines("hmm", "I don't know.")))
    list(core.tell_stream("q?"))
    assert calls["store"] == 0


def test_tell_stream_degrades_when_brain_unavailable(monkeypatch):
    monkeypatch.setattr(memory, "answer", lambda t, *a, **k: (None, []))
    monkeypatch.setattr(memory, "store", lambda *a, **k: None)
    brain.set_stream_runner(ScriptedStreamRunner(brain.BrainUnavailable("cli gone")))
    evs = list(core.tell_stream("q?"))
    answer = "".join(t for k, t in evs if k == "answer").lower()
    assert "unavailable" in answer or "don't know" in answer
    assert ("source", "unavailable") in evs


def test_tell_stream_memory_turn_answer_is_cleaned(monkeypatch):
    """A confident memory answer recalled from a stored 'turn' row must surface
    just the answer, not the raw 'Q: …\\nA: …' scaffold (like Claude would)."""
    hit = Hit(id=1, content="Q: what is utah\nA: Utah is the rebuild.", source="turn",
              score=1.0, sim=0.9)
    monkeypatch.setattr(memory, "answer",
                        lambda t, *a, **k: ("Q: what is utah\nA: Utah is the rebuild.", [hit]))
    evs = list(core.tell_stream("what is utah"))
    answer = "".join(t for k, t in evs if k == "answer")
    assert answer == "Utah is the rebuild."


def test_tell_stream_empty_input_is_handled():
    evs = list(core.tell_stream("   "))
    assert ("source", "unavailable") in evs
    assert any(k == "done" for k, _ in evs)

"""Introspection — Utah's self-model from live state (injectable)."""
from __future__ import annotations

from utah import introspect


def test_self_model_from_injected_state():
    m = introspect.self_model(
        status={"version": "1.0.0"},
        memory_counts={"total": 9000, "live": 1900, "entities": 9500},
    )
    assert m["daemon_up"] is True
    assert m["memory"]["live"] == 1900
    assert m["capability_count"] == len(introspect.CAPABILITIES)
    assert "leads" in m["capabilities"] and "selfcode" in m["capabilities"]


def test_self_model_daemon_down():
    m = introspect.self_model(status=None, memory_counts={})
    assert m["daemon_up"] is False


# --- the LIVE wire: a self/project question grounds the brain in the self-model --------
# self_model() existed but nothing consumed it — these pin the genuine consumption
# point: core's brain-context assembly for _SELF_OR_PROJECT turns.

def test_self_model_facts_render_for_grounding():
    from utah import core

    facts = core._self_model_facts(self_model=lambda: {
        "identity": "Utah — clean-room rebuild.",
        "capabilities": ["leads", "news"], "capability_count": 2,
        "daemon_up": True, "memory": {"live": 1900}})
    assert "Utah — clean-room rebuild." in facts
    assert "leads, news" in facts
    assert "Daemon: up" in facts
    assert "live=1900" in facts


def test_self_model_facts_degrade_honestly_when_introspection_fails():
    # A failed introspection is never silently dropped — the grounding block says
    # plainly that the live self-model is unavailable (and the failure is logged).
    from utah import core

    def boom():
        raise RuntimeError("socket gone")

    facts = core._self_model_facts(self_model=boom)
    assert "unavailable" in facts.lower()


def test_self_question_grounds_the_brain_in_the_live_self_model(mem, fake_brain, monkeypatch):
    """The live wire: a _SELF_OR_PROJECT turn through core.tell carries the
    introspection self-model into the brain prompt as labelled grounding facts."""
    from utah import core
    from utah.objects import ReplySource

    monkeypatch.setattr(
        "utah.introspect.self_model",
        lambda: {"identity": "Utah — clean-room AceOS rebuild.",
                 "capabilities": ["leads", "news"], "capability_count": 2,
                 "daemon_up": True, "memory": {"live": 4242}})
    fake_brain.respond = "I'm Ace — leads and news, daemon up."

    reply = core.tell("what can you do")

    assert reply.source is ReplySource.BRAIN
    assert "YOUR LIVE SELF-MODEL" in fake_brain.last_prompt
    assert "live=4242" in fake_brain.last_prompt


def test_non_self_questions_do_not_pay_the_introspection_call(mem, fake_brain, monkeypatch):
    # Introspection (a daemon status round-trip) is scoped to self/project turns —
    # an ordinary brain turn must not pay for it or carry the block.
    from utah import core

    calls = {"n": 0}

    def counting_model():
        calls["n"] += 1
        return {}

    monkeypatch.setattr("utah.introspect.self_model", counting_model)
    fake_brain.respond = "An answer."

    core.tell("what's the plan for tomorrow")

    assert calls["n"] == 0
    assert "SELF-MODEL" not in fake_brain.last_prompt

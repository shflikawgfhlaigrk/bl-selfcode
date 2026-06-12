"""News capability — the Route.NEWS lane: topic extraction, the grounded answer over
``headlines()`` → ``researcher.research``, honest degradation when the web yields
nothing, and the LIVE core dispatch (router NEWS intent → core → news.headlines).
``news.headlines`` existed but nothing live called it — these pin the real wire."""
from __future__ import annotations

import pytest

from utah import core
from utah.objects import ReplySource
from utah.product import news


# --- topic extraction (pure) -----------------------------------------------------------

@pytest.mark.parametrize("text,topic", [
    ("news about the housing market", "housing market"),
    ("latest news on tesla", "tesla"),
    ("any news about openai?", "openai"),
    ("what's happening with the election", "election"),
    ("headlines for atlanta", "atlanta"),
])
def test_extract_topic_pulls_the_subject(text, topic):
    assert news.extract_topic(text) == topic


def test_extract_topic_bare_news_request_gets_the_general_topic():
    # No explicit subject → the general fallback, never an empty query.
    assert news.extract_topic("give me the headlines") == news.FALLBACK_TOPIC
    assert news.extract_topic("what's the news") == news.FALLBACK_TOPIC
    assert news.extract_topic("") == news.FALLBACK_TOPIC


# --- answer(): a grounded, speakable reply over headlines() ----------------------------

def test_answer_reports_real_researched_facts():
    seen = {}

    def fake_research(q, k=3):
        seen["q"] = q
        return {"query": q, "sources": 2, "facts": 2, "stored": 2,
                "fact_list": ["Tesla recalled 100k cars.", "Tesla stock fell 4%."]}

    out = news.answer("latest news on tesla", research=fake_research)
    assert "latest news about tesla" in seen["q"]       # went through headlines()
    assert "Tesla recalled 100k cars." in out
    assert "2 sources" in out


def test_answer_is_honest_when_nothing_grounded():
    # Blocked search / empty web / zero extracted facts → say so plainly. This lane
    # exists precisely so a model never invents a headline.
    def fake_research(q, k=3):
        return {"query": q, "sources": 0, "facts": 0, "stored": 0, "error": "blocked"}

    out = news.answer("news about quantum widgets", research=fake_research)
    assert "couldn't pull" in out.lower()
    assert "won't make up headlines" in out.lower()
    assert "blocked" in out                              # the honest reason, surfaced


def test_answer_never_raises_on_a_research_blowup():
    def boom(q, k=3):
        raise RuntimeError("ddg fell over")

    out = news.answer("news about chickens", research=boom)
    assert "couldn't pull" in out.lower()                # honest, not a crash


# --- the LIVE wire: router NEWS intent → core dispatch → news.headlines ----------------

def test_news_route_dispatches_through_core_as_a_grounded_capability(mem, fake_brain, monkeypatch):
    """router.route → core._capability_reply → news.answer → news.headlines →
    researcher.research. The paid brain must not run; the reply is a CAPABILITY
    (real web facts) and is never stored as a durable turn (live state goes stale)."""
    fake_brain.respond = AssertionError("the paid lane must not be used for news")
    monkeypatch.setattr(
        "utah.product.researcher.research",
        lambda q, k=3: {"query": q, "sources": 1, "facts": 1, "stored": 1,
                        "fact_list": ["The Fed held rates steady this week."]})

    reply = core.tell("latest news on the fed")

    assert reply.source is ReplySource.CAPABILITY
    assert "The Fed held rates steady this week." in reply.text
    assert all(r.source != "turn" for r in mem.store.rows.values())


def test_news_stream_matches_tell_and_skips_recall(mem, fake_brain, monkeypatch):
    """Streaming path must match tell(): the news capability answers BEFORE recall
    (same instant-lane contract as weather), so voice/chat never pay a vector
    round-trip for a deterministic capability."""
    def boom_recall(*a, **k):
        raise AssertionError("recall must not run before the news capability")

    monkeypatch.setattr("utah.memory.recall", boom_recall)
    monkeypatch.setattr(
        "utah.product.researcher.research",
        lambda q, k=3: {"query": q, "sources": 1, "facts": 1, "stored": 1,
                        "fact_list": ["Atlanta approved the new transit line."]})

    events = list(core.tell_stream("headlines for atlanta", voice=True))

    assert ("source", "capability") in events
    answer = "".join(c for ch, c in events if ch == "answer")
    assert "Atlanta approved the new transit line." in answer

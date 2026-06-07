"""The Mark Douglas knowledge pack: the REAL Trading-in-the-Zone rules seeded as
durable facts so recall (not flaky model memory) answers them. The store is
injectable; seeding is idempotent and admits only ``source="fact"``."""
from __future__ import annotations

from utah.knowledge import douglas


class _RecordingStore:
    def __init__(self):
        self.calls = []

    def __call__(self, content, *, source, confidence):
        self.calls.append({"content": content, "source": source, "confidence": confidence})
        return len(self.calls)


def test_pack_has_five_truths_and_seven_principles():
    assert len(douglas.FIVE_TRUTHS) == 5
    assert len(douglas.SEVEN_PRINCIPLES) == 7


def test_seed_stores_every_fact_as_durable_fact():
    store = _RecordingStore()
    n = douglas.seed(store=store)
    assert n == len(douglas.FACTS)
    assert n == 1 + 5 + 7                       # summary + truths + principles
    assert all(c["source"] == "fact" for c in store.calls)
    assert all(c["confidence"] >= 0.9 for c in store.calls)


def test_facts_name_mark_douglas_and_contain_the_real_content():
    joined = " ".join(douglas.FACTS).lower()
    assert "mark douglas" in joined
    assert "anything can happen" in joined                 # truth 1, verbatim
    assert "predefine the risk" in joined                  # principle 2, verbatim
    assert "trading in the zone" in joined


def test_matches_only_the_pack_topic():
    assert douglas.matches("what are mark douglas's trading rules")
    assert douglas.matches("explain the trading in the zone principles")
    assert douglas.matches("list the five fundamental truths")
    assert not douglas.matches("what are my trading rules")
    assert not douglas.matches("what's the weather")


def test_render_is_the_real_verbatim_pack():
    text = douglas.render()
    # every real truth and principle is present, verbatim
    for t in douglas.FIVE_TRUTHS:
        assert t in text
    for p in douglas.SEVEN_PRINCIPLES:
        assert p in text
    assert "Five Fundamental Truths" in text and "Seven Principles of Consistency" in text


def test_answer_returns_pack_on_match_else_none():
    assert douglas.answer("mark douglas rules") is not None
    assert "Anything can happen." in douglas.answer("mark douglas rules")
    assert douglas.answer("what's for dinner") is None


def test_seed_survives_a_store_failure_on_one_fact():
    class _Flaky(_RecordingStore):
        def __call__(self, content, *, source, confidence):
            if "truth 3" in content.lower():
                raise RuntimeError("admission denied")
            return super().__call__(content, source=source, confidence=confidence)

    store = _Flaky()
    n = douglas.seed(store=store)
    assert n == len(douglas.FACTS) - 1          # the one failure is skipped, not fatal

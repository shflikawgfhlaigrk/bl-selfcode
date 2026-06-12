"""Consolidation pass bounds: the per-pass turn limit is honored and clamped
(a negative LIMIT is a Postgres error — the maintenance cron must degrade to a
no-op, never crash), the per-turn fact cap holds even if the extractor
over-returns, and malformed extractor output (non-string / blank "facts") is
counted as skipped without aborting the pass."""
from __future__ import annotations

from utah import memory
from utah.consolidate import consolidate


def test_limit_bounds_one_pass_and_the_rest_is_picked_up_next(mem, fake_brain):
    for i in range(3):
        memory.store(f"Q: note {i}\nA: ok", source="turn")
    fake_brain.respond = "[]"

    first = consolidate(limit=2)
    assert first.turns_seen == 2

    second = consolidate(limit=2)
    assert second.turns_seen == 1  # the remaining turn, not a re-scan of all 3


def test_negative_limit_is_clamped_to_zero_not_passed_to_the_backend(
    mem, fake_brain, monkeypatch
):
    """The real backend runs ``LIMIT %s`` — a negative value is a Postgres
    error that would abort the whole pass. The clamp must happen here."""
    seen = {}
    orig = mem.store.unpromoted_turns

    def spy(limit):
        seen["limit"] = limit
        return orig(limit)

    monkeypatch.setattr(mem.store, "unpromoted_turns", spy)
    memory.store("Q: x\nA: y", source="turn")
    report = consolidate(limit=-3)
    assert seen["limit"] == 0
    assert report.turns_seen == 0
    # the turn is NOT marked: a zero-width pass must not eat it
    next_pass = consolidate(limit=10)
    assert next_pass.turns_seen == 1


def test_per_turn_fact_cap_holds_even_if_the_extractor_over_returns(
    mem, fake_brain, monkeypatch
):
    """extract_facts caps at MAX_FACTS_PER_TURN itself, but consolidation must
    not trust that (belt and suspenders: a future extractor bug must not let
    one noisy turn flood the store)."""
    from utah import brain, config

    memory.store("Q: noisy\nA: ok", source="turn")
    monkeypatch.setattr(
        brain, "extract_facts", lambda content: [f"fact {i}" for i in range(50)]
    )
    report = consolidate()
    assert report.facts_promoted == config.MAX_FACTS_PER_TURN


def test_non_string_or_blank_facts_are_skipped_not_a_pass_abort(
    mem, fake_brain, monkeypatch
):
    """A malformed extractor result (None / int / whitespace) hitting
    memory.store raw would raise an untyped AttributeError — aborting the pass
    and stranding the turn forever. It must count as skipped and move on."""
    from utah import brain

    turn = memory.store("Q: junk\nA: ok", source="turn")
    monkeypatch.setattr(
        brain, "extract_facts", lambda content: ["good fact", None, 42, "   "]
    )
    report = consolidate()
    assert report.facts_promoted == 1
    assert report.facts_skipped == 3
    assert "good fact" in mem.store.live_contents()
    assert "promoted" in mem.store.get(turn.id).tags  # turn completed, not stranded


def test_one_turn_brain_failure_does_not_block_the_others(mem, fake_brain, monkeypatch):
    """The brain dying for ONE turn leaves that turn for retry; every other
    turn in the same pass still promotes (failure isolation per turn)."""
    from utah import brain

    bad = memory.store("Q: flaky\nA: ok", source="turn")
    good = memory.store("Q: solid\nA: ok", source="turn")

    def extractor(content):
        if "flaky" in content:
            return None  # brain unavailable for this turn only
        return ["Michael keeps going"]

    monkeypatch.setattr(brain, "extract_facts", extractor)
    report = consolidate()
    assert report.brain_failures == 1
    assert report.facts_promoted == 1
    assert "promoted" not in mem.store.get(bad.id).tags  # retried next pass
    assert "promoted" in mem.store.get(good.id).tags

"""Memory-confabulation guards.

Two ways Utah could poison its own recall (the AceOS confabulation failure mode):

1. The always-on mic transcribes room speech and grunts; ``_remember_turn`` would
   store them as confidence-0.5 ``turn`` facts that recall #1 next time. Guarded by
   ``_is_substantive_turn``.
2. ``SOURCE_BOOST`` is an additive prior on the reranker's logit score. The reranker's
   relevant-vs-irrelevant spread is only ~1–2 logits; a 1.5 prior (same scale) could
   lift an *irrelevant* curated row over a *relevant* fact. It must stay ≤ ENTITY_BOOST
   so it only breaks genuine ties.
"""
import pytest

from utah import config, core


class _RecordingStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, content, *, source, confidence):
        self.calls.append((content, source, confidence))


@pytest.fixture
def store_spy(monkeypatch):
    spy = _RecordingStore()
    monkeypatch.setattr(core.memory, "store", spy)
    monkeypatch.setattr(core.local, "is_refusal", lambda _t: False)
    return spy


def test_substantive_turn_is_remembered(store_spy):
    core._remember_turn("who invented penicillin", "Alexander Fleming, in 1928.")
    assert len(store_spy.calls) == 1
    content, source, conf = store_spy.calls[0]
    assert source == "turn"
    assert "Fleming" in content


@pytest.mark.parametrize(
    "noise", ["No,no,no", "no no no no", "uh", "uh?", "?", "ok", "", "hmm hmm", "  ", "..."]
)
def test_mic_noise_is_not_remembered(store_spy, noise):
    core._remember_turn(noise, "some reply that would otherwise be stored")
    assert store_spy.calls == []


def test_refusal_is_not_remembered(monkeypatch):
    spy = _RecordingStore()
    monkeypatch.setattr(core.memory, "store", spy)
    monkeypatch.setattr(core.local, "is_refusal", lambda _t: True)
    core._remember_turn("a genuinely substantive question here", "I don't know.")
    assert spy.calls == []


@pytest.mark.parametrize(
    "exc", [core.MemoryUnavailable, core.EmbedError, core.AdmissionDenied]
)
def test_store_failure_is_swallowed_reply_already_sent(monkeypatch, exc):
    """The error-handling branch of the remember write path: ``memory.store`` may
    fail (backend down, embedder error, admission gate rejection) AFTER the reply
    has already gone out. Storage is best-effort, so the failure must be caught and
    logged — never re-raised — or a recall-write hiccup would crash a turn whose
    answer the user already received."""
    def boom(*_a, **_k):
        raise exc("backend exploded")

    monkeypatch.setattr(core.memory, "store", boom)
    monkeypatch.setattr(core.local, "is_refusal", lambda _t: False)

    # Must return cleanly (None) — the exception is swallowed, not propagated.
    assert core._remember_turn("who invented penicillin", "Alexander Fleming.") is None


def test_is_substantive_predicate():
    assert core._is_substantive_turn("define entropy")
    assert core._is_substantive_turn("who is michael barber")
    assert not core._is_substantive_turn("No,no,no")
    assert not core._is_substantive_turn("uh")
    assert not core._is_substantive_turn("ok")
    assert not core._is_substantive_turn("")
    assert not core._is_substantive_turn("hmm hmm")


def test_source_boost_is_a_tiebreaker_not_an_override():
    # Bounded by ENTITY_BOOST so a strong relevance match in ANY source still wins.
    assert max(config.SOURCE_BOOST.values()) <= config.ENTITY_BOOST
    # Authority ordering preserved: core >= knowledge >= code.
    sb = config.SOURCE_BOOST
    assert sb["core"] >= sb["knowledge"] >= sb["code"] > 0

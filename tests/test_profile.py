"""Profile + librarian capability — the boundary contract: blank input is refused
before it can pollute memory, a dead backend degrades honestly (never raises into the
caller), and browse is bounded. All seams injected; no real Postgres."""
from __future__ import annotations

from utah import failures, profile
from tests.fakes import FakeFailureStore


class _Hit:
    def __init__(self, content: str, score: float) -> None:
        self.content, self.score = content, score


# ── remember_profile ─────────────────────────────────────────────────────────
def test_remember_stores_via_injected_store_and_returns_id():
    seen = {}

    def store(fact):
        seen["fact"] = fact
        return type("Res", (), {"id": 7})()

    r = profile.remember_profile("Michael's business is Black Label Bots", store=store)
    assert r == {"stored": True, "id": 7}
    assert seen["fact"] == "Michael's business is Black Label Bots"


def test_remember_rejects_blank_fact_without_touching_store():
    """The mic-noise confabulation guard: a blank 'fact' must never reach memory."""
    calls = []
    for blank in ("", "   ", "\t\n", None):
        r = profile.remember_profile(blank, store=lambda f: calls.append(f))
        assert r["stored"] is False and "blank" in r["error"]
    assert calls == []


def test_remember_store_failure_is_honest_and_documented():
    store = FakeFailureStore()
    failures.set_store(store)

    def boom(fact):
        raise RuntimeError("pg down")

    r = profile.remember_profile("a real durable fact", store=boom)
    assert r["stored"] is False and "pg down" in r["error"]
    assert any(kind == "store_failed" for _, _, kind, _ in store.rows)  # documented, not silent


# ── browse (librarian) ───────────────────────────────────────────────────────
def test_browse_maps_hits_to_plain_rows_with_rounded_scores():
    hits = [_Hit("Michael lives in GA", 0.91234), _Hit("Ace is the agent", 0.5)]
    rows = profile.browse("michael", recall=lambda q, k: hits)
    assert rows == [{"content": "Michael lives in GA", "score": 0.91},
                    {"content": "Ace is the agent", "score": 0.5}]


def test_browse_blank_query_is_empty_and_never_hits_recall():
    calls = []
    assert profile.browse("   ", recall=lambda q, k: calls.append(1) or []) == []
    assert calls == []


def test_browse_recall_failure_degrades_to_empty_never_raises():
    def boom(q, k):
        raise RuntimeError("memory backend dead")

    assert profile.browse("anything", recall=boom) == []   # deck gets an empty shelf, not a trace


def test_browse_clamps_k_into_bounds():
    seen = {}

    def recall(q, k):
        seen["k"] = k
        return []

    profile.browse("q", k=10_000, recall=recall)
    assert seen["k"] == profile.MAX_BROWSE_K
    profile.browse("q", k=-3, recall=recall)
    assert seen["k"] == 1


def test_browse_none_recall_result_is_empty():
    assert profile.browse("q", recall=lambda q, k: None) == []


def test_browse_survives_malformed_hit_scores():
    """A hit with a junk score must not blow up the librarian row-mapping."""
    rows = profile.browse("q", recall=lambda q, k: [_Hit("ok", "not-a-number")])
    assert rows == [] or all(isinstance(r["score"], float) for r in rows)


# ── is_blank ─────────────────────────────────────────────────────────────────
def test_is_blank():
    assert profile.is_blank(None) and profile.is_blank("") and profile.is_blank(" \t ")
    assert not profile.is_blank("x")

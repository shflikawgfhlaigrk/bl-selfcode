"""Profile/librarian hardening — the never-raises contract under HOSTILE inputs:
a junk ``k`` (the deck passes query-string values), a single malformed hit, and
non-string content must each degrade per-item, never blank the whole shelf and
never raise into the deck/voice caller. All seams injected; no real Postgres."""
from __future__ import annotations

from utah import profile


class _Hit:
    def __init__(self, content, score) -> None:
        self.content, self.score = content, score


# ── k coercion: never-raises even for junk k ─────────────────────────────────
def test_browse_string_k_is_coerced():
    seen = {}

    def recall(q, k):
        seen["k"] = k
        return []

    profile.browse("q", k="5", recall=recall)
    assert seen["k"] == 5


def test_browse_junk_k_degrades_to_default_not_a_raise():
    """int('garbage') used to blow up BEFORE the boundary try — a deck request with a
    malformed k must fall back to the default page size, never a stack trace."""
    seen = {}

    def recall(q, k):
        seen["k"] = k
        return ["ignored"] if False else []

    rows = profile.browse("q", k="garbage", recall=recall)
    assert rows == []
    assert seen["k"] == profile.DEFAULT_BROWSE_K          # honest default, recall still ran


def test_browse_none_k_degrades_to_default():
    seen = {}
    profile.browse("q", k=None, recall=lambda q, k: seen.update(k=k) or [])
    assert seen["k"] == profile.DEFAULT_BROWSE_K


# ── per-hit degradation: one bad hit must not blank the shelf ────────────────
def test_one_malformed_hit_keeps_the_good_hits():
    hits = [_Hit("good one", 0.9), _Hit("bad score", "not-a-number"), _Hit("good two", 0.5)]
    rows = profile.browse("q", recall=lambda q, k: hits)
    assert [r["content"] for r in rows] == ["good one", "bad score", "good two"]
    assert rows[1]["score"] == 0.0                        # malformed score → neutral, not a wipe
    assert rows[0]["score"] == 0.9 and rows[2]["score"] == 0.5


def test_non_string_content_is_coerced_to_str():
    rows = profile.browse("q", recall=lambda q, k: [_Hit({"nested": 1}, 0.4)])
    assert len(rows) == 1 and isinstance(rows[0]["content"], str)


def test_hit_without_attrs_falls_back_to_its_repr():
    rows = profile.browse("q", recall=lambda q, k: ["a bare string hit"])
    assert rows == [{"content": "a bare string hit", "score": 0.0}]


# ── remember_profile: store result without an id is still honest ─────────────
def test_remember_store_without_id_reports_stored_none_id():
    r = profile.remember_profile("fact", store=lambda f: object())
    assert r == {"stored": True, "id": None}

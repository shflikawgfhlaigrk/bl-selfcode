"""Ace knowledge migration — bring ace.db semantic_memory into Utah's Postgres memory
through the admission gate (dedup + no-fab), with a junk filter so transient scrape/news
telemetry never pollutes Utah (Michael's no-injection rule + 'everything, gated' choice).
Lives in migrations/ (not utah/) so the runtime stays SQLite-free. Failures documented."""
from __future__ import annotations

from migrations import ace_knowledge as ak
from utah.memory import WriteAction, WriteResult, AdmissionDenied


def test_junk_filter_drops_scrape_and_transient_keeps_facts():
    assert ak.is_junk("[TRUMP_TRUTH] The Obama Library in 10 years!", "ace") is True
    assert ak.is_junk("[No Title] - Post from June 3, 2026", "ace") is True
    assert ak.is_junk("anything", "notifier") is True          # transient agent
    assert ak.is_junk("ok", "ace") is True                     # too short to be a fact
    assert ak.is_junk("Michael maintains a project he calls 'Utah'.", "ace") is False


def test_migrate_classifies_inserted_deduped_rejected_junk():
    calls = {"n": 0}

    def fake_store(content):
        calls["n"] += 1
        if "dup" in content:
            return WriteResult(id=1, action=WriteAction.REINFORCED)
        if "bad" in content:
            raise AdmissionDenied("disallowed")
        return WriteResult(id=calls["n"], action=WriteAction.INSERTED)

    rows = [
        ("ace", "Michael maintains a project called Utah on Postgres."),  # inserted
        ("ace", "a duplicate fact about the utah project here"),         # deduped
        ("ace", "a bad fact that the gate rejects entirely now"),        # rejected
        ("notifier", "transient ping notification"),                     # junk (agent)
        ("ace", "[TRUMP_TRUTH] news scrape noise"),                      # junk (marker)
    ]
    stats = ak.migrate(rows, store_fn=fake_store)
    assert stats["read"] == 5
    assert stats["junk"] == 2
    assert stats["inserted"] == 1
    assert stats["deduped"] == 1
    assert stats["rejected"] == 1
    assert calls["n"] == 3                                       # only non-junk hit the gate

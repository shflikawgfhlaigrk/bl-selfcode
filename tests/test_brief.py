"""Morning brief capability — Ace's brief transitions here (NOT an agent): compose a brief
from LIVE Utah state (revenue ledger, memory, open items) — grounded in real Postgres, never
fabricated. Delivery: spoken via Piper (ungated) and/or email (GATED on Gmail creds, which is
documented). compose_brief is pure; the gate + delivery are tested with injected boundaries."""
from __future__ import annotations

from utah import failures
from utah.failures import FailureRow
from utah.product import brief
from tests.fakes import FakeFailureStore


def _frow(source, kind, detail=""):
    return FailureRow(source=source, kind=kind, detail=detail)


def test_compose_brief_includes_live_state():
    text = brief.compose_brief(
        ledger_counts={"leads": 164, "probate": 0, "outreach_ledger": 10, "fires": 0},
        memory_live=1911,
        failures_recent=[_frow("probate", "zero_ring"), _frow("outreach", "send_gated")],
        leads_recent=[{"name": "Foxtail Coffee Co."}, {"name": "Red Door Consignment"}],
    )
    assert "164" in text and "1911" in text          # real numbers, not faked
    assert "Foxtail Coffee Co." in text              # real lead
    assert "probate/zero_ring" in text               # open items surfaced
    assert "MORNING BRIEF" in text.upper()


def test_compose_brief_surfaces_real_estate_intel():
    """The probate property intel (ARV + resolved address) reaches Michael in the daily
    brief — it existed in the ledger for days without ever being DELIVERED (2026-06-10)."""
    text = brief.compose_brief(
        ledger_counts={"leads": 1, "probate": 64, "outreach_ledger": 0, "fires": 0},
        memory_live=5, failures_recent=[], leads_recent=[],
        probate_top=[{"case_name": "JEAN OSBORN SAWYER", "county": "hall",
                      "arv": 913400, "address": "434 THUNDER ROAD"}],
    )
    assert "JEAN OSBORN SAWYER" in text and "913,400" in text
    assert "434 THUNDER ROAD" in text and "REAL ESTATE" in text.upper()


def test_compose_brief_omits_real_estate_section_when_empty():
    text = brief.compose_brief(
        ledger_counts={"leads": 1, "probate": 0, "outreach_ledger": 0, "fires": 0},
        memory_live=5, failures_recent=[], leads_recent=[], probate_top=[],
    )
    assert "real estate" not in text.lower()           # never an empty fabricated section


def test_run_speaks_and_documents_email_gate():
    store = FakeFailureStore(); failures.set_store(store)
    spoken = []
    r = brief.run(
        gather=lambda: {"ledger_counts": {"leads": 1, "probate": 0, "outreach_ledger": 0, "fires": 0},
                        "memory_live": 5, "failures_recent": [], "leads_recent": []},
        speak_fn=spoken.append, can_email=False,
    )
    assert r["spoke"] is True
    assert r["emailed"] is False and r["email_gated"]
    assert spoken                                     # the brief was spoken (Piper, ungated)
    assert any("gated" in row[2] for row in store.rows)   # email gate documented


def test_run_emails_when_enabled():
    failures.set_store(FakeFailureStore())
    sent = []
    r = brief.run(
        gather=lambda: {"ledger_counts": {"leads": 0, "probate": 0, "outreach_ledger": 0, "fires": 0},
                        "memory_live": 0, "failures_recent": [], "leads_recent": []},
        speak_fn=None, can_email=True, email_fn=lambda text: sent.append(text) or True,
    )
    assert r["emailed"] is True and not r["email_gated"]
    assert sent


def test_compose_brief_shows_3mile_average_when_present():
    """The '3-mile average' Michael flagged missing (2026-06-10): when the probate row
    carries area_avg_3mi, the brief renders it next to the parcel's own ARV."""
    text = brief.compose_brief(
        ledger_counts={"leads": 1, "probate": 64, "outreach_ledger": 0, "fires": 0},
        memory_live=5, failures_recent=[], leads_recent=[],
        probate_top=[{"case_name": "JEAN OSBORN SAWYER", "county": "hall",
                      "arv": 913400, "address": "434 THUNDER ROAD",
                      "area_avg": 152340}],
    )
    assert "3mi avg $152,340" in text


def test_compose_brief_omits_3mile_average_when_gated():
    text = brief.compose_brief(
        ledger_counts={"leads": 1, "probate": 64, "outreach_ledger": 0, "fires": 0},
        memory_live=5, failures_recent=[], leads_recent=[],
        probate_top=[{"case_name": "JEAN OSBORN SAWYER", "county": "bryan",
                      "arv": 913400, "address": "434 THUNDER ROAD"}],
    )
    assert "3mi avg" not in text                       # gated county: no fabricated number


def test_compose_brief_shows_week_rollup_when_present():
    text = brief.compose_brief(
        ledger_counts={}, memory_live=0, failures_recent=[], leads_recent=[],
        leads_week=[{"region": "atlanta", "n": 12}, {"region": "macon", "n": 9}])
    assert "Last 7 days: 21 new leads" in text
    assert "atlanta (12)" in text and "macon (9)" in text


def test_compose_brief_omits_week_rollup_when_unavailable():
    text = brief.compose_brief(
        ledger_counts={}, memory_live=0, failures_recent=[], leads_recent=[],
        leads_week=[])
    assert "Last 7 days" not in text


def test_leads_week_rollup_rides_the_olap_tier():
    seen = {}
    def fake_query(sql, **kw):
        seen["sql"] = sql
        return [("atlanta", 12), ("macon", 9)]
    rows = brief._leads_week_rollup(query_fn=fake_query)
    assert rows == [{"region": "atlanta", "n": 12}, {"region": "macon", "n": 9}]
    assert "pg.public.leads" in seen["sql"]  # the analytic reads the attached primary


def test_leads_week_rollup_degrades_honestly_when_olap_down():
    from utah.store import olap
    def boom(sql, **kw):
        raise olap.OlapError("attach failed")
    assert brief._leads_week_rollup(query_fn=boom) == []

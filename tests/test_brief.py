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

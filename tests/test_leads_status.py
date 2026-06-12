"""The leads-status capability — answers lead/pipeline questions from the LIVE ledger so
the chat/voice brain never fabricates counts again (it once served '500+0+0=721'). Pure
formatting is unit-tested with injected counts; the DB read is exercised separately."""
from __future__ import annotations

from utah.product import leads_status

_FAKE = {"total": 4666, "by_source": [("osm", 4218), ("google_maps", 448)],
         "with_email": 143, "today": 0,
         "recent": [("Jun 10", 447), ("Jun 09", 594), ("Jun 08", 1412)], "probate": 138}


def test_answer_uses_only_real_counts():
    ans = leads_status.answer("how many leads", counts_fn=lambda: _FAKE)
    assert "4,666" in ans                       # real total, comma-grouped
    assert "4,218" in ans and "448" in ans      # real source split
    assert "143" in ans                         # with-email
    assert "138" in ans                         # probate present
    assert "721" not in ans                     # the old fabricated number never appears


def test_answer_reports_today_and_recent_truthfully():
    ans = leads_status.answer("today's lead count", counts_fn=lambda: _FAKE)
    assert "0" in ans                           # today is honestly zero
    assert "447" in ans                         # most-recent active day surfaced


def test_answer_is_honest_when_ledger_unreachable():
    def boom():
        raise RuntimeError("pg down")
    ans = leads_status.answer("how many leads", counts_fn=boom).lower()
    assert ("can't" in ans or "couldn't" in ans or "unavailable" in ans)
    assert "guess" not in ans or "won't guess" in ans   # never fabricates on failure

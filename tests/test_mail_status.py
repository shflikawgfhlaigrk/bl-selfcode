"""The mail-status capability — answers "how many emails/texts did we send" from the LIVE
mail_ledger so the chat/voice brain stops giving the "I can't reach the ledger" runaround
(2026-06-13). Pure formatting is unit-tested with injected counts; the DB read is separate."""
from __future__ import annotations

from utah.product import mail_status

_FAKE = {"today": [("email", "sent", 58), ("email", "bounced", 2)], "total": 214,
         "first": "06:35:05", "last": "09:00:21",
         "recent": [("Jun 13", 58), ("Jun 12", 47), ("Jun 11", 30)]}


def test_answer_uses_only_real_counts():
    ans = mail_status.answer("how many emails sent this morning", counts_fn=lambda: _FAKE)
    assert "58" in ans                      # real sent count
    assert "06:35:05" in ans and "09:00:21" in ans   # real first/last
    assert "2 bounced" in ans               # real bounce count surfaced


def test_answer_handles_a_quiet_day():
    quiet = {"today": [], "total": 100, "first": None, "last": None, "recent": []}
    ans = mail_status.answer("emails today", counts_fn=lambda: quiet).lower()
    assert "0 email" in ans and ("nothing sent yet" in ans)


def test_answer_is_honest_when_ledger_unreachable():
    def boom():
        raise RuntimeError("pg down")
    ans = mail_status.answer("how many emails sent", counts_fn=boom).lower()
    assert "can't reach" in ans
    assert "won't guess" in ans              # never fabricates on failure

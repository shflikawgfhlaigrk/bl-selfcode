"""Lead-pipeline funnel + system-scope scheme (utah/product/pipeline.py).

Grounding contract, same doctrine as the leads-status capability: every funnel number is a
real ``COUNT(*)`` or an honest error — NEVER a guess. The DB connection is injected so the
funnel/scope logic is exercised without a live Postgres; a dead ledger yields ``error``,
never a fabricated funnel.
"""
from __future__ import annotations

from utah.product import pipeline


class _FakeCursor:
    def __init__(self, value: int) -> None:
        self._value = value

    def fetchone(self):
        return (self._value,)


class _FakeConn:
    """A connection whose ``execute(sql, params)`` defers to a responder(sql, params)->int.
    Closing is a no-op (the capability closes it; the fake must tolerate that)."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self.closed = False

    def execute(self, sql, params=()):
        return _FakeCursor(self._responder(sql, params))

    def close(self):
        self.closed = True


def _responder(sql, params):
    """Map each funnel query to a deterministic count by its distinguishing predicate.
    Specific predicates are checked before general ones (order matters)."""
    s = " ".join(sql.split())  # normalize whitespace
    # --- leads lane (most specific first) ---
    if "FROM leads" in s and "status = 'contacted'" in s:
        return 197
    if "FROM leads" in s and "website','') <> ''" in s and "email','') = ''" in s:
        return 1514                                   # site, no email — harvest backlog
    if "FROM leads" in s and "phone','') <> ''" in s and "email','') = ''" in s:
        return 1973                                   # phone, no email — SMS lane
    if "FROM leads" in s and "phone','') <> ''" in s and "OR coalesce(contact->>'email" in s:
        return 2328                                   # reachable (phone OR email)
    if "FROM leads" in s and "phone','') <> ''" in s:
        return 2310                                   # with phone
    if "FROM leads" in s and "email','') <> ''" in s:
        return 337                                    # with email
    if "FROM leads" in s and "website','') <> ''" in s:
        return 1514                                   # with website
    if "FROM leads WHERE source = ANY(%s)" in s:
        return 7532                                   # discovered (mouth)
    # --- outreach / mail / replies ---
    if "FROM outreach_ledger WHERE channel = 'email'" in s:
        return 170
    if "FROM outreach_ledger WHERE channel = 'sms'" in s:
        return 100
    if "FROM outreach_ledger WHERE campaign = %s" in s:
        return 0
    if "FROM outreach_ledger" in s:
        return 270
    if "FROM mail_ledger WHERE status = 'sent'" in s:
        return 147
    if "FROM mail_ledger WHERE status = 'bounced'" in s:
        return 8
    if "FROM mail_replies" in s:
        return 11
    # --- probate lane ---
    if "FROM probate WHERE arv IS NOT NULL" in s:
        return 22
    if "FROM probate WHERE status = 'contacted'" in s:
        return 0
    if "FROM probate" in s:
        return 230
    raise AssertionError(f"unmapped funnel query: {s}")


def _funnel():
    return pipeline.funnel(conn_fn=lambda: _FakeConn(_responder))


def test_funnel_stages_are_grounded_counts():
    f = _funnel()
    assert "error" not in f
    smb = {s["key"]: s["n"] for s in f["smb"]}
    assert smb["discovered"] == 7532
    assert smb["reachable"] == 2328
    assert smb["email"] == 337
    assert smb["contacted"] == 197
    assert smb["sent"] == 270
    assert smb["delivered"] == 147
    assert smb["replied"] == 11
    assert smb["won"] == 0                       # honest zero — no sale has landed


def test_funnel_is_monotonic_at_the_mouth():
    """No stage may claim a larger share than the funnel mouth that feeds it."""
    f = _funnel()
    mouth = f["smb"][0]["n"]
    for stage in f["smb"]:
        assert stage["of"] == mouth
        assert 0.0 <= stage["n"] / mouth <= 1.0 if mouth else True


def test_diagnosis_names_the_email_leak_and_a_free_fix():
    f = _funnel()
    diag = f["diagnosis"]
    assert diag["email_rate"] == round(100 * 337 / 7532, 1)   # 4.5% — computed, not painted
    assert "%" in diag["headline"]
    titles = " ".join(l["title"] for l in diag["leaks"]).lower()
    assert "harvest" in titles                  # the website-email backlog leak
    assert any(l["free"] for l in diag["leaks"])  # every prescribed fix is free
    assert any(l["n"] == 1514 for l in diag["leaks"])  # grounded in the real backlog count


def test_probate_lane_present_and_grounded():
    f = _funnel()
    prob = {s["key"]: s["n"] for s in f["probate"]}
    assert prob["cases"] == 230
    assert prob["valued"] == 22


def test_funnel_is_honest_when_ledger_unreachable():
    def boom():
        raise RuntimeError("pg down")
    f = pipeline.funnel(conn_fn=boom)
    assert "error" in f
    assert "smb" not in f                        # never a fabricated funnel on failure


# -- scope() : the system scheme ---------------------------------------------------------

_FAKE_COUNTS = {"leads": 7532, "probate": 230, "outreach_ledger": 270, "mail_ledger": 155,
                "marketer_posts": 6, "memory": 9000, "mem_entity": 400, "trade_lore": 50,
                "fires": 2107, "wc_live": 1, "bars": 0, "selfcode_log": 30,
                "selfcode_archive": 12, "tasks": 5, "failures": 100, "sync_log": 17,
                "timers": 2, "signals_subscribers": 0}
_FAKE_CRONS = ["leads", "enrich", "outreach", "probate", "selfcode", "wcfeed"]


def _scope():
    return pipeline.scope(counts_fn=lambda: _FAKE_COUNTS, crons_fn=lambda: _FAKE_CRONS)


def test_scope_groups_every_area_with_live_counts():
    sc = _scope()
    assert sc["groups"]
    # every area carries a count key (live or honest None), and group totals sum its areas.
    for g in sc["groups"]:
        assert all("count" in a for a in g["areas"])
        assert g["rows"] == sum(a["count"] for a in g["areas"] if isinstance(a["count"], int))
    # the revenue group surfaces the lead table count
    rev = next(g for g in sc["groups"] if "Revenue" in g["group"])
    leads_area = next(a for a in rev["areas"] if a["name"] == "Leads (SMB)")
    assert leads_area["count"] == 7532
    assert "grow" in leads_area                  # the expansion surface is named


def test_scope_totals_and_real_crons():
    sc = _scope()
    assert sc["totals"]["rows"] == sum(v for v in _FAKE_COUNTS.values())
    assert sc["totals"]["crons"] == len(_FAKE_CRONS)
    assert "enrich" in sc["crons"]               # real launchd job surfaced


def test_scope_degrades_honestly_when_counts_unavailable():
    """A dead ledger yields None counts, never fabricated zeros that read as 'empty'."""
    none_counts = {t: None for t in _FAKE_COUNTS}
    sc = pipeline.scope(counts_fn=lambda: none_counts, crons_fn=lambda: [])
    assert sc["totals"]["rows"] == 0
    for g in sc["groups"]:
        assert all(a["count"] is None for a in g["areas"])

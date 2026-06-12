"""The conversational ACTION capability — "rerun the leads" actually RUNS the finder
and reports the REAL net-new count, never a fabricated "done".

Two layers are pinned here:

* router intent — run/rerun verbs over a capability noun ("rerun the lead scout",
  "run the 500 leads again", "run outreach", "rerun probate", "run the engines")
  route to ``Route.ACTION`` BEFORE the read capabilities, fixing the bug where
  "rerun the lead scout" matched the LEADS *read* capability and only reported counts;

* execution — the action handler calls the REAL capability function and the reply
  reports the REAL returned numbers (proven with a fake executor) and is HONEST on
  failure ("I couldn't run it … <reason>"), never a painted success.

The existing leads_status READ capability (asking "how many leads") stays untouched:
a question still reports counts; a command runs the finder.
"""
from __future__ import annotations

from utah import actions
from utah.router import Route, route


# --------------------------------------------------------------------------
# 1. ROUTER — run/rerun verbs route to ACTION, before the read capabilities.
# --------------------------------------------------------------------------

import pytest


@pytest.mark.parametrize("text", [
    "rerun the leads",
    "re-run the leads",
    "run the leads again",
    "run the 500 leads again",
    "rerun the lead scout",
    "run lead scout",
    "scout the leads again",
    "run outreach",
    "rerun outreach",
    "work the leads",
    "rerun probate",
    "run the probate scout",
    "run the engines",
    "rerun the engines",
    "run research on probate law",
])
def test_run_verbs_route_to_action(text):
    assert route(text) is Route.ACTION


@pytest.mark.parametrize("text", [
    "how many leads do we have",
    "what's today's lead count",
    "how many leads did lead_scout find",
    "how many probate cases",
    "what's the pipeline look like",
])
def test_read_questions_still_route_to_leads_read(text):
    # The READ capability must NOT be hijacked by the action route — a *question*
    # about counts still answers from the ledger, it does not re-run the finder.
    assert route(text) is Route.LEADS


def test_action_does_not_steal_plain_agentic_or_social():
    # "run" inside a genuinely agentic request stays BRAIN territory is fine, but a
    # bare social/greeting is never an action.
    assert route("hello") is not Route.ACTION
    assert route("how are you") is not Route.ACTION


# --------------------------------------------------------------------------
# 2. INTENT classification — map the phrase to the right capability.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("rerun the leads", "scout_leads"),
    ("run the 500 leads again", "scout_leads"),
    ("rerun the lead scout", "scout_leads"),
    ("rerun probate", "scout_probate"),
    ("run the probate scout", "scout_probate"),
    ("run outreach", "outreach"),
    ("work the leads", "outreach"),
    ("run the engines", "run_engines"),
    ("run research on probate law", "research"),
])
def test_classify_intent(text, expected):
    assert actions.classify(text) == expected


# --------------------------------------------------------------------------
# 3. EXECUTION — the reply reports the REAL returned numbers (fake executor).
# --------------------------------------------------------------------------

def test_leads_action_reports_real_net_new_and_totals():
    # fake the capability AND the count-reads so no DB is touched; the reply MUST
    # surface the real +net-new and the real before->after totals.
    totals = iter([4666, 5139])  # before, after
    reply = actions.run(
        "rerun the leads",
        run_leads=lambda: {"new": 473, "found": 980, "tiles_scanned": 12},
        total_leads=lambda: next(totals),
    )
    assert "473" in reply                      # the real net-new
    assert "4,666" in reply and "5,139" in reply   # real before -> after totals
    assert "done" != reply.strip().lower()     # never a bare painted "done"


def test_leads_action_is_honest_on_failure():
    def boom():
        raise RuntimeError("Overpass mirrors all down")
    reply = actions.run(
        "rerun the leads",
        run_leads=boom,
        total_leads=lambda: 4666,
    ).lower()
    assert "couldn't" in reply or "could not" in reply or "failed" in reply
    assert "overpass" in reply                 # the REAL reason is surfaced, honestly
    assert "473" not in reply                   # no fabricated success number


def test_outreach_action_reports_real_sent_count():
    reply = actions.run(
        "run outreach",
        run_outreach=lambda: {"campaign": "smb_no_website", "channel": "auto",
                              "sent": 17, "queued": 22},
    )
    assert "17" in reply
    assert "sent" in reply.lower()


def test_outreach_action_reports_send_gate_skip_honestly():
    # the existing business-hours / deliverability gate returns skipped=True — the
    # action must REPORT that truthfully (it is legal compliance, not a fabricated win).
    reply = actions.run(
        "run outreach",
        run_outreach=lambda: {"campaign": "smb_no_website", "sent": 0, "queued": 0,
                              "skipped": True, "reason": "outside business hours"},
    ).lower()
    assert "0" in reply
    assert "business hours" in reply or "skipped" in reply or "didn't send" in reply


def test_probate_action_reports_real_new_count():
    totals = iter([138, 152])
    reply = actions.run(
        "rerun probate",
        run_probate=lambda: {"new": 14, "found": 40, "region": "Georgia (statewide)"},
        total_probate=lambda: next(totals),
    )
    assert "14" in reply
    assert "138" in reply and "152" in reply


def test_engines_action_reports_gated_truthfully():
    reply = actions.run(
        "run the engines",
        run_engines=lambda: {"fires": 0, "gated": True, "signal": None},
    ).lower()
    assert "0" in reply
    assert "gated" in reply or "feed" in reply   # honest about the WC-feed gate


def test_engines_action_reports_real_fire():
    reply = actions.run(
        "run the engines",
        run_engines=lambda: {"fires": 1, "signal": {"engine": "breakout"}, "fire_id": 9},
    )
    assert "1" in reply
    assert "fire" in reply.lower()


def test_research_action_reports_real_stored_count():
    reply = actions.run(
        "run research on probate law",
        run_research=lambda q: {"query": q, "sources": 4, "facts": 11, "stored": 9},
    )
    assert "9" in reply or "11" in reply
    assert "research" in reply.lower() or "learned" in reply.lower()

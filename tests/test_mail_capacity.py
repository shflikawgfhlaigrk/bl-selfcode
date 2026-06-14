"""mail_capacity — warmup ramp, provider caps, per-account health, and the 3000/day plan.

All pure/offline: dates, first-seen maps, and a fake DB connection are injected so warmup
math, capacity, deliverability, auto-pause, and the honest plan are provable without a
network, a clock, or Postgres.
"""
from __future__ import annotations

import datetime

import pytest

from utah import mail_capacity as mc


DOMAIN_ACCT = {"from": "info@blacklabelbots.com", "smtp_host": "smtp.privateemail.com"}
DOMAIN_ACCT2 = {"from": "delivery@blacklabelbots.com", "smtp_host": "smtp.privateemail.com"}
GMAIL_ACCT = {"from": "mtuburnsbarber@gmail.com", "smtp_host": "smtp.gmail.com"}
TODAY = datetime.date(2026, 6, 14)


# ── provider classification + steady caps ─────────────────────────────────────────────

def test_own_domain_vs_gmail_tiers():
    assert mc.is_own_domain(DOMAIN_ACCT) is True
    assert mc.is_own_domain(GMAIL_ACCT) is False
    assert mc.provider_steady_cap(DOMAIN_ACCT) == mc.DOMAIN_STEADY
    assert mc.provider_steady_cap(GMAIL_ACCT) == mc.GMAIL_STEADY
    assert mc.DOMAIN_STEADY > mc.GMAIL_STEADY  # own domain sustains more than free gmail


# ── warmup ramp: day-0 starts low, climbs, never exceeds the provider steady cap ───────

def test_warmup_ramp_starts_low_and_caps_at_steady():
    # brand new (age 0) → WARMUP_START regardless of provider
    assert mc.safe_daily_cap(DOMAIN_ACCT, 0) == mc.WARMUP_START
    assert mc.safe_daily_cap(GMAIL_ACCT, 0) == mc.WARMUP_START
    # ramps up by WARMUP_STEP/day
    assert mc.safe_daily_cap(DOMAIN_ACCT, 1) == mc.WARMUP_START + mc.WARMUP_STEP
    # gmail tops out at its (lower) steady cap quickly and never passes it
    assert mc.safe_daily_cap(GMAIL_ACCT, 999) == mc.GMAIL_STEADY
    # a fully-warmed own-domain box reaches the higher domain steady cap
    assert mc.safe_daily_cap(DOMAIN_ACCT, 999) == mc.DOMAIN_STEADY


def test_warmup_negative_age_clamped():
    assert mc.safe_daily_cap(DOMAIN_ACCT, -5) == mc.WARMUP_START


# ── first-seen tracking (pure via injected state) ─────────────────────────────────────

def test_record_seen_stamps_new_and_preserves_old():
    state = {"info@blacklabelbots.com": "2026-06-01"}  # already warming
    updated = mc.record_seen([DOMAIN_ACCT, GMAIL_ACCT], today=TODAY, state=state)
    assert updated["info@blacklabelbots.com"] == "2026-06-01"   # preserved (older)
    assert updated["mtuburnsbarber@gmail.com"] == "2026-06-14"  # newly stamped


def test_age_days_from_first_seen():
    fs = {"info@blacklabelbots.com": "2026-06-04"}
    assert mc.age_days_for(DOMAIN_ACCT, fs, today=TODAY) == 10
    assert mc.age_days_for(GMAIL_ACCT, fs, today=TODAY) == 0     # unknown → brand new
    assert mc.age_days_for(DOMAIN_ACCT, {"info@blacklabelbots.com": "garbage"}, today=TODAY) == 0


# ── capacity across the pool, with warmup + pause applied ──────────────────────────────

def test_capacity_sums_warmed_caps_and_excludes_paused():
    fs = {  # both domain boxes fully warmed; gmail fully warmed
        "info@blacklabelbots.com": "2026-01-01",
        "delivery@blacklabelbots.com": "2026-01-01",
        "mtuburnsbarber@gmail.com": "2026-01-01",
    }
    cap = mc.capacity([DOMAIN_ACCT, DOMAIN_ACCT2, GMAIL_ACCT], first_seen=fs, today=TODAY)
    assert cap["total"] == mc.DOMAIN_STEADY * 2 + mc.GMAIL_STEADY
    assert cap["accounts"] == 3 and cap["active"] == 3

    # pause one domain box → its cap drops out of the total
    cap2 = mc.capacity([DOMAIN_ACCT, DOMAIN_ACCT2, GMAIL_ACCT], first_seen=fs,
                       paused={"delivery@blacklabelbots.com"}, today=TODAY)
    assert cap2["total"] == mc.DOMAIN_STEADY + mc.GMAIL_STEADY
    assert cap2["active"] == 2
    assert any(p["from"] == "delivery@blacklabelbots.com" and p["paused"] for p in cap2["per_account"])


def test_capacity_brand_new_pool_is_warmup_start_each():
    cap = mc.capacity([DOMAIN_ACCT, GMAIL_ACCT], first_seen={}, today=TODAY)
    assert cap["total"] == mc.WARMUP_START * 2  # both day-0


# ── how many mailboxes for a target ───────────────────────────────────────────────────

def test_accounts_needed_for_target():
    import math
    assert mc.accounts_needed_for(3000, tier="domain") == math.ceil(3000 / mc.DOMAIN_STEADY)
    assert mc.accounts_needed_for(3000, tier="gmail") == math.ceil(3000 / mc.GMAIL_STEADY)
    assert mc.accounts_needed_for(0) == 0


# ── deliverability + per-account health from a fake ledger connection ──────────────────

class FakeConn:
    """Answers exactly the SQL mail_capacity issues, by substring match."""

    def __init__(self, sent=0, bounced=0, health_rows=None, has_sender=True):
        self._sent, self._bounced = sent, bounced
        self._health = health_rows or []
        self._has_sender = has_sender

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "GROUP BY sender" in s:
            if not self._has_sender:
                raise RuntimeError('column "sender" does not exist')
            return _Cur(self._health)
        if "status='sent'" in s and "count(*) FROM mail_ledger" in s:
            return _Cur([(self._sent,)])
        if "status='bounced'" in s and "count(*) FROM mail_ledger" in s:
            return _Cur([(self._bounced,)])
        raise AssertionError(f"unexpected SQL: {s}")


class _Cur:
    def __init__(self, rows): self._rows = rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows


def test_deliverability_rate():
    d = mc.deliverability(FakeConn(sent=256, bounced=13))
    assert d["sent"] == 256 and d["bounced"] == 13
    assert d["rate_pct"] == 95.2


def test_deliverability_no_sends_is_none_not_fake_100():
    d = mc.deliverability(FakeConn(sent=0, bounced=0))
    assert d["rate"] is None and d["rate_pct"] is None  # honest: unknown, not 100%


def test_account_health_flags_high_bounce_sender():
    rows = [("good@blacklabelbots.com", 100, 2),    # 2% — fine
            ("bad@blacklabelbots.com", 50, 20),     # 28.6% — pull it
            ("tiny@blacklabelbots.com", 3, 2)]      # high rate but < MIN_SENDS → not judged
    health = {h["sender"]: h for h in mc.account_health(FakeConn(health_rows=rows))}
    assert health["good@blacklabelbots.com"]["paused"] is False
    assert health["bad@blacklabelbots.com"]["paused"] is True
    assert health["tiny@blacklabelbots.com"]["paused"] is False  # small-N guard


def test_paused_accounts_set():
    rows = [("bad@blacklabelbots.com", 50, 20), ("good@blacklabelbots.com", 100, 1)]
    assert mc.paused_accounts(FakeConn(health_rows=rows)) == {"bad@blacklabelbots.com"}


def test_health_unenforced_without_sender_column():
    # Older schema (no sender column) → health simply not measured, never guessed/crashes.
    assert mc.account_health(FakeConn(has_sender=False)) == []
    assert mc.paused_accounts(FakeConn(has_sender=False)) == set()


# ── DMARC detection + recommended record ──────────────────────────────────────────────

def test_dmarc_present_detection():
    has = lambda name: ['v=DMARC1; p=none; rua=mailto:x@y']
    none = lambda name: []
    assert mc.dmarc_present("blacklabelbots.com", txt_lookup=has) is True
    assert mc.dmarc_present("blacklabelbots.com", txt_lookup=none) is False


def test_recommended_dmarc_record():
    rec = mc.recommended_dmarc("blacklabelbots.com")
    assert "_dmarc.blacklabelbots.com" in rec and "v=DMARC1" in rec


# ── the honest plan ───────────────────────────────────────────────────────────────────

def test_plan_reports_shortfall_and_steps_when_underprovisioned():
    fs = {"info@blacklabelbots.com": "2026-01-01", "delivery@blacklabelbots.com": "2026-01-01",
          "mtuburnsbarber@gmail.com": "2026-01-01"}
    p = mc.plan([DOMAIN_ACCT, DOMAIN_ACCT2, GMAIL_ACCT],
                conn=FakeConn(sent=256, bounced=13), target=3000,
                first_seen=fs, today=TODAY, dmarc_lookup=lambda n: [])
    assert p["meets_target"] is False
    assert p["safe_capacity_today"] == mc.DOMAIN_STEADY * 2 + mc.GMAIL_STEADY
    assert p["mailbox_shortfall_for_target"] > 0
    assert p["deliverability"]["rate_pct"] == 95.2
    assert p["deliverability_ok"] is True            # 95.2% >= 70
    assert p["dmarc"]["blacklabelbots.com"] is False
    # steps name both the mailbox shortfall and the missing DMARC
    joined = " ".join(p["steps"])
    assert "mailbox" in joined.lower() and "DMARC" in joined


def test_plan_meets_target_when_enough_warmed_boxes():
    import math
    n = math.ceil(3000 / mc.DOMAIN_STEADY)
    boxes = [{"from": f"box{i}@blacklabelbots.com", "smtp_host": "smtp.privateemail.com"}
             for i in range(n)]
    fs = {b["from"]: "2026-01-01" for b in boxes}  # all fully warmed
    p = mc.plan(boxes, conn=FakeConn(sent=1000, bounced=5), target=3000,
                first_seen=fs, today=TODAY, dmarc_lookup=lambda n: ["v=DMARC1; p=none"])
    assert p["safe_capacity_today"] >= 3000
    assert p["meets_target"] is True
    assert p["dmarc"]["blacklabelbots.com"] is True

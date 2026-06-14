"""Deliverability-safe sending CAPACITY — warmup ramp, provider caps, per-account health,
and an honest plan to reach a daily target (e.g. 3000/day) WITHOUT burning reputation.

The naive way to "send 3000/day" is to raise the per-inbox cap — that just trades the
volume for a wrecked spam-complaint rate and a suspended domain (deliverability → 0). The
ONLY way to hold high deliverability at volume is more *warmed* senders, each kept under a
provider-safe daily cap, with any sender whose bounce rate spikes pulled out automatically.

This module is the math + measurement for that, kept separate from the send path (mail.py)
so it composes cleanly and is fully unit-testable off the network/DB:

  * safe_daily_cap(account, age_days)  — provider steady cap, ramped over a warmup window
  * record_seen / first_seen_map       — when each inbox first sent (drives warmup age)
  * capacity(accounts, ...)            — today's TOTAL safe sends across the pool
  * accounts_needed_for(target, ...)   — how many warmed mailboxes a target requires
  * deliverability(conn)               — real sent/bounced/rate from the ledgers
  * paused_accounts / account_health   — bounce-rate-driven auto-pause (holds the rate)
  * plan(...)                          — ties it together: where we stand, what's missing

Nothing here ever fabricates capacity or a deliverability number; a missing input degrades
to the honest conservative value (cap 0, rate None), never an optimistic guess.
"""
from __future__ import annotations

import datetime
import json
import logging
import math
import os
from pathlib import Path

from utah.daemon import runtime

log = logging.getLogger("utah.mail_capacity")

#: When each sending inbox first sent — the clock the warmup ramp counts from.
WARMUP_STATE = runtime.UTAH_HOME / "run" / "mail_warmup.json"

# ── provider-safe STEADY caps (cold, warmed, own-domain w/ SPF+DKIM). Env-overridable. ──
#: Free Gmail: hard ~500/day total, but cold-safe far lower before complaints bite.
GMAIL_STEADY = int(os.environ.get("UTAH_MAIL_GMAIL_STEADY", "50"))
#: Own domain on a real mailbox host (privateemail/workspace) with SPF+DKIM: much higher.
DOMAIN_STEADY = int(os.environ.get("UTAH_MAIL_DOMAIN_STEADY", "200"))

# ── warmup ramp: a brand-new inbox must not jump to its steady cap on day one. ──
#: Day-0 cap for a never-used inbox.
WARMUP_START = int(os.environ.get("UTAH_MAIL_WARMUP_START", "20"))
#: Added to the allowed cap per elapsed day until the provider steady cap is reached.
WARMUP_STEP = int(os.environ.get("UTAH_MAIL_WARMUP_STEP", "20"))

# ── auto-pause: a sender bleeding bounces is poisoning the domain — pull it. ──
#: Bounce rate (0..1) above which an account is auto-paused (needs >= MIN_SENDS first).
PAUSE_BOUNCE_RATE = float(os.environ.get("UTAH_MAIL_PAUSE_BOUNCE_RATE", "0.10"))
#: Don't judge an account's rate until it has at least this many sends (small-N noise).
PAUSE_MIN_SENDS = int(os.environ.get("UTAH_MAIL_PAUSE_MIN_SENDS", "20"))

#: The sending domains we own (drives DMARC checks + provider classification).
OWNED_DOMAINS = frozenset(
    d.strip().lower() for d in
    os.environ.get("UTAH_MAIL_OWNED_DOMAINS", "blacklabelbots.com").split(",") if d.strip()
)


def _today() -> datetime.date:
    # Date.now() is unavailable in some sandboxes; the live path uses the real clock,
    # tests pass `today=` explicitly so this is never reached under test.
    return datetime.date.today()


def _domain_of(addr: str) -> str:
    return (addr or "").strip().lower().split("@", 1)[-1]


def is_own_domain(account: dict) -> bool:
    """True for a mailbox on a domain we own (own-domain + SPF/DKIM sustains more volume).
    A non-gmail SMTP host (privateemail, workspace) on an owned domain is the high-cap tier;
    a gmail.com SMTP host is the low-cap tier even when the From is a custom domain alias."""
    host = (account.get("smtp_host") or "").strip().lower()
    if host == "smtp.gmail.com":
        return False
    return _domain_of(account.get("from", "")) in OWNED_DOMAINS or host.startswith("smtp.privateemail")


def provider_steady_cap(account: dict) -> int:
    return DOMAIN_STEADY if is_own_domain(account) else GMAIL_STEADY


def safe_daily_cap(account: dict, age_days: int) -> int:
    """The deliverability-safe number of sends allowed for *account* today.

    Ramps from :data:`WARMUP_START` up by :data:`WARMUP_STEP`/day until the account's
    provider steady cap. ``age_days`` is days since the inbox's first send (0 = brand new).
    A negative age (clock skew) is clamped to 0 so a new inbox is never handed full volume."""
    steady = provider_steady_cap(account)
    warm = WARMUP_START + max(0, age_days) * WARMUP_STEP
    return max(0, min(steady, warm))


# ── warmup first-seen tracking ────────────────────────────────────────────────────────

def first_seen_map() -> dict[str, str]:
    """``{from_addr: 'YYYY-MM-DD'}`` — when each inbox first sent. Missing/corrupt → {}."""
    try:
        data = json.loads(WARMUP_STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def record_seen(accounts: list[dict], *, today: datetime.date | None = None,
                state: dict | None = None) -> dict[str, str]:
    """Stamp first-seen=*today* for any account not already recorded; return the updated map.
    Idempotent — an already-seen account keeps its original (earlier) date so warmup age only
    grows. Pure when *state* is supplied (tests); else reads/writes :data:`WARMUP_STATE`."""
    today = today or _today()
    cur = dict(state if state is not None else first_seen_map())
    changed = False
    for a in accounts:
        addr = (a.get("from") or "").strip().lower()
        if addr and addr not in cur:
            cur[addr] = today.isoformat()
            changed = True
    if changed and state is None:
        _save_warmup(cur)
    return cur


def _save_warmup(state: dict) -> None:
    try:
        WARMUP_STATE.parent.mkdir(parents=True, exist_ok=True)
        WARMUP_STATE.write_text(json.dumps(state))
    except OSError as exc:  # a lost stamp just restarts that inbox's warmup — safe-conservative
        log.warning("mail_capacity: could not persist warmup state (%s)", exc)


def age_days_for(account: dict, first_seen: dict[str, str], *,
                 today: datetime.date | None = None) -> int:
    """Warmup age (days since first send) for *account*. Unknown → 0 (treat as brand new)."""
    today = today or _today()
    addr = (account.get("from") or "").strip().lower()
    raw = first_seen.get(addr)
    if not raw:
        return 0
    try:
        return max(0, (today - datetime.date.fromisoformat(raw)).days)
    except (TypeError, ValueError):
        return 0


# ── capacity math ─────────────────────────────────────────────────────────────────────

def capacity(accounts: list[dict], *, first_seen: dict[str, str] | None = None,
             paused: set[str] | None = None, today: datetime.date | None = None) -> dict:
    """Today's TOTAL deliverability-safe send capacity across the pool, with the per-account
    breakdown. Paused accounts contribute 0. Pure given *first_seen*/*paused*/*today*."""
    today = today or _today()
    first_seen = first_seen if first_seen is not None else first_seen_map()
    paused = paused if paused is not None else set()
    per = []
    total = 0
    for a in accounts:
        addr = (a.get("from") or "").strip().lower()
        if addr in paused:
            per.append({"from": addr, "cap": 0, "paused": True,
                        "tier": "domain" if is_own_domain(a) else "gmail"})
            continue
        age = age_days_for(a, first_seen, today=today)
        cap = safe_daily_cap(a, age)
        per.append({"from": addr, "cap": cap, "age_days": age, "paused": False,
                    "tier": "domain" if is_own_domain(a) else "gmail",
                    "steady": provider_steady_cap(a)})
        total += cap
    return {"total": total, "accounts": len(accounts),
            "active": sum(1 for p in per if not p["paused"]), "per_account": per}


def accounts_needed_for(target: int, *, tier: str = "domain") -> int:
    """How many fully-warmed mailboxes of *tier* ('domain'|'gmail') a *target*/day needs."""
    steady = DOMAIN_STEADY if tier == "domain" else GMAIL_STEADY
    if steady <= 0:
        return 0
    return math.ceil(max(0, target) / steady)


# ── deliverability + per-account health from the ledgers ───────────────────────────────

def deliverability(conn) -> dict:
    """Real, ledger-grounded deliverability. ``rate`` = sent / (sent + bounced) over all
    recorded mail. ``rate`` is None (not a fake 100%) when nothing has been sent yet."""
    sent = conn.execute("SELECT count(*) FROM mail_ledger WHERE status='sent'").fetchone()[0]
    bounced = conn.execute("SELECT count(*) FROM mail_ledger WHERE status='bounced'").fetchone()[0]
    total = sent + bounced
    rate = (sent / total) if total else None
    return {"sent": sent, "bounced": bounced, "total": total,
            "rate": rate, "rate_pct": (round(100 * rate, 1) if rate is not None else None)}


def account_health(conn) -> list[dict]:
    """Per-sender bounce rate from mail_ledger's ``sender`` column (when present). Returns []
    when the column doesn't exist yet (older schema) — health is then simply not enforced,
    never guessed. Each row: {sender, sent, bounced, rate, paused}."""
    try:
        rows = conn.execute(
            "SELECT sender, "
            "  count(*) FILTER (WHERE status='sent')    AS sent, "
            "  count(*) FILTER (WHERE status='bounced') AS bounced "
            "FROM mail_ledger WHERE sender IS NOT NULL GROUP BY sender"
        ).fetchall()
    except Exception:  # noqa: BLE001 — no sender column / DB hiccup: health unenforced, honest
        return []
    out = []
    for sender, sent, bounced in rows:
        total = (sent or 0) + (bounced or 0)
        rate = (bounced / total) if total else 0.0
        paused = total >= PAUSE_MIN_SENDS and rate > PAUSE_BOUNCE_RATE
        out.append({"sender": sender, "sent": sent or 0, "bounced": bounced or 0,
                    "rate": round(rate, 3), "paused": paused})
    return out


def paused_accounts(conn) -> set[str]:
    """Senders to pull from rotation NOW: bounce rate over threshold past the min-sends floor.
    Empty when health can't be measured (no sender column) — fail-open, never silently stalls
    all sending on a measurement gap."""
    return {h["sender"] for h in account_health(conn) if h["paused"]}


# ── DMARC (the one missing domain-auth record for bulk inbox placement) ─────────────────

def _dig_txt(name: str) -> list[str]:  # pragma: no cover — real DNS
    import subprocess
    try:
        out = subprocess.run(["dig", "+short", "TXT", name], capture_output=True,
                             text=True, timeout=8).stdout
        return [ln.strip().strip('"') for ln in out.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return []


def dmarc_present(domain: str, *, txt_lookup=None) -> bool:
    """True iff a DMARC policy is published at ``_dmarc.<domain>``. Gmail/Yahoo require it
    for bulk senders; without it own-domain mail loses inbox placement at volume."""
    lookup = txt_lookup or _dig_txt
    return any(t.lower().startswith("v=dmarc1") for t in lookup(f"_dmarc.{domain}"))


def recommended_dmarc(domain: str) -> str:
    """The exact TXT record to publish at ``_dmarc.<domain>`` — start in monitor mode."""
    return (f"_dmarc.{domain}  TXT  "
            f"\"v=DMARC1; p=none; rua=mailto:dmarc@{domain}; fo=1; adkim=s; aspf=s\"")


# ── the honest plan ────────────────────────────────────────────────────────────────────

def plan(accounts: list[dict], conn=None, *, target: int = 3000,
         first_seen: dict[str, str] | None = None, paused: set[str] | None = None,
         today: datetime.date | None = None, dmarc_lookup=None) -> dict:
    """Where we stand vs *target*/day at deliverability-safe caps, and exactly what closes
    the gap. Honest: states current capacity, current deliverability, the mailbox shortfall,
    and the DMARC status — never claims a target is met when the senders don't exist."""
    cap = capacity(accounts, first_seen=first_seen, paused=paused, today=today)
    deliv = deliverability(conn) if conn is not None else None
    domain_steady = DOMAIN_STEADY
    need_domain_boxes = accounts_needed_for(target, tier="domain")
    shortfall = max(0, target - cap["total"])
    extra_boxes = math.ceil(shortfall / domain_steady) if domain_steady else 0
    dmarc = {d: dmarc_present(d, txt_lookup=dmarc_lookup) for d in sorted(OWNED_DOMAINS)}
    steps = []
    if shortfall > 0:
        steps.append(
            f"Add ~{extra_boxes} more warmed mailbox(es) on an owned domain "
            f"(each safely sustains ~{domain_steady}/day warmed) to cover the "
            f"{shortfall}/day shortfall.")
    for d, ok in dmarc.items():
        if not ok:
            steps.append(f"Publish DMARC for {d}:  {recommended_dmarc(d)}")
    if deliv and deliv["rate"] is not None and deliv["rate"] < 0.70:
        steps.append(f"Deliverability {deliv['rate_pct']}% is under 70% — auto-pause is "
                     "pulling bad senders; widen list hygiene (MX gate) before adding volume.")
    return {
        "target_per_day": target,
        "safe_capacity_today": cap["total"],
        "accounts_configured": cap["accounts"],
        "accounts_active": cap["active"],
        "fully_warmed_domain_boxes_needed_for_target": need_domain_boxes,
        "mailbox_shortfall_for_target": extra_boxes,
        "deliverability": deliv,
        "deliverability_ok": (deliv is None or deliv["rate"] is None or deliv["rate"] >= 0.70),
        "dmarc": dmarc,
        "capacity_detail": cap,
        "meets_target": cap["total"] >= target,
        "steps": steps,
    }


__all__ = [
    "safe_daily_cap", "provider_steady_cap", "is_own_domain",
    "first_seen_map", "record_seen", "age_days_for",
    "capacity", "accounts_needed_for",
    "deliverability", "account_health", "paused_accounts",
    "dmarc_present", "recommended_dmarc", "plan",
    "GMAIL_STEADY", "DOMAIN_STEADY", "WARMUP_START", "WARMUP_STEP",
    "PAUSE_BOUNCE_RATE", "PAUSE_MIN_SENDS", "OWNED_DOMAINS", "WARMUP_STATE",
]

# Email deliverability → 3,000/day at >70%

Goal: send **3,000+ cold emails/day** while keeping deliverability **above 70%**.
Status as of 2026-06-14. All numbers below are measured/computed, not estimated.

## Where we stand

| Metric | Value | Source |
|---|---|---|
| Detected deliverability | **95.2%** (256 sent / 13 hard-bounced) | `mail_ledger` |
| Suppression leaks (re-send after bounce) | **0** | `mail_ledger` |
| Distinct authenticated senders | **3** (`info@`, `delivery@`, `mtuburnsbarber@gmail.com`) | SMTP login probe |
| Safe capacity today (3 senders, fully warmed) | **~450/day** | `mail_capacity.capacity()` |
| Current daily volume | ~55/day (demand-limited, not cap-limited) | `mail_ledger` |

**Deliverability is already over 70%.** The blocker for 3,000/day is **capacity**, and
capacity is gated by the number of *warmed* sending mailboxes — not by code.

## Why you can't just "raise the cap"

Past ~30–50 cold sends/day a single mailbox's spam-complaint rate crosses the provider
threshold (Gmail 0.3%), reputation dies, and the **domain** gets throttled/suspended —
deliverability collapses. Volume at high deliverability comes ONLY from more warmed
senders, each kept under a provider-safe daily cap. That is what this system now enforces.

## What was built (code, in-house, $0)

- **Warmup-aware, provider-aware caps** (`utah/mail_capacity.py`, wired into `utah/mail.py`):
  a new inbox ramps from 20/day up by 20/day to its provider steady cap — own-domain
  mailbox **~200/day**, free Gmail **~50/day**. `PER_ACCOUNT_DAILY=-1` (default) uses this;
  `0` = uncapped, `>0` = flat (overrides for testing).
- **Distinct-sender fix**: `delivery@blacklabelbots.com` was being silently collapsed into
  `info@` (legacy-alias canonicalization), so we had 2 real senders, not 3, and that inbox's
  login was broken. Fixed — all 3 now authenticate and rotate. Reply-To stays canonical `info@`.
- **Per-account health + auto-pause** (`mail_capacity.paused_accounts`): bounce rate per
  sending inbox (new `mail_ledger.sender` column); any inbox over 10% bounces (≥20 sends) is
  pulled from rotation before it poisons the domain. *Holds* the >70% at scale.
- **Honest capacity planner** (`mail_capacity.plan`): real capacity vs target, the mailbox
  shortfall, deliverability, and DMARC status — never claims a target is met without senders.

Already in place and verified: MX/syntax/role pre-send gate (`enrich.verify_email`),
never-twice suppression, IMAP bounce ingestion → `mark_bounced` (`mail_replies.py`),
reply→phone-page, SPF + DKIM on `blacklabelbots.com`.

## To actually reach 3,000/day at >70% — two owner actions

1. **Add ~14 more mailboxes on an owned domain** (free on Namecheap Private Email, e.g.
   `hello@`, `sales@`, `team@`, `hi@`, … or a second domain). Each warmed inbox safely
   sustains ~200/day → 15 total × 200 = 3,000/day. Add them to
   `~/.utah/secrets/email_accounts.json` (same shape as the 3 there now). The pool, caps,
   warmup, rotation, and health auto-scale to however many are present — no code change.
   New inboxes ramp over ~9 days (20 → 200/day); plan the warmup runway accordingly.

2. **Publish DMARC for blacklabelbots.com** (Gmail/Yahoo require it for bulk; SPF+DKIM are
   already set). Add this DNS TXT record:

   ```
   _dmarc.blacklabelbots.com  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@blacklabelbots.com; fo=1; adkim=s; aspf=s"
   ```

   `p=none` is monitor-only (safe); tighten to `quarantine` later once reports look clean.

## Check status any time

```bash
python -c "from utah import mail, mail_capacity as mc; from utah.db_pool import get_pool; from utah import config
import json
with get_pool(config.DB_DSN).connection() as c:
    print(json.dumps(mc.plan(mail.accounts(), conn=c, target=3000), indent=2, default=str))"
```

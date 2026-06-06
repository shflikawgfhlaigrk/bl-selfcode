# 12 — PRODUCT / REVENUE audit (the money)

> The end-state Utah must deliver. Dirt (live data + ~/.ace/bin senders) + the
> BOX acceptance gates. Capabilities, not agents.

## 1. What we had
- **Leads:** a real moving frontier — **500 no-website SMB/day** + 551 law-firm
  leads with emails; auto-sender built (suppression + sent-ledger + unsubscribe +
  ramp). **One real email proven.**
- **Probate:** real GA statewide notices (40/run), source-traceable.
- **Marketing:** local reel renderer makes real MP4s daily; **Veo dead (429)**.
- **Inbox:** 135,170 unread; concierge routes voice, no real triage.
- **Trading:** 8 engines, WC feed bridge, regime guards — **$0 real, ~49% win,
  no proven edge, 1 manual paper trade.**

## 2. Why we did it
The whole point of Ace is money. Leads/probate were built to feed a pipeline;
reels for marketing; trading as the big bet. Each pipeline was engineered and (for
leads/probate) producing **real data**.

## 3. What we didn't think about
- **"Build it, gate it, forget to open it":** the lead machine is **$0** — blocked
  on a `physical_address:"Placeholder"`, no email for no-website SMBs, no sending
  domain. Reels render into a folder **nothing posts from**. Veo's dead + its
  **fake-URL fallback passed the quality gate**.
- **Inbox neglected** (135k unread, no triage).
- **Trading had no skin in the game** → no edge could ever be proven; sophistication
  around a loop that can't validate itself.

## 4. What we're gonna change (each with its done-right gate)
- **Leads (BOX 7):** ledger → **Postgres** (UNIQUE = never-twice), **email
  enrichment/skip-trace** for no-website SMBs, **Resend + a verified sending
  domain (SPF/DKIM)**, real CAN-SPAM address. *Gate: a real compliant email to a
  real prospect, logged, with working unsubscribe, shown on the dashboard.*
- **Probate (BOX 4):** statewide scrape + **skip-trace heir contact** + comps/ARV.
  *Gate: 10 profitable, source-traceable candidates/day.*
- **Marketing (BOX 11):** kill Veo + the fake-URL fallback; **local LTX-2 MLX**
  video + **build the missing posting pipeline** (TikTok/IG) + approval. *Gate:
  approved reel → posted, URL captured, analytics back in 24 h.*
- **Inbox:** triage via **Apple Foundation Models / Claude** (VIP, urgent, action);
  surfaced + spoken. *Gate: "any urgent mail" returns real classified results.*
- **Trading:** **decoupled, research-only** (BROKER=null, AUTO_EXECUTE=false); WC
  feed → **WIN**; per-fill alert <5 s + 17:00 brief; **skin-in-the-game first** —
  one engine, one real (then $1) trade before any second. *Gate: real engine.fired
  only (no synthetic), honest scorecard.*

## 5. How it helps
- The money **actually flows** — the gates that left Ace at $0 are opened
  (address + domain + enrichment), routed through a transactional ledger.
- Real, posted, genuinely-good video (local LTX-2) instead of a folder of unposted
  clips; inbox handled; trading honest (no $0-no-edge theater).
- Baseline = these capabilities live + gated; then autonomy (audit 13) compounds.

Sources: local LTX-2 MLX video https://github.com/dgrauet/ltx-2-mlx · Resend deliverability (SPF/DKIM) — standard; BOX gates from AGI-BUILD-ROADMAP.

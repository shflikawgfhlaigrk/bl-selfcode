# 23 — Discord (the server that mirrors the deck)

The hand-built Discord was painful: click 9 categories, 34 channels, 6 roles, topics
and permissions into existence by hand, then get the bot token / intents / library
wrong. **Bypass:** the entire server is declared once as data (`utah/integrations/
discord.py :: BLUEPRINT`) and applied **idempotently** over the raw Discord REST API
(`requests`, no `discord.py` to install/break). Run it on an empty guild → it builds
everything. Run it again → it only fills gaps and patches topics/permissions.

## What it builds (mirrors the Black Gold deck domains)

```
Roles:   Sovereign · Operator · Ace · Investor · Member · Muted
🏛️ WELCOME      welcome · announcements · start-here · roadmap · how-to-read-it
🧠 THE BRAIN    ask-ace · reasoning · memory · knowledge
💰 REVENUE      leads · probate · outreach · ledger
📊 TRADING      fires · engine-roster · wc-feed · trading-floor
🦴 SPINE        heartbeat · event-bus · system-status · audit-ledger
🛠️ SELF-CODE    proposals · merges · utility-scores
🎙️ VOICE        voice-log · Voice Lounge (voice)
🌐 COMMUNITY    general · introductions · showcase · support · ideas
🔒 OPERATIONS   control-plane · logs · alerts        (operator-only)
```

Read-only channels (`announcements`, all feeds) let `@everyone` view but not send;
`Ace`/`Operator` post. Operator channels hide from `@everyone` entirely. Every feed
channel gets a `Utah Feed` webhook so the spine pushes live data in (no bot process
needed).

## One-pass setup

1. **The one human-gated step** (Discord requires your login to mint a bot identity):
   https://discord.com/developers/applications → New Application → Bot → Reset Token.
   Under **Installation / OAuth2** give it the `bot` scope with **Administrator**, and
   invite it to your server (or none — it can create one).
2. Drop creds at `~/.utah/secrets/discord.json` (see `discord.json.example`):
   ```json
   { "bot_token": "...", "guild_id": "...(optional)", "invite_url": "https://discord.gg/..." }
   ```
3. Build it:
   ```bash
   utah discord --plan      # dry-run, no token needed — see exactly what lands
   utah discord             # apply idempotently (re-runnable; only fills gaps)
   utah discord --create    # ...or create a brand-new guild first (bot in <10 guilds)
   ```
   Webhook URLs for every feed channel are saved to
   `~/.utah/secrets/discord_webhooks.json`.

## Integrated into the website

- **JOIN DISCORD** button in the deck header (`live.html`) lights once `invite_url`
  is set; it links through the stable `/discord` redirect.
- `GET /discord` → 302 to the live invite (404 honestly when none is set — never faked).
- `GET /api/discord` → `{invite, available, totals}` (feeds the header button).
- Invite source order: `config.DISCORD_INVITE_URL` (env `UTAH_DISCORD_INVITE`) →
  `~/.utah/secrets/discord.json :: invite_url`.

## Live feed bridge (`utah/integrations/discord_feed.py`)

The deck's live domains flow into the matching channels via the saved webhooks, so the
server is a live mirror, not a shell:

| feed key | channel        | producer                         |
|----------|----------------|----------------------------------|
| leads    | 📈leads        | `discord_feed.feed_lead(row)`    |
| probate  | ⚖️probate      | `discord_feed.feed_probate(row)` |
| outreach | 📨outreach     | `discord_feed.publish('outreach')` |
| fires    | 🔥fires        | `discord_feed.feed_fire(fire)`   |
| audit    | 🛡️audit-ledger | `discord_feed.feed_audit(s,k,d)` |
| selfcode | ✅merges       | `discord_feed.feed_merge(...)`   |
| announce | 📣announcements| `discord_feed.announce(text)`    |
| critical | 🚨alerts       | `discord_feed.alert(text)`       |

Honest gate throughout: no token → `provision` returns `{gated:true}`; no webhook for a
domain → the feed helper returns `False`. Nothing is ever faked; every failure is
recorded to the audit ledger. Fully unit-proven with zero network (`tests/test_discord.py`).

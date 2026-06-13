# Utah — Access & API request (everything the system needs to ungate)

> Generated 2026-06-07. The system is built so that **dropping a real creds file into
> `~/.utah/secrets/` ungates the matching capability instantly** (`creds_available()` just
> checks the file exists). Templates already live there as `*.json.example` — fill one in,
> save it without the `.example`, and that capability goes live. Some also need ME to wire
> the live adapter (flagged ⚙️ below) — credential alone isn't enough there.
>
> ⚠️ Don't create empty `.json` files: the gate only checks existence, so an empty file
> ungates then fails at send. Put real values in, or leave it as `.example`.

---

## TIER 1 — turnkey (your credential alone activates it; adapter already wired)

### 1. Gmail  → `~/.utah/secrets/gmail.json`   ★ highest value
Unlocks THREE at once: **morning-brief email**, **lead outreach email**, general **mail send**.
- Use **info@blacklabelbots.com** as the outbound From/Reply-To (matches the storefront).
- Get a Google **App Password** (not your normal password): Google Account → Security →
  2-Step Verification (must be on) → **App passwords** → generate → copy the 16 chars.
  For Workspace: either create a dedicated `info@` user, or add **Send mail as** for
  `info@blacklabelbots.com` on the account that holds the app password.
- File:
  ```json
  { "from": "info@blacklabelbots.com", "app_password": "abcd efgh ijkl mnop", "smtp_host": "smtp.gmail.com" }
  ```
- Override default From without editing the file: `UTAH_BLB_FROM_EMAIL=info@blacklabelbots.com`
- Wiring: DONE (SMTP-over-SSL adapter is live). Works the instant the file lands.

### 2. macOS automation  → `~/.utah/secrets/macos.json`  (flag already present ✅)
Unlocks: **Notes**, **Contacts**, **clipboard/Shortcuts**, **desktop notifications**.
- The flag file is already there. The only thing left is the OS-level grant: when Utah first
  drives Notes/Contacts/System Events you'll get a macOS "Allow Automation" prompt — click
  Allow. (Or pre-grant in System Settings → Privacy & Security → Automation, and Contacts.)
- Wiring: DONE (osascript adapters live).

### 3. Browser (JS-rendered fetch)  → already works ✅
Auto-activates because Chrome is installed (headless `--dump-dom`). No credential needed.
Only touch `chrome.json` if you want a specific Chrome binary path.

### 4. Pushover (phone alerts)  → `~/.utah/secrets/pushover.json`   ✅ live
Unlocks: **morning brief push**, **trade-fire pages**, **critical breakage alerts**,
**pipeline summaries** (leads/probate/outreach).
- Create a Pushover app at https://pushover.net/apps/build → copy the **API token**.
- Your **user key** is on the Pushover dashboard (or use a **group key** for all devices).
- File (either key shape works — Utah accepts both):
  ```json
  { "api_token": "...", "user_key": "...", "group_key": "...", "default_target": "user" }
  ```
  Legacy Ace/plan shape `{ "token", "user" }` also works.
- Wiring: DONE (`utah/integrations/pushover.py` + `utah/alerts.py`). Real send proven live.

### 5. Apple Developer (UTAH iPhone app)  → membership ✅ + one-time Xcode setup
Unlocks: **native UTAH command deck on iPhone** (`app/` Flutter build), TestFlight, App Store.
- **Membership:** enrolled ✅ (was the blocker).
- **Bundle ID:** register `com.utah.utahApp` at developer.apple.com → Identifiers.
- **Team ID:** copy into `app/ios/Signing.xcconfig` (from `Signing.xcconfig.example`).
- **Xcode:** sign in with your Apple ID → open `app/ios/Runner.xcworkspace` once → Automatic
  signing creates the provisioning profile.
- **Tailscale:** iPhone must be on the tailnet; `com.utah.tailserve` already proxies
  `:8765` → Utah `:8766`.
- Wiring: DONE (`app/lib/` polls live `/status`, `/memory`, `/panel/*`, streamed `/api/tell`).
  Full steps: `app/README.md`.

---

## TIER 2 — your credential + I wire the live adapter (⚙️ = code still a stub)

### 6. WealthCharts feed (TRADING)  → live login + ⚙️ bridge   ★ revenue
Unlocks: the 9-engine lab fires for real (currently 0, honest-gated).
- This is NOT a file — it's a **live login**: log into WealthCharts in its Chrome profile so
  the chart/tick data is on screen.
- ⚙️ I still need to wire the CDP bridge: `trading.feed_available()` hard-returns False and
  `_live_feed()` is a stub. I'll port the old-Ace `wc_cdp_bridge` (Chrome :9222 → ticks →
  `evaluate()` → `record_fire`). **So: your login + ~half a day of my wiring + live proof.**

### 6. Instagram  → `~/.utah/secrets/instagram.json` + ⚙️ Graph API
Unlocks: **auto-posting** (marketer).
- Needs a Meta/Facebook Developer app + an **IG Business/Creator account** linked to a FB
  Page, then a long-lived access token + your IG user id:
  ```json
  { "access_token": "EAAB...long-lived...", "ig_user_id": "1784xxxxxxxxxxx" }
  ```
- ⚙️ `_real_publish()` is a stub — I wire the Graph API `media` + `media_publish` calls.

### 7. TikTok  → `~/.utah/secrets/tiktok.json` + ⚙️ Content Posting API
- TikTok for Developers app (Content Posting API; approval required) → access token:
  `{ "access_token": "act...." }`
- ⚙️ I wire the upload/publish call.

### 8. Google Calendar  → `~/.utah/secrets/google.json` + ⚙️ Google client
Unlocks: **calendar event creation**.
- Google Cloud Console → OAuth 2.0 client (Desktop) → consent once → get client_id,
  client_secret, refresh_token:
  ```json
  { "client_id": "...apps.googleusercontent.com", "client_secret": "...", "refresh_token": "1//..." }
  ```
- ⚙️ Calendar API call path needs finishing + the google client lib in `~/.utah/venv`.
- NOTE: there's already a connected **claude.ai Google Calendar** MCP — for most calendar
  needs I can use that today without this OAuth app. This file is only for the *daemon's own*
  calendar capability.

### 9. External data API (weather/finance quotes)  → `~/.utah/secrets/external.json` + ⚙️
- A data API key: `{ "api_key": "..." }` (your choice of provider).
- ⚙️ fetcher is a stub. LOW priority: free weather already works ungated via Open-Meteo
  (`utah/product/weather.py`). This is only for paid finance quotes / a richer source.

---

## TIER 2.5 — revenue last-mile unlocks (added 2026-06-09; code built, gated on input)

### 10. SMS / texting  → `~/.utah/secrets/twilio.json` OR the iMessage grant   ★ revenue
Cold-text the ~790 phone-only leads (CAN-SPAM-compliant pitch with opt-out).
- **Already works via iMessage** when no Twilio: `sms.send` falls back to Messages.app.
  The ONE unlock there is the OS grant — System Settings → Privacy & Security → Automation
  → allow the Utah Python to control **Messages** (one click). Then phone leads are reached.
- For a dedicated number instead: `{ "account_sid": "AC...", "auth_token": "...", "from_number": "+1..." }`.
  Wiring: DONE (Twilio adapter + iMessage relay both live).

### 11. Email skip-trace  → `~/.utah/secrets/skiptrace.json`   (grows the emailable pool)
The free open-web enricher already runs (finds public emails for some leads). A paid
skip-trace raises the yield. Drop the provider creds and the provider path activates
(`enrich._provider_find` — wire the real call when you pick a vendor). Without it, the free
web pass still runs every cycle. No vendor chosen yet → free path only.

### 12. Probate direct-mail  → `~/.utah/secrets/lob.json`   (auto-send heir letters)
The probate last-mile generates ready-to-mail letters to `~/.utah/run/probate_letters/`
today (Michael prints + mails). Drop a print-mail provider's creds (e.g. Lob) and the
letters auto-send; suppression then commits on real send. Without it, letters are queued,
never faked as sent.

## HUMAN-ONLY blockers (no credential can unlock these — your call/action)
These are the §1.4 blockers that are NOT code: the engine is built and waiting.
- **The volume-send GO** — email is live + business-hours-capped at 50/hr (≤500/day). It
  sends automatically on the `com.utah.outreach` schedule; the only "decision" is leaving it
  armed (it is). Watch the first batch, then let it run.
- **Close one sale** — the pipeline delivers (james-bros is a real built site). One reply
  worked → one close moves the system off $0 (the `outcome_gate` flips green).
- **WealthCharts login** — log into WC once in the migrated `~/.utah/chrome-wc` profile; the
  CDP bridge reads the feed (engines stay paper until you fund a broker — by design).
- **Fund a broker** — only when a backtest proves edge (`product/backtest.prove_edge`). Until
  then trading stays paper, one engine. Don't fund on hope.

## Already covered — no action needed
- **Brain / reasoning** = Claude CLI (`claude -p`), already logged in. CLI-only by design —
  do NOT add an Anthropic API key (credits depleted; CLI subscription is the free path).
- **Embeddings / rerank** = on-device (fastembed BGE + ms-marco), free, working.
- **Voice** = local Whisper/Silero/Piper, free, working.
- **Postgres + pgvector** = local, working.
- **Leads source** = OpenStreetMap (Overpass), free, no key — 962 leads already.

---

## Priority order (my recommendation)
1. **Apple Developer → iPhone app** — register `com.utah.utahApp`, copy Team ID to
   `app/ios/Signing.xcconfig`, `flutter run` on your phone (see `app/README.md`).
2. **Gmail app password** — 2 minutes, unlocks 3 capabilities incl. lead outreach.
3. **macOS Automation grant** — click Allow when prompted; unlocks notes/contacts/notify.
4. **WealthCharts login** — then tell me, I wire the bridge + prove fires live.
5. **Instagram token** (if you want social autopost) — I wire the Graph API.
6. TikTok / Calendar OAuth / external-data key — as needed.

Drop the file(s), tell me which you did, and I'll prove each one live (real send / real event /
real fire) — not just "file present."

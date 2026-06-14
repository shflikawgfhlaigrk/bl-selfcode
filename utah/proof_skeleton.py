"""The dormant skeleton + meta-proofs + human accountability rows.

Every pipeline/product/claim is registered RED (proof_cmd=None) until proven one at a
time. Meta-proofs make the proof system prove itself. Human rows (owner='michael',
system='you') hold Michael to the same standard — proven by the artifact his action
produces, never his word. Re-running any seeder never erases an earned proof.
"""
from __future__ import annotations

from utah import proof
from utah.proof import ProofSpec as P

_SKELETON = [
    # --- Growth / Sales ---
    P(id="pipeline.leads.fresh_daily", claim="leads pipeline adds fresh rows daily",
      system="growth", artifact="utah/product/leads.py:run_scheduled", freshness_sla="26 hours",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM leads WHERE ts > now() - interval '26 hours'"),
    P(id="pipeline.enrich.adds_contacts", claim="enrich finds emails/phones for leads",
      system="growth", artifact="utah/product/enrich.py",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM leads WHERE contact ? 'email' OR contact ? 'phone'"),
    P(id="pipeline.outreach.sends_gated", claim="outreach sends only to verified, deliverable addresses",
      system="growth", artifact="utah/product/outreach.py",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM outreach_ledger"),
    P(id="pipeline.marketer.posts", claim="marketer produces posts",
      system="growth", artifact="utah/product/marketer.py",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM marketer_posts"),
    P(id="pipeline.replies.reads_imap", claim="replies reader ingests inbound mail",
      system="growth", artifact="utah/product/ledger.py", freshness_sla="2 hours",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM mail_replies"),
    P(id="pipeline.mailcheck.polls", claim="mailcheck polls inbound mail", system="growth",
      artifact="utah/mail.py", freshness_sla="3 hours",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM mail_ledger"),
    P(id="pipeline.leads_maps.harvest", claim="maps harvest adds SMB leads", system="growth",
      artifact="utah/product/leads.py",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM leads WHERE source = 'google_maps'"),
    # --- Real Estate ---
    P(id="pipeline.probate.scrapes", claim="probate scraper adds records", system="realestate",
      artifact="utah/product/probate.py", freshness_sla="26 hours",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM probate"),
    P(id="pipeline.probate_enrich.values", claim="probate enrich adds ARV + heir contacts",
      system="realestate", artifact="utah/product/property.py",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM probate WHERE arv > 0 OR heir_contact IS NOT NULL"),
    P(id="pipeline.probate_outreach.mails", claim="probate outreach mails letters", system="realestate",
      artifact="utah/product/probate_outreach.py",
      proof_kind="shell", proof_cmd="test -d $HOME/.utah/run/probate_letters && test -n \"$(ls -A $HOME/.utah/run/probate_letters 2>/dev/null)\""),
    # --- Trading ---
    P(id="pipeline.wcfeed.live_ticks", claim="market feed ticks during open session", system="trading",
      artifact="utah/canary.py:check_ticks", freshness_sla="5 minutes",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM wc_live"),
    P(id="pipeline.signals.generates", claim="signals are generated", system="trading",
      artifact="utah/product/signals.py", freshness_sla="1 hour"),
    P(id="pipeline.grade_fires.honest", claim="fires are graded honestly", system="trading",
      artifact="utah/product/fire_grader.py"),
    P(id="pipeline.engine_audit.proves_edge", claim="engine audit recomputes OOS edge nightly",
      system="trading", artifact="utah/product/engine_audit.py", freshness_sla="26 hours"),
    # --- Engineering / self ---
    P(id="pipeline.selfcode.merges_real", claim="selfcode merges real, tested commits", system="eng",
      artifact="utah/sica.py",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM selfcode_log"),
    P(id="pipeline.selfaudit.runs", claim="selfaudit runs", system="eng", artifact="utah/sica.py",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/run/discoveries.jsonl && test -n \"$(cat $HOME/.utah/run/discoveries.jsonl 2>/dev/null)\""),
    P(id="pipeline.codeindex.indexes", claim="codeindex ingests the codebase", system="eng",
      artifact="utah/codebase.py",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM memory WHERE source='code'"),
    P(id="pipeline.consolidate.memory", claim="memory consolidation runs", system="eng",
      artifact="utah/memory/store.py", freshness_sla="1 hour",
      proof_kind="sql", proof_cmd="SELECT count(*) > 0 FROM memory WHERE source='consolidation'"),
    # --- On-device AI (Apple Foundation Models / Apple Silicon) ---
    P(id="pipeline.local_fm.runs", claim="on-device LLM (Apple Foundation Models) answers — free, low-RAM local tier",
      system="eng", artifact="utah/fm_local.py + native/fm_cli", freshness_sla="24 hours",
      proof_kind="shell",
      proof_cmd="test -x \"$HOME/ProjectUtah/native/fm_cli\" && echo 'reply with: ok' | \"$HOME/ProjectUtah/native/fm_cli\" >/dev/null"),
    # --- Infra ---
    P(id="infra.supervisor.alive", claim="supervisor daemon is alive", system="infra",
      artifact="utah/daemon.py", freshness_sla="15 minutes",
      proof_kind="shell", proof_cmd="launchctl list com.utah.supervisor >/dev/null"),
    P(id="infra.deck.honest", claim="deck /state matches the DB (no fabrication)", system="infra",
      artifact="utah/interface/web.py", freshness_sla="15 minutes",
      proof_kind="http", proof_cmd="http://127.0.0.1:8766/state"),
    P(id="infra.postgres.reachable", claim="postgres :5433 reachable", system="infra",
      artifact="utah/db_pool.py", freshness_sla="15 minutes",
      proof_kind="shell", proof_cmd="/opt/homebrew/opt/postgresql@17/bin/pg_isready -h /tmp -p 5433 >/dev/null"),
    P(id="infra.canary.runs", claim="canary sweep runs", system="infra", artifact="utah/canary.py",
      freshness_sla="20 minutes",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/logs/canary.log && test -n \"$(cat $HOME/.utah/logs/canary.log 2>/dev/null)\""),
    P(id="infra.tailserve.serves", claim="deck served to phone via tailscale", system="infra",
      artifact="utah/canary.py",
      proof_kind="shell", proof_cmd="launchctl list com.utah.tailserve >/dev/null"),
    P(id="infra.operator.heals", claim="operator loop runs", system="infra", artifact="utah/canary.py",
      freshness_sla="10 minutes",
      proof_kind="shell", proof_cmd="launchctl list com.utah.operator >/dev/null"),
    P(id="infra.verify.gate", claim="verify gate runs", system="infra", artifact="ops/verify.py",
      proof_kind="shell", proof_cmd="launchctl list com.utah.verify >/dev/null"),
    # --- Comms ---
    P(id="comms.brief.sends", claim="morning brief sends", system="comms", artifact="utah/product/brief.py",
      freshness_sla="26 hours",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/logs/brief.log && test -n \"$(cat $HOME/.utah/logs/brief.log 2>/dev/null)\""),
    P(id="comms.discord_bot.heartbeat", claim="discord bot heartbeats", system="comms",
      artifact="utah/integrations/wc_feed.py", freshness_sla="1 hour",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/discord_webhooks.json"),
    # --- Products (sellable) ---
    P(id="product.website.up", claim="blacklabelbots.com serves real content", system="website",
      artifact="utah/product/sitegen.py", freshness_sla="1 hour"),
    P(id="product.sovereign.download", claim="Sovereign download path works (currently 404)",
      system="products", artifact="utah/product/sitegen.py", freshness_sla="1 hour"),
    P(id="product.stripe.catalog_clean", claim="Stripe catalog deduped to the real product line",
      system="revenue", artifact="ops/grade-queue.json"),
    # --- Social ---
    P(id="social.discord.live", claim="discord is wired and live", system="social",
      artifact="utah/integrations/wc_feed.py",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/discord.json"),
    P(id="social.instagram.wired", claim="instagram posting is wired", system="social",
      artifact="utah/product/marketer.py"),
    P(id="social.tiktok.wired", claim="tiktok posting is wired", system="social",
      artifact="utah/product/marketer.py"),
    P(id="social.linkedin.wired", claim="linkedin posting is wired", system="social",
      artifact="utah/product/marketer.py"),
]

# Meta-proofs: the proof system proves ITSELF by running its own test suite (pytest uses
# sys.executable = the venv, so utah is importable).
_META = [
    P(id="meta.runner.runs", claim="the proof runner runs and tallies", system="eng",
      artifact="utah/proof.py:run_scheduled", proof_kind="pytest",
      proof_cmd="tests/test_proof_scheduled.py"),
    P(id="meta.gate.blocks_unproven", claim="the gate hard-blocks an unproven id", system="eng",
      artifact="utah/proof.py:require_proven", proof_kind="pytest",
      proof_cmd="tests/test_proof_gate.py"),
    P(id="meta.tier.fail_closed", claim="stale + red-dependency read as not-green", system="eng",
      artifact="utah/proof.py:effective_tier", proof_kind="pytest",
      proof_cmd="tests/test_proof_tier.py"),
]

_HUMAN = [   # owner=michael, system="you" — RED until the artifact of HIS action exists.
             # Each carries how= (do this) and link= (go here). Still proven by a real check.
    P(id="you.mac.no_sleep", claim="Mac will not sleep (24/7 enabled)", system="you", owner="michael",
      proof_kind="shell", freshness_sla="24 hours",
      proof_cmd="test \"$(pmset -g | awk '/^ *sleep/{print $2; exit}')\" = 0",
      how="On the charger: sudo pmset -c sleep 0 disksleep 0 standby 0. Keep the lid open (or use Amphetamine for lid-closed)."),
    P(id="you.repo.off_icloud", claim="ProjectUtah moved off iCloud Desktop", system="you", owner="michael",
      proof_kind="shell", proof_cmd="test -d $HOME/ProjectUtah && { ! test -d \"$HOME/Desktop/ProjectUtah\" || test -L \"$HOME/Desktop/ProjectUtah\"; }",
      how="Tell Ace 'move the repo off iCloud' — it relocates ProjectUtah to ~/ProjectUtah and rewires all 26 jobs. Never move it by hand (breaks launchd paths)."),
    P(id="you.ace.deleted", claim="dead ~/.ace (82GB) removed", system="you", owner="michael",
      proof_kind="shell", proof_cmd="! test -d $HOME/.ace",
      how="Reclaim 82GB of dead AceOS: rm -rf ~/.ace (irreversible). Tell Ace 'delete .ace' and it does it after one confirm."),
    P(id="you.google.oauth", claim="Google OAuth refresh_token present", system="you", owner="michael",
      proof_kind="shell", proof_cmd="grep -q refresh_token $HOME/.utah/secrets/google.json",
      how="client_id/secret already set. Run: python ops/google_oauth_setup.py → browser opens → click Allow. (OAuth client must be type 'Desktop app'.)",
      link="https://console.cloud.google.com/apis/credentials"),
    P(id="you.instagram.connected", claim="Instagram account connected (not a template)", system="you",
      owner="michael", proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/instagram.json",
      how="Create a Meta/Instagram app, then copy instagram.json.example → ~/.utah/secrets/instagram.json and fill in the token.",
      link="https://developers.facebook.com/apps"),
    P(id="you.tiktok.connected", claim="TikTok account connected", system="you", owner="michael",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/tiktok.json",
      how="Register a TikTok developer app (Content Posting API), save creds to ~/.utah/secrets/tiktok.json.",
      link="https://developers.tiktok.com/"),
    P(id="you.linkedin.connected", claim="LinkedIn account connected", system="you", owner="michael",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/linkedin.json",
      how="Create a LinkedIn developer app, save creds to ~/.utah/secrets/linkedin.json.",
      link="https://www.linkedin.com/developers/apps"),
    P(id="you.website.download_fixed", claim="blacklabelbots.com/download works (you own the site)",
      system="you", owner="michael", proof_kind="http", freshness_sla="6 hours",
      proof_cmd="https://blacklabelbots.com/download",
      how="The /download route 404s. Fix it on the storefront (you own the site — Ace can't touch it) and re-deploy so /download serves the Sovereign file.",
      link="https://blacklabelbots.com/download"),
    P(id="you.offer.decided", claim="the converting offer is decided + written in ROADMAP", system="you",
      owner="michael", proof_kind="shell",
      proof_cmd="grep -qi '## OFFER' \"$HOME/Desktop/Black Label Bots/ROADMAP.md\" 2>/dev/null || grep -qi '## OFFER' \"$HOME/iCloud Drive (Archive)/Desktop/Black Label Bots/ROADMAP.md\"",
      how="0 sales on 376 sends = the offer, not the tech. Decide the one offer that converts and write it under a '## OFFER' heading in ROADMAP.md."),
    P(id="you.stripe.deduped", claim="Stripe catalog deduped to the real product line", system="you",
      owner="michael",
      how="Stripe has 17 products, ~7 are dups/typos ('Pre Orer'). Archive the junk down to the real ~10-item line.",
      link="https://dashboard.stripe.com/products"),
    # --- Apple Developer (the other world) ---
    P(id="you.xcode.signed", claim="Xcode signed in (Apple Developer) + a codesigning identity exists",
      system="you", owner="michael", proof_kind="shell",
      proof_cmd="security find-identity -p codesigning -v 2>/dev/null | grep -qi 'Apple Develop'",
      how="Xcode ▸ Settings ▸ Accounts ▸ + ▸ sign in with your Apple ID, then put your Team ID in Signing.xcconfig. (Your click — no agent can do the Apple ID login.)",
      link="https://developer.apple.com/account"),
    P(id="you.flutter.installed", claim="Flutter installed (for the iPhone app)", system="you",
      owner="michael", proof_kind="shell", proof_cmd="command -v flutter >/dev/null",
      how="Run: brew install --cask flutter then flutter doctor. (Or tell Ace to install it — this one an agent CAN do.)",
      link="https://docs.flutter.dev/get-started/install/macos"),
]


def seed_skeleton() -> int:
    for spec in _SKELETON:
        proof.register(spec)
    return len(_SKELETON)


def seed_meta() -> int:
    for spec in _META:
        proof.register(spec)
    return len(_META)


def seed_human() -> int:
    for spec in _HUMAN:
        proof.register(spec)
    return len(_HUMAN)


def seed_all() -> int:
    return seed_skeleton() + seed_meta() + seed_human()

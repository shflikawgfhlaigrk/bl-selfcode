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
      system="growth", artifact="utah/product/leads.py:run_scheduled", freshness_sla="26 hours"),
    P(id="pipeline.enrich.adds_contacts", claim="enrich finds emails/phones for leads",
      system="growth", artifact="utah/product/enrich.py"),
    P(id="pipeline.outreach.sends_gated", claim="outreach sends only to verified, deliverable addresses",
      system="growth", artifact="utah/product/outreach.py"),
    P(id="pipeline.marketer.posts", claim="marketer produces posts",
      system="growth", artifact="utah/product/marketer.py"),
    P(id="pipeline.replies.reads_imap", claim="replies reader ingests inbound mail",
      system="growth", artifact="utah/product/ledger.py", freshness_sla="2 hours"),
    P(id="pipeline.mailcheck.polls", claim="mailcheck polls inbound mail", system="growth",
      artifact="utah/mail.py", freshness_sla="3 hours"),
    P(id="pipeline.leads_maps.harvest", claim="maps harvest adds SMB leads", system="growth",
      artifact="utah/product/leads.py"),
    # --- Real Estate ---
    P(id="pipeline.probate.scrapes", claim="probate scraper adds records", system="realestate",
      artifact="utah/product/probate.py", freshness_sla="26 hours"),
    P(id="pipeline.probate_enrich.values", claim="probate enrich adds ARV + heir contacts",
      system="realestate", artifact="utah/product/property.py"),
    P(id="pipeline.probate_outreach.mails", claim="probate outreach mails letters", system="realestate",
      artifact="utah/product/probate_outreach.py"),
    # --- Trading ---
    P(id="pipeline.wcfeed.live_ticks", claim="market feed ticks during open session", system="trading",
      artifact="utah/canary.py:check_ticks", freshness_sla="5 minutes"),
    P(id="pipeline.signals.generates", claim="signals are generated", system="trading",
      artifact="utah/product/signals.py", freshness_sla="1 hour"),
    P(id="pipeline.grade_fires.honest", claim="fires are graded honestly", system="trading",
      artifact="utah/product/fire_grader.py"),
    P(id="pipeline.engine_audit.proves_edge", claim="engine audit recomputes OOS edge nightly",
      system="trading", artifact="utah/product/engine_audit.py", freshness_sla="26 hours"),
    # --- Engineering / self ---
    P(id="pipeline.selfcode.merges_real", claim="selfcode merges real, tested commits", system="eng",
      artifact="utah/sica.py"),
    P(id="pipeline.selfaudit.runs", claim="selfaudit runs", system="eng", artifact="utah/sica.py"),
    P(id="pipeline.codeindex.indexes", claim="codeindex ingests the codebase", system="eng",
      artifact="utah/codebase.py"),
    P(id="pipeline.consolidate.memory", claim="memory consolidation runs", system="eng",
      artifact="utah/memory/store.py", freshness_sla="1 hour"),
    # --- Infra ---
    P(id="infra.supervisor.alive", claim="supervisor daemon is alive", system="infra",
      artifact="utah/daemon.py", freshness_sla="15 minutes"),
    P(id="infra.deck.honest", claim="deck /state matches the DB (no fabrication)", system="infra",
      artifact="utah/interface/web.py", freshness_sla="15 minutes"),
    P(id="infra.postgres.reachable", claim="postgres :5433 reachable", system="infra",
      artifact="utah/db_pool.py", freshness_sla="15 minutes"),
    P(id="infra.canary.runs", claim="canary sweep runs", system="infra", artifact="utah/canary.py",
      freshness_sla="20 minutes"),
    P(id="infra.tailserve.serves", claim="deck served to phone via tailscale", system="infra",
      artifact="utah/canary.py"),
    P(id="infra.operator.heals", claim="operator loop runs", system="infra", artifact="utah/canary.py",
      freshness_sla="10 minutes"),
    P(id="infra.verify.gate", claim="verify gate runs", system="infra", artifact="ops/verify.py"),
    # --- Comms ---
    P(id="comms.brief.sends", claim="morning brief sends", system="comms", artifact="utah/product/brief.py",
      freshness_sla="26 hours"),
    P(id="comms.discord_bot.heartbeat", claim="discord bot heartbeats", system="comms",
      artifact="utah/integrations/wc_feed.py", freshness_sla="1 hour"),
    # --- Products (sellable) ---
    P(id="product.website.up", claim="blacklabelbots.com serves real content", system="website",
      artifact="utah/product/sitegen.py", freshness_sla="1 hour"),
    P(id="product.sovereign.download", claim="Sovereign download path works (currently 404)",
      system="products", artifact="utah/product/sitegen.py", freshness_sla="1 hour"),
    P(id="product.stripe.catalog_clean", claim="Stripe catalog deduped to the real product line",
      system="revenue", artifact="ops/grade-queue.json"),
    # --- Social ---
    P(id="social.discord.live", claim="discord is wired and live", system="social",
      artifact="utah/integrations/wc_feed.py"),
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

_HUMAN = [   # owner=michael, system="you" — RED until the artifact of HIS action exists
    P(id="you.mac.no_sleep", claim="Mac will not sleep (24/7 enabled)", system="you", owner="michael",
      proof_kind="shell", freshness_sla="24 hours",
      proof_cmd="test \"$(pmset -g | awk '/^ *sleep/{print $2; exit}')\" = 0"),
    P(id="you.repo.off_icloud", claim="ProjectUtah moved off iCloud Desktop", system="you", owner="michael",
      proof_kind="shell", proof_cmd="test -d $HOME/ProjectUtah && ! test -d \"$HOME/Desktop/ProjectUtah\""),
    P(id="you.ace.deleted", claim="dead ~/.ace (82GB) removed", system="you", owner="michael",
      proof_kind="shell", proof_cmd="! test -d $HOME/.ace"),
    P(id="you.google.oauth", claim="Google OAuth refresh_token present", system="you", owner="michael",
      proof_kind="shell", proof_cmd="grep -q refresh_token $HOME/.utah/secrets/google.json"),
    P(id="you.instagram.connected", claim="Instagram account connected (not a template)", system="you",
      owner="michael", proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/instagram.json"),
    P(id="you.tiktok.connected", claim="TikTok account connected", system="you", owner="michael",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/tiktok.json"),
    P(id="you.linkedin.connected", claim="LinkedIn account connected", system="you", owner="michael",
      proof_kind="shell", proof_cmd="test -f $HOME/.utah/secrets/linkedin.json"),
    P(id="you.website.download_fixed", claim="blacklabelbots.com/download works (you own the site)",
      system="you", owner="michael", proof_kind="http", freshness_sla="6 hours",
      proof_cmd="https://blacklabelbots.com/download"),
    P(id="you.offer.decided", claim="the converting offer is decided + written in ROADMAP", system="you",
      owner="michael", proof_kind="shell",
      proof_cmd="grep -qi '## OFFER' \"$HOME/Desktop/Black Label Bots/ROADMAP.md\""),
    P(id="you.stripe.deduped", claim="Stripe catalog deduped to the real product line", system="you",
      owner="michael"),   # genuinely-manual: dormant RED until a real check is wired (never fake-passed)
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

"""Self-measuring lead quality — measure the scraper's "actually no website" precision.

The audit's "never-done-before" lift: the scraper defines a lead as "no website" from the
absence of OSM website tags + a hand-curated chain denylist — but a business with only a
Facebook page or an untagged site slips through, and that false-positive rate was never
MEASURED. This samples leads flagged "no website", checks each for a real site on the open
web, and reports precision (fraction genuinely site-less). The number turns lead quality
from a hope into a measured property and tells the scraper where its filter leaks.

Pure orchestration: the website check is injectable (web search by default), so the metric
is unit-proven offline and run live on a sample.
"""
from __future__ import annotations

import logging
import re

import msgspec

log = logging.getLogger("utah.product.leads_eval")

#: Domains that aren't a real business site (so finding one is NOT a false positive).
_NON_SITE_HOSTS = ("facebook.com", "instagram.com", "yelp.com", "mapquest.com",
                   "yellowpages.com", "linkedin.com", "tripadvisor.com", "doordash.com",
                   "ubereats.com", "grubhub.com", "google.com", "bbb.org", "nextdoor.com")
_URL_RE = re.compile(r"https?://([a-z0-9.\-]+)", re.I)


def _looks_like_own_site(url: str, business_name: str) -> bool:
    """A result URL is the business's OWN site if its host isn't a directory/social host AND
    a business-name token appears in the host (so a random org's site isn't miscounted)."""
    m = _URL_RE.search(url or "")
    if not m:
        return False
    host = m.group(1).lower()
    if any(h in host for h in _NON_SITE_HOSTS):
        return False
    toks = [t for t in re.findall(r"[a-z0-9]{4,}", (business_name or "").lower())
            if t not in ("the", "and", "llc", "inc", "shop", "store")]
    bare = re.sub(r"[^a-z0-9]", "", host)
    return any(t in bare for t in toks)


def has_real_website(lead: dict, *, search_fn=None) -> bool:
    """True if the business appears to have its OWN website on the open web (a false positive
    for a 'no website' lead). Social/directory listings don't count. Best-effort; a blocked/
    empty search returns False (we don't fabricate a false positive)."""
    name = (lead.get("name") or "").strip()
    if not name:
        return False
    region = (lead.get("region") or "").strip()
    try:
        from utah.product import researcher
        results = (search_fn or researcher.search)(f'{name} {region} official website', 5)
    except Exception as exc:  # noqa: BLE001
        log.debug("leads_eval search failed for %s: %s", name, exc)
        return False
    return any(_looks_like_own_site(url, name) for _title, url in results)


class LeadQuality(msgspec.Struct, frozen=True):
    sampled: int
    no_website_confirmed: int
    false_positives: int          # flagged "no website" but actually has one
    precision: float              # confirmed / sampled — the headline quality number
    false_positive_rate: float
    leaked: list[str]             # names of the false positives (where the filter leaks)


def measure_precision(leads, *, check_fn=None) -> LeadQuality:
    """Sample *leads* (already flagged 'no website') and measure how many are genuinely
    site-less. ``check_fn(lead) -> bool`` (has a real site) is injectable. Empty → zeroed."""
    leads = list(leads or [])
    n = len(leads)
    if n == 0:
        return LeadQuality(0, 0, 0, 0.0, 0.0, [])
    check = check_fn or has_real_website
    leaked = [l.get("name", "?") for l in leads if check(l)]
    fp = len(leaked)
    confirmed = n - fp
    return LeadQuality(
        sampled=n, no_website_confirmed=confirmed, false_positives=fp,
        precision=round(confirmed / n, 4), false_positive_rate=round(fp / n, 4),
        leaked=leaked,
    )


def run_eval(sample: int = 25, *, ledger=None, check_fn=None) -> dict:
    """Sample recent SMB leads and measure 'actually no website' precision. Records the
    score so a regression in lead quality is visible. Returns a plain dict for the deck.

    Cron-shaped boundary: a dead lead store yields a zeroed, honest result with an
    ``error`` and a recorded failure — never a raise, never a fabricated precision."""
    from utah import failures

    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    try:
        leads = ledger.leads_missing_email(sample)   # recent SMB leads (the no-website pile)
    except Exception as exc:  # noqa: BLE001 — store down: zeroed + documented, never invented
        failures.record("leads", "eval_store_unreachable",
                        f"lead-quality eval skipped (store unreachable): {exc}")
        log.warning("leads_eval store unreachable: %s", exc)
        out = msgspec.structs.asdict(LeadQuality(0, 0, 0, 0.0, 0.0, []))
        out["error"] = str(exc)
        return out
    q = measure_precision(leads, check_fn=check_fn)
    if q.sampled and q.false_positive_rate > 0.25:
        failures.record("leads", "low_precision",
                        f"'no website' precision {q.precision:.0%} on {q.sampled} sampled — "
                        f"{q.false_positives} actually have a site ({', '.join(q.leaked)[:80]})")
    return msgspec.structs.asdict(q)


__all__ = ["has_real_website", "LeadQuality", "measure_precision", "run_eval"]

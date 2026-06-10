"""Email enrichment — turn a lead ``{name, region, contact}`` into a real business email.

This is the supply engine for the audit's §1.1 revenue ceiling: the leads pile is ~4,000
businesses but only a handful carry an email, so cold-email runs out of prospects at ~22/day.
Enrichment reuses the primitives that already exist — ``researcher.search`` (free DDG),
a raw HTML fetch + ``browser.render`` (headless Chrome) fallback — to find the business's
site/listing, scrape a contact email, junk-filter it, verify the domain resolves, and write
it back via ``ledger.update_lead``. It NEVER fabricates an address; a lead with no findable
email is simply left as-is for the next pass.

Niche-broadening (Michael, 2026-06-09): businesses that HAVE a website are the *easy* case —
their contact page carries the email directly — so enrichment is highest-yield exactly where
the old leads filter used to throw those businesses away.
"""
from __future__ import annotations

import logging
import re
import socket
from urllib.parse import urljoin

log = logging.getLogger("utah.product.enrich")

#: Pragmatic email matcher — local@domain.tld, no leading/trailing dot in the local part.
EMAIL_RE = re.compile(r"[A-Za-z0-9_%+\-](?:[A-Za-z0-9._%+\-]*[A-Za-z0-9_%+\-])?@"
                      r"[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")

#: Local-parts that are never a sales contact (role/bounce/monitoring addresses).
_JUNK_LOCAL = frozenset({
    "noreply", "no-reply", "donotreply", "do-not-reply", "privacy", "abuse", "postmaster",
    "mailer-daemon", "webmaster", "hostmaster", "root", "sentry", "wixpress",
    # placeholder/template locals scraped off sign-in forms & boilerplate
    "username", "user", "name", "firstname", "lastname", "yourname", "your-email",
    "youremail", "email", "test", "example", "first.last",
})
#: Domains that are placeholders / vendor boilerplate / asset CDNs, never a real prospect.
_JUNK_DOMAINS = frozenset({
    "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "example.com", "example.org",
    "example.net", "godaddy.com", "squarespace.com", "wix.com", "domain.com", "email.com",
    "yourdomain.com", "yoursite.com", "cloudflare.com", "schema.org", "w3.org",
})
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")
#: Inboxes most likely to reach the owner, in priority order.
_PREFERRED = ("info", "contact", "hello", "sales", "office", "owner", "admin",
              "booking", "reservations", "service")


def _is_junk(email: str) -> bool:
    if "@" not in email:
        return True
    if email.endswith(_IMG_EXT):                 # foo@2x.png style asset false-positive
        return True
    local, _, domain = email.partition("@")
    if not local or not domain or ".." in email:
        return True
    if local.endswith(_IMG_EXT):                 # logo.png@cdn.com asset false-positive
        return True
    if local in _JUNK_LOCAL:
        return True
    if domain in _JUNK_DOMAINS:
        return True
    if domain.endswith(_IMG_EXT):
        return True
    return False


def extract_emails(html: str) -> list[str]:
    """All non-junk emails in *html* (raw HTML or text), lowercased and de-duplicated,
    in first-seen order. Catches both ``mailto:`` hrefs and visible-text addresses."""
    out: list[str] = []
    for raw in EMAIL_RE.findall(html or ""):
        email = raw.strip().strip(".").lower()
        if _is_junk(email) or email in out:
            continue
        out.append(email)
    return out


def best_email(candidates: list[str]) -> str | None:
    """Pick the address most likely to reach the owner: a preferred role inbox
    (info@/contact@/…) if present, else the first candidate."""
    if not candidates:
        return None
    for pref in _PREFERRED:
        for e in candidates:
            if e.split("@", 1)[0] == pref:
                return e
    return candidates[0]


#: Name tokens that don't identify a business (so they can't anchor an entity match).
_NAME_STOPWORDS = frozenset({
    "the", "and", "llc", "inc", "co", "corp", "company", "ltd", "store", "shop", "group",
    "service", "services", "center", "of", "for", "one", "stop", "best", "new", "old",
    "auto", "home", "city", "county", "first",
})


def _name_tokens(name: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]{4,}", (name or "").lower())
    return [t for t in toks if t not in _NAME_STOPWORDS]


def _entity_match(email: str, business_name: str) -> bool:
    """True when *email* plausibly belongs to *business_name* — a business-name token (≥4
    chars, non-generic) appears in the email's local-part or domain. Blind web search grabs
    whatever address is on the top result, so this guards against cold-emailing the WRONG
    business (a UGA staffer for 'University ACE'). No tokens to match on → reject (can't verify)."""
    tokens = _name_tokens(business_name)
    if not tokens:
        return False
    haystack = re.sub(r"[^a-z0-9]", "", (email or "").lower())
    return any(t in haystack for t in tokens)


def domain_resolves(domain: str) -> bool:
    """Best-effort validity check (no dnspython): the domain must resolve. Drops obvious
    typos / .invalid / dead domains before they bounce against the sender's reputation."""
    try:
        socket.getaddrinfo(domain, None)
        return True
    except (socket.gaierror, OSError, UnicodeError, ValueError):
        return False


def _raw_fetch(url: str) -> str:
    """Raw HTML (NOT stripped to text — we need ``mailto:`` hrefs), with a browser-like UA;
    falls back to headless Chrome for JS-rendered pages. Returns '' on any failure."""
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(800_000).decode("utf-8", "replace")
        if "@" in html or len(html) > 500:
            return html
    except Exception as exc:  # noqa: BLE001
        log.debug("raw fetch failed %s: %s", url, exc)
    try:
        from utah.integrations import browser
        res = browser.render(url)
        if res.get("rendered"):
            return res.get("html", "")
    except Exception as exc:  # noqa: BLE001
        log.debug("chrome render failed %s: %s", url, exc)
    return ""


def _candidate_urls(lead: dict, search_fn) -> list[str]:
    contact = lead.get("contact") or {}
    site = (contact.get("website") or "").strip()
    if site:
        if not site.startswith(("http://", "https://")):
            site = "https://" + site
        return [site] + [urljoin(site + "/", p) for p in ("contact", "about", "contact-us")]
    query = f'{lead.get("name", "")} {lead.get("region", "")} email contact'.strip()
    try:
        return [url for _title, url in (search_fn or _search)(query, 5) if url]
    except Exception as exc:  # noqa: BLE001
        log.debug("search failed for %s: %s", query, exc)
        return []


def _search(query: str, k: int):
    from utah.product import researcher
    return researcher.search(query, k)


def find_email(lead: dict, *, fetch_html=None, search_fn=None, verify_fn=None) -> dict:
    """Find one real business email for *lead*: try its website's contact/about pages, else
    discover its listing via search, scrape, junk-filter, and keep the first whose domain
    resolves. Returns ``{email, source_url}`` or ``{email: None}``. All I/O is injectable."""
    fetch_html = fetch_html or _raw_fetch
    verify_fn = verify_fn or domain_resolves
    name = lead.get("name", "")
    seen: set[str] = set()
    for url in _candidate_urls(lead, search_fn):
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            html = fetch_html(url) or ""
        except Exception as exc:  # noqa: BLE001
            log.debug("fetch %s failed: %s", url, exc)
            continue
        candidates = [e for e in extract_emails(html)
                      if _entity_match(e, name) and verify_fn(e.split("@", 1)[1])]
        email = best_email(candidates)
        if email:
            return {"email": email, "source_url": url}
    return {"email": None}


def enrich_lead(ledger, lead: dict, *, find_fn=None) -> dict:
    """Find an email for *lead* and, on success, merge it onto the ledger row. Returns the
    find result. Suppression/CAN-SPAM are downstream in outreach; this only fills contact."""
    result = (find_fn or find_email)(lead)
    if result.get("email"):
        ledger.update_lead(lead.get("name"), lead.get("region"),
                           contact={"email": result["email"]})
    return result


def _leads_needing_email(limit: int) -> list[dict]:
    """SMB leads (osm|google_maps) with a phone or website but no email yet — phone-first,
    since those are the most contactable. The pool enrichment drains toward outreach."""
    import psycopg

    from utah import config
    from utah.product.ledger import SMB_LEAD_SOURCES

    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        rows = c.execute(
            "SELECT name, kind, region, contact FROM leads "
            "WHERE source = ANY(%s) "
            "AND (contact->>'email' IS NULL OR contact->>'email' = '') "
            "AND (contact->>'website' IS NOT NULL OR contact->>'phone' IS NOT NULL) "
            "ORDER BY (contact->>'website' IS NOT NULL) DESC, ts DESC "
            "LIMIT %s",
            (list(SMB_LEAD_SOURCES), limit),
        ).fetchall()
    return [{"name": r[0], "kind": r[1], "region": r[2], "contact": r[3] or {}} for r in rows]


def run_scheduled(limit: int = 50, *, ledger=None, lead_fetch=None, find_fn=None,
                  foundation_gate=None) -> dict:
    """``com.utah.enrich`` cron — pull leads needing an email, find + store one each. Runs
    before the outreach cron so freshly-enriched leads are sendable the same hour. Skips on
    a red substrate (same gate as every other cron)."""
    from utah import foundation

    skip = (foundation.gate_cron if foundation_gate is None else foundation_gate)("enrich")
    if skip:
        return skip
    if ledger is None:
        from utah.product.ledger import get_ledger
        ledger = get_ledger()
    leads = (lead_fetch or _leads_needing_email)(limit)
    enriched = scanned = 0
    for lead in leads:
        scanned += 1
        result = enrich_lead(ledger, lead, find_fn=find_fn)
        if result.get("email"):
            enriched += 1
    log.info("enrich run_scheduled: scanned=%d enriched=%d", scanned, enriched)
    return {"scanned": scanned, "enriched": enriched}


__all__ = ["extract_emails", "best_email", "domain_resolves", "find_email",
           "enrich_lead", "run_scheduled"]

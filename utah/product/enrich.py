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
import subprocess
from urllib.parse import urljoin, urlparse

log = logging.getLogger("utah.product.enrich")

#: Schemes the fetcher will touch. Search results are UNTRUSTED input — a hostile or
#: garbage listing must never make the cron read file:// or follow javascript:.
_FETCHABLE_SCHEMES = frozenset({"http", "https"})


def _is_fetchable(url: str) -> bool:
    try:
        return urlparse(url or "").scheme.lower() in _FETCHABLE_SCHEMES
    except ValueError:        # malformed port/netloc — not a fetchable URL
        return False

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


#: Common two-label public suffixes — ``acme.co.uk`` is not the same registrant as ``co.uk``.
_TWO_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk",
    "com.au", "net.au", "org.au", "edu.au",
    "co.nz", "co.jp", "co.kr", "com.br", "com.mx",
})

_DIG_TIMEOUT = 10   # seconds — bounded; never block the enrich cron on a slow resolver


def _safe_dig_domain(domain: str | None) -> str | None:
    """Hostname safe to pass as a ``dig`` argv tail — rejects flags, spaces, shell junk."""
    d = (domain or "").strip().lower().strip(".")
    if not d or d.startswith("-") or " " in d or ";" in d:
        return None
    if not re.fullmatch(r"[a-z0-9._-]+", d):
        return None
    return d


def _dig(rtype: str, domain: str | None) -> list[str]:
    """One bounded ``dig +short`` lookup. Empty on any failure or unsafe *domain*."""
    safe = _safe_dig_domain(domain)
    if not safe:
        return []
    try:
        out = subprocess.run(
            ["dig", "+short", rtype, safe],
            capture_output=True,
            text=True,
            timeout=_DIG_TIMEOUT,
        )
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def _registrable_domain(host: str) -> str:
    """Best-effort registrable domain (``joesdiner.com`` from ``www.joesdiner.com:8443``)."""
    host = (host or "").lower().strip().strip(".")
    if host.count(":") == 1:
        left, right = host.rsplit(":", 1)
        if right.isdigit():
            host = left
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _website_domains(website: str) -> set[str]:
    """Domains implied by a lead's own website URL."""
    try:
        host = urlparse(website if "://" in website else f"https://{website}").netloc
    except ValueError:        # urlparse rejects malformed ports/brackets
        return set()
    reg = _registrable_domain(host)
    return {reg} if reg else set()


def _email_on_website(email: str, website: str) -> bool:
    """True when *email*'s domain matches the lead's own site (info@joesdiner.com on joesdiner.com)."""
    if "@" not in email:
        return False
    domain = _registrable_domain(email.rsplit("@", 1)[1])
    return bool(domain) and domain in _website_domains(website)


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
    """Best-effort validity check (no dnspython): the domain must resolve via bounded
    ``dig`` A/AAAA lookups — never ``getaddrinfo`` (no timeout knob)."""
    if not _safe_dig_domain(domain):
        return False
    if _dig("A", domain):
        return True
    return bool(_dig("AAAA", domain))


def _dig_mx(domain: str) -> list[str]:
    """MX records for *domain* via the shared bounded ``dig`` helper."""
    return _dig("MX", domain)


def domain_accepts_mail(domain: str, *, mx_lookup=None, a_lookup=None) -> bool:
    """The real deliverability gate: does *domain* ACCEPT mail? An MX record proves it;
    failing that, RFC 5321 §5.1 says a bare A-record host still accepts mail (the fallback).
    A domain that merely *resolves* (has a website) but has neither is a guaranteed hard
    bounce — and hard bounces are what blacklist a sender — so it is rejected here. All
    lookups are injectable for hermetic tests."""
    domain = (domain or "").strip().lower()
    if not domain:
        return False
    mx_lookup = mx_lookup or _dig_mx
    a_lookup = a_lookup or domain_resolves
    try:
        if mx_lookup(domain):
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("mx lookup failed %s: %s", domain, exc)
    try:
        return bool(a_lookup(domain))
    except Exception:  # noqa: BLE001
        return False


def verify_email(email: str, *, mx_fn=None) -> dict:
    """Pre-send verdict for one address — the gate outreach MUST pass before sending, so we
    only ever send to addresses that can actually receive mail. Returns
    ``{deliverable, reason, confidence}``: rejects bad syntax (``bad-syntax``), role/bounce
    inboxes (``role-or-junk``), and domains with no mail server (``no-mail-server``); a clean
    address on a mail-accepting domain is ``deliverable`` at confidence ``mx``. Never does a
    RCPT-TO probe — that gets the prober blacklisted and breaks on catch-all domains."""
    addr = (email or "").strip().lower()
    if not EMAIL_RE.fullmatch(addr):
        return {"deliverable": False, "reason": "bad-syntax", "confidence": ""}
    if _is_junk(addr):
        return {"deliverable": False, "reason": "role-or-junk", "confidence": ""}
    mx_fn = mx_fn or domain_accepts_mail
    domain = addr.rsplit("@", 1)[1]
    if not mx_fn(domain):
        return {"deliverable": False, "reason": "no-mail-server", "confidence": ""}
    return {"deliverable": True, "reason": "ok", "confidence": "mx"}


def _raw_fetch(url: str) -> str:
    """Raw HTML (NOT stripped to text — we need ``mailto:`` hrefs), with a browser-like UA;
    falls back to headless Chrome for JS-rendered pages. Refuses non-http(s) schemes
    outright (untrusted search results). Returns '' on any failure."""
    import urllib.request

    if not _is_fetchable(url):
        log.debug("refusing non-http(s) url %r", url)
        return ""
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
        # untrusted results: keep only http(s) candidates (see _FETCHABLE_SCHEMES)
        return [url for _title, url in (search_fn or _search)(query, 5)
                if url and _is_fetchable(url)]
    except Exception as exc:  # noqa: BLE001 — search boundary: no email beats a crash
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
    verify_fn = verify_fn or domain_accepts_mail
    name = lead.get("name", "")
    own_site = (lead.get("contact") or {}).get("website") or ""
    own_domains = _website_domains(own_site) if own_site else set()
    seen: set[str] = set()
    for url in _candidate_urls(lead, search_fn):
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            html = fetch_html(url) or ""
        except Exception as exc:  # noqa: BLE001 — one dead page must not end the hunt
            log.debug("fetch %s failed: %s", url, exc)
            continue
        from_own_site = bool(own_domains) and _registrable_domain(
            urlparse(url if "://" in url else f"https://{url}").netloc) in own_domains
        candidates: list[str] = []
        for e in extract_emails(html):
            dom = e.split("@", 1)[1]
            if not verify_fn(dom):
                continue
            if from_own_site and _email_on_website(e, own_site):
                candidates.append(e)
            elif _entity_match(e, name):
                candidates.append(e)
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


def enrich_and_generate(ledger, lead: dict, *, find_fn=None, out_dir=None) -> dict:
    """Enrich one lead (email onto the row) then render + record the sitegen preview through
    the same ledger path outreach uses. Returns the find result with a ``site`` bag from
    :func:`utah.product.sitegen.generate`. Hermetic tests inject ``find_fn`` + a fake ledger."""
    result = enrich_lead(ledger, lead, find_fn=find_fn)
    gen_lead = dict(lead)
    if result.get("email"):
        contact = dict(gen_lead.get("contact") or {})
        contact["email"] = result["email"]
        gen_lead["contact"] = contact
    from utah.product import sitegen

    result["site"] = sitegen.generate(gen_lead, out_dir=out_dir, ledger=ledger)
    return result


def _leads_needing_email(limit: int) -> list[dict]:
    """SMB leads (osm|google_maps) with a phone or website but no email yet — phone-first,
    since those are the most contactable. The pool enrichment drains toward outreach.

    Reads through the shared bounded pool (:mod:`utah.db_pool` — connect AND checkout
    both time-boxed, same as tasks.py), so a stalled Postgres can never hang the cron;
    the previous fresh ``psycopg.connect`` had no connect timeout."""
    from utah import config, db_pool
    from utah.product.ledger import SMB_LEAD_SOURCES

    with db_pool.get_pool(config.DB_DSN).connection() as c:
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
    a red substrate (same gate as every other cron).

    Cron boundary: NEVER raises into launchd. A dead Postgres returns an explicit
    ``error`` dict (honest failure, not a stack trace), and one bad lead is counted in
    ``failed`` instead of killing the rest of the batch."""
    from utah import foundation

    skip = (foundation.gate_cron if foundation_gate is None else foundation_gate)("enrich")
    if skip:
        return skip
    try:
        if ledger is None:
            from utah.product.ledger import get_ledger
            ledger = get_ledger()
        leads = (lead_fetch or _leads_needing_email)(limit)
    except Exception as exc:  # noqa: BLE001 — boundary: report the outage, don't crash
        log.warning("enrich run_scheduled: lead fetch failed: %s", exc)
        return {"scanned": 0, "enriched": 0, "failed": 0,
                "error": f"lead fetch failed: {exc}"}
    enriched = scanned = failed = 0
    for lead in leads:
        scanned += 1
        try:
            result = enrich_lead(ledger, lead, find_fn=find_fn)
        except Exception as exc:  # noqa: BLE001 — one bad lead must not kill the batch
            failed += 1
            log.warning("enrich failed for %r: %s", lead.get("name"), exc)
            continue
        if result.get("email"):
            enriched += 1
    log.info("enrich run_scheduled: scanned=%d enriched=%d failed=%d",
             scanned, enriched, failed)
    return {"scanned": scanned, "enriched": enriched, "failed": failed}


__all__ = ["extract_emails", "best_email", "domain_resolves", "domain_accepts_mail",
           "verify_email", "find_email", "enrich_lead", "enrich_and_generate", "run_scheduled"]

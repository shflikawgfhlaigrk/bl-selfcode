"""People-level contact engine — owning Apollo's moat in-house.

Apollo/Hunter's value is decision-maker contacts: a person's name + their company → a verified
email. They don't divine it; they generate the standard email PATTERNS a company uses, rank by
commonality, and gate on real MX deliverability. We do the same — free, reusing
:mod:`utah.product.enrich`'s in-house MX verifier — so the contact data is an asset *we own*,
not a per-seat subscription.

Honest by design: we deliberately do NOT RCPT-probe individual mailboxes (that burns sender
reputation, the same reason enrich avoids it). So a pattern on a mail-accepting domain is a
ranked GUESS at confidence ``pattern+mx``, never a fabricated certainty. The full candidate
list is returned so outreach can try the ranked alternatives.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from utah.product import enrich


def _tok(s: str) -> str:
    """A name part reduced to bare lowercase letters (drops spaces, hyphens, apostrophes)."""
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _domain(domain: str) -> str:
    return (domain or "").strip().lower().lstrip("@")


def email_patterns(first: str, last: str, domain: str) -> list[str]:
    """The candidate emails for *first*/*last* at *domain*, ranked most-common-format first
    (``first.last`` > ``flast`` > … > ``first`` > ``last``). De-duplicated, lowercased. Empty
    when there's no usable name or domain."""
    f, ln = _tok(first), _tok(last)
    d = _domain(domain)
    if not d or not (f or ln):
        return []
    fi, lni = f[:1], ln[:1]
    locals_: list[str] = []
    if f and ln:
        locals_ += [f"{f}.{ln}", f"{fi}{ln}", f"{f}{ln}", f"{f}_{ln}", f"{f}-{ln}",
                    f"{fi}.{ln}", f"{f}{lni}", f"{ln}.{f}", f"{ln}{fi}"]
    if f:
        locals_.append(f)
    if ln:
        locals_.append(ln)
    seen: set[str] = set()
    out: list[str] = []
    for loc in locals_:
        if loc and loc not in seen:
            seen.add(loc)
            out.append(f"{loc}@{d}")
    return out


def guess_email(first: str, last: str, domain: str, *, verify_fn=None) -> dict:
    """Best email for a person at *domain*: the top-ranked pattern, gated on the domain really
    accepting mail (MX). Returns ``{email, confidence, candidates, reason}``. ``email`` is None
    (confidence ``none``) when there's no input or the domain won't accept mail — never a guess
    dressed up as confirmed."""
    cands = email_patterns(first, last, domain)
    if not cands:
        return {"email": None, "confidence": "none",
                "reason": "need a name and a domain", "candidates": []}
    verify = verify_fn or enrich.verify_email
    v = verify(cands[0])
    if not v.get("deliverable"):
        return {"email": None, "confidence": "none",
                "reason": f"domain not accepting mail ({v.get('reason')})", "candidates": cands}
    return {"email": cands[0], "confidence": "pattern+mx", "candidates": cands,
            "reason": "top-ranked pattern on a mail-accepting domain (not RCPT-confirmed)"}


def _split_name(name: str) -> tuple[str, str]:
    parts = (name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def find_contacts(company: str, domain: str, people: list[dict], *, verify_fn=None) -> list[dict]:
    """Turn known *people* (``{name, title}``) at *company*/*domain* into ranked, MX-gated
    contacts. Each: ``{name, title, company, domain, email, confidence, candidates, source}``.
    The people themselves come from a discovery layer (web/team-page scrape) — built next."""
    out: list[dict] = []
    for p in people:
        first, last = _split_name(p.get("name", ""))
        g = guess_email(first, last, domain, verify_fn=verify_fn)
        out.append({
            "name": p.get("name", ""), "title": p.get("title", ""),
            "company": company, "domain": domain,
            "email": g["email"], "confidence": g["confidence"],
            "candidates": g["candidates"], "source": "pattern",
        })
    return out


# ── contact discovery (find the decision-makers from the company's own site) ────────────
#: Role words that mark a line/snippet as a person's title — the gate that stops the name
#: extractor from inventing "people" out of stray capitalized words (headings, CTAs, etc.).
_ROLE_WORDS = (
    "owner", "co-owner", "founder", "co-founder", "cofounder", "ceo", "cfo", "coo", "cto",
    "president", "vice president", "vp", "director", "manager", "principal", "partner",
    "broker", "proprietor", "chief", "head", "lead", "attorney", "lawyer", "realtor",
    "agent", "dentist", "doctor", "physician", "md", "dds", "esq", "operator",
)

#: Common team/leadership page paths to probe (the page the client gave is checked too).
_TEAM_PATHS = ("/team", "/about", "/about-us", "/our-team", "/staff", "/leadership",
               "/meet-the-team", "/people", "/our-staff", "/management")

#: First [M.] Last — two/three capitalized tokens. Conservative: needs ≥2 real name words.
_NAME = r"[A-Z][a-z'’]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’]+"
_LINE_NAME_TITLE = re.compile(rf"^({_NAME})\s*[,\-–—:|]\s*(.+)$")
_BARE_NAME = re.compile(rf"^({_NAME})$")


def _has_role(text: str) -> str:
    t = (text or "").lower()
    return next((r for r in _ROLE_WORDS if r in t), "")


def _clean_lines(html: str) -> list[str]:
    """Strip scripts/styles/tags to visible text lines (entities collapsed to spaces)."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&[#a-z0-9]+;", " ", text)
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def extract_people(html: str) -> list[dict]:
    """Decision-makers (``{name, title}``) parsed from a team/about page's HTML. A name is kept
    only when a role word sits with it — inline (``Name, Owner``) or on the next line (card
    layout: a bare name followed by the title). Heuristic and de-duplicated; never fabricated."""
    lines = _clean_lines(html)
    people: list[dict] = []
    seen: set[str] = set()

    def add(name: str, title: str) -> None:
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            people.append({"name": name, "title": title.strip(" ,-–—:|")})

    for i, ln in enumerate(lines):
        m = _LINE_NAME_TITLE.match(ln)
        if m and _has_role(m.group(2)):
            add(m.group(1), m.group(2))
            continue
        nm = _BARE_NAME.match(ln)
        if nm and i + 1 < len(lines) and _has_role(lines[i + 1]) \
                and not _BARE_NAME.match(lines[i + 1]):
            add(nm.group(1), lines[i + 1])
    return people


def team_page_urls(website: str) -> list[str]:
    """Candidate URLs to scan for people: the page the client gave + common team paths off the
    site root. De-duplicated, order-preserving."""
    w = (website or "").strip()
    if not w:
        return []
    if not w.startswith("http"):
        w = "https://" + w
    parts = urlsplit(w)
    root = urlunsplit((parts.scheme or "https", parts.netloc, "", "", ""))
    urls = [w] + [root + p for p in _TEAM_PATHS]
    return list(dict.fromkeys(urls))


def discover_people(website: str, *, fetch_html=None, max_pages: int = 5) -> list[dict]:
    """Scan a site's team/about pages and return the people found (``{name, title}``), deduped.
    ``fetch_html(url) -> html`` is injected for tests; defaults to enrich's real fetcher."""
    fetch = fetch_html or enrich._raw_fetch
    people: list[dict] = []
    seen: set[str] = set()
    for url in team_page_urls(website)[:max_pages]:
        try:
            html = fetch(url)
        except Exception:  # noqa: BLE001 — one dead page never sinks the scan
            continue
        for p in extract_people(html or ""):
            key = p["name"].lower()
            if key not in seen:
                seen.add(key)
                people.append(p)
    return people


def discover_contacts(company: str, website: str, *, fetch_html=None, verify_fn=None,
                      max_pages: int = 5) -> list[dict]:
    """End-to-end: discover decision-makers on *company*'s *website*, then turn each into a
    verified, ranked email. The whole Apollo path — find the person, find their email — owned
    in-house. Returns the :func:`find_contacts` records."""
    host = urlsplit(website if website.startswith("http") else "https://" + website).netloc
    domain = enrich._registrable_domain(host)
    people = discover_people(website, fetch_html=fetch_html, max_pages=max_pages)
    return find_contacts(company, domain, people, verify_fn=verify_fn)


__all__ = ["email_patterns", "guess_email", "find_contacts",
           "extract_people", "team_page_urls", "discover_people", "discover_contacts"]

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


__all__ = ["email_patterns", "guess_email", "find_contacts"]

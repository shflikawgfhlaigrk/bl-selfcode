"""Probate capability — local estate/probate filings (motivated-seller signal).

Ace's realestate/GPN producer transitions HERE as a capability behind the brain, not an
agent. Source = Georgia Public Notice (georgiapublicnotice.com), the free public estate/
probate aggregator covering Coweta + the metro-Atlanta ring (O.C.G.A. § 9-13-140). It is
a fragile ASP.NET WebForms scrape (session URL, __VIEWSTATE, autopostback, GridView), so
EVERY failure path records a DOCUMENTED reason to the failure log and degrades honestly —
no fabrication, never a crash. Each parsed notice → ``ledger.record_probate`` (Postgres,
never-twice, pushes the ``probate`` deck channel).

The parse/extract layer is pure (unit-tested on real notice text); the fetch is an
injectable boundary so the capability is tested offline.
"""
from __future__ import annotations

import gzip
import html as _html
import http.cookiejar
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Callable

from utah import failures

log = logging.getLogger("utah.product.probate")

_BASE = "https://www.georgiapublicnotice.com"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_PFX = "ctl00$ContentPlaceHolder1$as1$"
_GRID = "ctl00$ContentPlaceHolder1$WSExtendedGridNP1$GridView1"
_PERPAGE = _GRID + "$ctl01$ddlPerPage"
POPULAR = {"foreclosure": "16", "probate": "23", "estate": "23",
           "public_sale": "22", "sheriff": "24", "tax_sale": "25"}

#: Coweta County + metro-Atlanta ring (Michael's market). Lower-cased.
DEFAULT_COUNTIES = (
    "coweta", "fayette", "fulton", "clayton", "carroll", "heard", "meriwether",
    "troup", "spalding", "henry", "douglas", "paulding", "pike", "lamar",
    "newton", "rockdale", "butts", "upson", "haralson", "chattooga",
)

# --- name extraction (faithful port of Ace's GPN regexes) --------------------
_NAME_TAIL = r"(?=\s*,|\s+deceased|\s+dec'd|\s+late\s+of|\s+estate\s+no|$)"
_ESTATE_NAME = re.compile(
    r"(?:in\s+re:?\s+)?estate\s+of\s+([A-Z][A-Za-z .'-]{2,55}?)" + _NAME_TAIL, re.I)
_DEBTORS_NAME = re.compile(
    r"demands?\s+against\s+(?:the\s+estate\s+of\s+)?([A-Z][A-Za-z .'-]{2,55}?)"
    + _NAME_TAIL, re.I)
_COUNTY_OF_RE = re.compile(r"\bCOUNTY\s+OF\s+([A-Za-z]+)", re.I)
_X_COUNTY_RE = re.compile(r"\b([A-Za-z]+)\s+COUNTY\b", re.I)
_NOT_A_COUNTY = frozenset({"georgia", "state", "the", "said", "this", "of", "in"})
_BAD_NAMES = frozenset({
    "the", "a certain", "said", "grantor", "grantee", "borrower", "trustee",
    "georgia", "county", "mortgage", "bank", "llc", "corp", "hereinafter",
    "security", "deed", "loan", "sheriff", "petitioner", "plaintiff",
    "ex-officio", "ex officio", "tax commissioner", "state", "city",
})
_ROLE_MARKERS = ("sheriff", "petitioner", "plaintiff", "ex-officio", "ex officio",
                 "tax commissioner", "in his capacity", "in her capacity", "solely in")
_SECTION_CODE_RE = re.compile(r"^(?:GPN|RN)\s?\d{1,2}$", re.I)
_NOTICE_NUM_RE = re.compile(r"\b(\d{6,})\b")
_FILING_RE = re.compile(r"\b((?:FN|RN|GPN)\s?\d{3,})\b", re.I)


def _clean(s: str) -> str:
    return _html.unescape(re.sub(r"\s+", " ", s or "")).strip()


def _county_from_text(text: str) -> str:
    m = _COUNTY_OF_RE.search(text)
    if m and m.group(1).lower() not in _NOT_A_COUNTY:
        return m.group(1).lower()
    for m in _X_COUNTY_RE.finditer(text):
        cand = m.group(1).lower()
        if cand not in _NOT_A_COUNTY:
            return cand
    return ""


def _extract_name(text: str, category: str = "probate") -> str:
    for pat in (_ESTATE_NAME, _DEBTORS_NAME):
        m = pat.search(text)
        if not m:
            continue
        name = _clean(m.group(1)).rstrip(".,")
        lowered = name.lower()
        if (3 <= len(name) <= 70 and lowered not in _BAD_NAMES
                and not lowered.startswith("the ") and not lowered.startswith("said ")
                and not any(mark in lowered for mark in _ROLE_MARKERS)):
            return name
    return ""


def _stable_notice_id(text: str) -> str:
    import hashlib
    if not text:
        return ""
    fn = _FILING_RE.search(text)
    if fn:
        return fn.group(1).replace(" ", "").upper()
    num = _NOTICE_NUM_RE.search(text)
    if num:
        return num.group(1)
    toks = text.split()
    while toks and (_SECTION_CODE_RE.match(toks[0]) or _FILING_RE.fullmatch(toks[0] or "")):
        toks.pop(0)
    basis = " ".join(toks).lower()[:600] or text.lower()[:600]
    return "h:" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def _parse_notices(page: str) -> list[dict]:
    """Split a GPN results page into per-notice records (info cell + prose cell)."""
    infos = re.findall(r'<td[^>]*class="info[^"]*"[^>]*>(.*?)</td>', page, re.S | re.I)
    proses = re.findall(r'<td colspan="3"[^>]*>(.*?)</td>', page, re.S | re.I)
    out: list[dict] = []
    for info, prose in zip(infos, proses):
        county = re.search(r"County:\s*([A-Za-z][A-Za-z ]+?)\s*<", info)
        text = _html.unescape(re.sub(r"<[^>]+>", " ", prose))
        text = re.sub(r"\s+", " ", text).strip()
        cty = (_clean(county.group(1)) if county else "").lower()
        if not cty:
            cty = _county_from_text(text)
        out.append({"county": cty, "notice_id": _stable_notice_id(text), "text": text})
    return out


def find_probate(pages: list[str], counties: set[str] | None = None,
                 category: str = "probate") -> list[dict]:
    """Parse result pages → ring-filtered probate findings. Pure; never fetches.
    Each: ``{case_name, county, notice_id, text}``. counties=None means no filter."""
    if counties is None:
        counties = set(DEFAULT_COUNTIES)
    seen: set[str] = set()
    out: list[dict] = []
    for page in pages:
        for n in _parse_notices(page):
            cty = n["county"] or _county_from_text(n["text"])
            if counties and cty not in counties:
                continue
            nid = n["notice_id"] or n["text"][:40]
            guid = f"gpn:{category}:{nid}"
            if guid in seen:
                continue
            seen.add(guid)
            name = _extract_name(n["text"], category)
            if not name:
                continue  # no decedent name extracted -> not an actionable lead
            out.append({"case_name": name, "county": cty or "unknown",
                        "notice_id": nid, "text": n["text"][:500]})
    return out


# --- fragile GPN fetch (instrumented) ----------------------------------------

def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA), ("Accept", "text/html"),
                     ("Accept-Encoding", "gzip")]
    return op


def _read(resp) -> str:
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw.decode("utf-8", "replace")


_INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.I)
_VALUE_ATTR_RE = re.compile(r'(?<![\w-])value="([^"]*)"')


def _hidden(page: str, name: str) -> str:
    if not page:
        return ""
    needle = re.compile(rf'(?<![\w-])(?:id|name)="{re.escape(name)}"')
    for m in _INPUT_TAG_RE.finditer(page):
        tag = m.group(0)
        if needle.search(tag):
            v = _VALUE_ATTR_RE.search(tag)
            return _html.unescape(v.group(1)) if v else ""
    return ""


def _post(op, url: str, page: str, fields: dict) -> str:
    form = {"__EVENTARGUMENT": "", "__VIEWSTATE": _hidden(page, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden(page, "__VIEWSTATEGENERATOR")}
    ev = _hidden(page, "__EVENTVALIDATION")
    if ev:
        form["__EVENTVALIDATION"] = ev
    form.update(fields)
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _read(op.open(req, timeout=45))


def _real_fetch(category: str = "probate", days: int = 60, max_pages: int = 4,
                **_) -> list[str]:
    """Live GPN: seed a session, run the popular search, bump to 50/page, and walk up
    to ``max_pages`` result pages (GPN's county checkbox doesn't bind over a raw POST,
    so it returns STATEWIDE newest-first — paginating is how ring counties surface).
    Returns the result HTML pages. Raises on hard failure (caller documents it)."""
    op = _opener()
    page, url = "", _BASE + "/"
    for attempt in range(3):
        try:
            r = op.open(_BASE + "/", timeout=30)
            page, url = _read(r), r.geturl()
        except Exception as exc:  # noqa: BLE001
            log.debug("gpn session GET attempt %d failed: %s", attempt + 1, exc)
            page = ""
        if page and _hidden(page, "__VIEWSTATE"):
            break
        time.sleep(1.5 * (attempt + 1))
    if not (page and _hidden(page, "__VIEWSTATE")):
        raise RuntimeError("no usable GPN session (no __VIEWSTATE after 3 GET attempts)")
    page = _post(op, url, page, {
        "__EVENTTARGET": _PFX + "btnGo", _PFX + "btnGo": "Search",
        _PFX + "txtSearch": "", _PFX + "ddlPopularSearches": POPULAR.get(category, "23"),
        _PFX + "dateRange": "rbLastNumDays", _PFX + "txtLastNumDays": str(days),
        _PFX + "txtLastNumWeeks": "", _PFX + "txtLastNumMonths": "",
    })
    try:  # 50 per page (non-fatal — paging still works at 25)
        page = _post(op, url, page, {"__EVENTTARGET": _PERPAGE, _PERPAGE: "50"})
    except Exception as exc:  # noqa: BLE001
        log.debug("gpn per-page bump failed (non-fatal): %s", exc)
    pages = [page]
    for pageno in range(2, max_pages + 1):  # GridView pager postback
        try:
            nxt = _post(op, url, page, {"__EVENTTARGET": _GRID,
                                        "__EVENTARGUMENT": f"Page${pageno}"})
        except Exception as exc:  # noqa: BLE001
            log.debug("gpn page %d fetch failed (stop): %s", pageno, exc)
            break
        if not _parse_notices(nxt):
            break  # ran out of notices — clean exhaustion
        pages.append(nxt)
        page = nxt
    return pages


FetchPages = Callable[..., list[str]]


def scout(ledger, category: str = "probate", counties=None, days: int = 60,
          max_pages: int = 4, fetch: FetchPages | None = None) -> dict:
    """Find probate notices and write each to the ledger (never-twice). Returns
    ``{found, new, region[, error]}``. ``counties=None`` filters to the Coweta ring;
    an empty set captures STATEWIDE (every GA notice). Every failure is recorded with a
    documented reason to the failure log (the AUDIT panel) — degrades honestly, never
    crashes."""
    cset = set(counties) if counties is not None else set(DEFAULT_COUNTIES)
    region = "Georgia (statewide)" if not cset else "Coweta metro ring, GA"
    try:
        pages = (fetch or _real_fetch)(category=category, days=days, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — fragile ASP.NET fetch
        failures.record("probate", "fetch_failed",
                        f"GPN {category} fetch failed: {exc}")
        log.warning("probate fetch failed: %s", exc)
        return {"found": 0, "new": 0, "region": region, "error": str(exc)}

    findings = find_probate(pages, counties=cset, category=category)
    if not findings:
        # Precise, documented reason: distinguish "source returned nothing / markup
        # changed" from "notices found but none in the target ring this window".
        raw = [n for p in pages for n in _parse_notices(p)]
        if not raw:
            failures.record("probate", "zero_notices",
                            f"GPN {category}: parsed {len(pages)} page(s) but 0 notices "
                            "(GridView markup changed or search returned no results)")
        else:
            from collections import Counter
            seen = Counter((n["county"] or _county_from_text(n["text"]) or "?") for n in raw)
            if cset:
                failures.record("probate", "zero_ring",
                                f"GPN {category}: {len(raw)} statewide notices across "
                                f"{dict(seen)} — 0 in the {len(cset)}-county ring this window "
                                "(raw POST can't bind the county filter)")
            else:
                failures.record("probate", "zero_named",
                                f"GPN {category}: {len(raw)} notices but 0 had an extractable "
                                f"decedent name (counties: {dict(seen)})")
        return {"found": 0, "new": 0, "region": region, "raw_notices": len(raw)}

    new = 0
    for f in findings:
        try:
            if ledger.record_probate(f["case_name"], f["county"]):
                new += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the rest
            failures.record("probate", "ledger_write_failed",
                            f"{f['case_name']} ({f['county']}): {exc}")
    log.info("probate scout %s: found=%d new=%d", region, len(findings), new)
    return {"found": len(findings), "new": new, "region": region}


#: ``com.utah.probate`` cron knobs — statewide daily catch.
DAILY_DAYS = 45
DAILY_MAX_PAGES = 12


def run_scheduled(days: int = DAILY_DAYS, max_pages: int = DAILY_MAX_PAGES,
                  ledger=None, fetch: FetchPages | None = None) -> dict:
    """``com.utah.probate`` cron entry — capture ALL Georgia probate notices, daily.

    STATEWIDE (``counties=set()`` → no filter): GPN's county checkbox doesn't bind over
    a raw POST, so the search returns the statewide newest-first stream; we walk
    ``max_pages`` deep for coverage and write EVERY actionable estate (the ledger dedups
    on case_name+county, so daily overlap never double-writes). The Coweta-ring filter
    that zeroed older runs is intentionally dropped here — the county is still stored per
    row, so the deck can narrow to the market. Real GPN only; degrades honestly."""
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    res = scout(ledger, category="probate", counties=set(), days=days,
                max_pages=max_pages, fetch=fetch)
    log.info("probate cron (statewide): %s", res)
    return res


__all__ = ["find_probate", "scout", "run_scheduled", "DEFAULT_COUNTIES",
           "_extract_name", "_county_from_text", "_parse_notices"]

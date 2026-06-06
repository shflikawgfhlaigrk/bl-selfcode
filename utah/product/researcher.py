"""Researcher capability — find on the web and remember (grounded).

Ace's researcher transitions HERE as a capability behind the brain, not an agent: search
DuckDuckGo (no API key), fetch the top results, extract DURABLE facts with the brain, and
store them in Utah memory through the admission gate. Adversary-controlled page text is
sanitized (prompt-injection defence) before it ever reaches the brain. Search/fetch are
dependency-light (urllib). Every failure — soft-block, empty results, a dead fetch, no
facts — is documented to the failure log and degrades honestly; it never fabricates.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request

from utah import UtahError, brain, failures, memory

log = logging.getLogger("utah.product.researcher")

_DDG_HTML_URL = "https://duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.5 Safari/605.1.15")
MAX_PAGE_CHARS = 10000   # nav/boilerplate eats the first few KB; give the brain real article text

#: Research extraction is DISTINCT from brain.extract_facts (which is tuned for personal/
#: project facts about Michael and returns [] on general web prose). This pulls the key
#: world-knowledge facts that answer the query, as standalone statements.
_RESEARCH_EXTRACT = (
    "From the WEB CONTENT below, extract the key factual statements that answer the "
    "QUESTION. Each fact must be a short, standalone sentence understandable on its own "
    "(include the subject — don't write 'It supports...'). Ignore navigation/menu/boilerplate. "
    "Return ONLY a JSON array of fact strings; [] if the content has no real answer. No commentary."
)
_JSON_ARRAY = re.compile(r"\[.*\]", re.S)


def extract_research_facts(content: str, query: str, ask=None) -> list[str]:
    """Brain-extract standalone world-knowledge facts answering *query* from *content*.
    Returns [] on brain failure or unparseable output (never fabricates)."""
    ask = ask or brain.ask
    try:
        raw = ask(f"{_RESEARCH_EXTRACT}\n\nQUESTION: {query}\n\nWEB CONTENT:\n{content[:MAX_PAGE_CHARS]}")
    except brain.BrainUnavailable:
        return []
    m = _JSON_ARRAY.search(raw or "")
    try:
        parsed = json.loads(m.group(0) if m else (raw or ""))
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [s.strip() for s in parsed if isinstance(s, str) and len(s.strip()) > 15][:10]

_RESULT_ANCHOR_RE = re.compile(
    r'<a[^>]*class="(?:result__a|result-link)"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.S,
)
_SOFT_BLOCK_MARKERS = ("too many requests", "if this error persists",
                       "unfortunately, bots use duckduckgo", "anomaly", "challenge")

_INJECTION_PLACEHOLDER = "[utah:redacted-injection-attempt]"
_INVISIBLE_TAG_RE = re.compile(r"[\U000E0000-\U000E007F]")
_INJECTION_PATTERNS = (
    re.compile(r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}?"
               r"\b(?:above|previous|prior|earlier|all|any|the)\b[^.\n]{0,40}?"
               r"\b(?:instructions?|prompt|rules?|directives?|system)\b", re.I),
    re.compile(r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be|"
               r"from\s+now\s+on(?:\s+you)?)\b[^.\n]{0,80}", re.I),
    re.compile(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>"
               r"|\[/?INST\]|<<SYS>>|<</SYS>>", re.I),
    re.compile(r"(?m)^\s*(?:Human|Assistant|System)\s*:\s*", re.I),
    re.compile(r"\b(?:system|developer|admin|root)\s*(?:prompt|message|instruction)\s*[:=]", re.I),
)


class SearchBlocked(UtahError):
    """Every search provider soft-blocked — an empty result that is a BLOCK, not an empty web."""


def sanitize_fetched_text(text: str) -> str:
    """Neutralize prompt-injection in scraped text before it reaches the brain. Never raises."""
    if not text:
        return ""
    cleaned = _INVISIBLE_TAG_RE.sub("", text)
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub(_INJECTION_PLACEHOLDER, cleaned)
    return cleaned


def _decode_ddg_redirect(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path == "/l/":
        params = urllib.parse.parse_qs(parsed.query)
        if params.get("uddg"):
            return params["uddg"][0]
    return href if href.startswith(("http://", "https://")) else ""


def _parse_results(body: str, k: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _RESULT_ANCHOR_RE.finditer(body):
        url = _decode_ddg_redirect(m.group(1))
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url and title:
            out.append((title, url))
        if len(out) >= k:
            break
    return out


def _html_to_text(body: str) -> str:
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", body)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def _get(url: str, params: dict | None = None, timeout: float = 20.0) -> str:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _looks_blocked(body: str) -> bool:
    low = body.lower()
    return any(m in low for m in _SOFT_BLOCK_MARKERS)


def search(query: str, k: int = 5, get=None) -> list[tuple[str, str]]:
    """DuckDuckGo → [(title, url)], html then lite fallback. Raises SearchBlocked when
    every provider soft-blocks (so an empty list never silently means a block)."""
    get = get or _get
    q = query.strip()
    if not q:
        return []
    blocked = False
    for url in (_DDG_HTML_URL, _DDG_LITE_URL):
        try:
            body = get(url, {"q": q})
        except Exception as exc:  # noqa: BLE001 — try the next provider
            log.debug("search provider %s failed: %s", url, exc)
            continue
        res = _parse_results(body, k)
        if res:
            return res
        blocked = blocked or _looks_blocked(body)
    if blocked:
        raise SearchBlocked(f"all providers soft-blocked for {q!r}")
    return []


def fetch(url: str, get=None) -> str:
    """Fetch + strip + sanitize + truncate. '' for a non-http url."""
    if not url.startswith(("http://", "https://")):
        return ""
    text = sanitize_fetched_text(_html_to_text((get or _get)(url)))
    return text[:MAX_PAGE_CHARS]


def research(query: str, *, k: int = 4, search_fn=None, fetch_fn=None,
             extract_fn=None, store_fn=None) -> dict:
    """Search → fetch → extract durable facts (grounded) → store in memory. Returns
    ``{query, sources, facts, stored}``. Every failure documented; never raises."""
    search_fn = search_fn or (lambda q, k=k: search(q, k))
    fetch_fn = fetch_fn or fetch
    # query-aware research extractor (NOT brain.extract_facts, which is for personal facts)
    extract_fn = extract_fn or (lambda content: extract_research_facts(content, query))
    store_fn = store_fn or (lambda c: memory.store(c, source="fact", confidence=0.5))

    try:
        sources = search_fn(query, k)
    except SearchBlocked as exc:
        failures.record("researcher", "search_blocked", f"{query[:60]}: {exc}")
        return {"query": query, "sources": 0, "facts": 0, "stored": 0, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        failures.record("researcher", "search_failed", f"{query[:60]}: {exc}")
        return {"query": query, "sources": 0, "facts": 0, "stored": 0, "error": str(exc)}

    if not sources:
        failures.record("researcher", "no_results", f"{query[:60]}: 0 web results")
        return {"query": query, "sources": 0, "facts": 0, "stored": 0}

    facts_found = stored = 0
    for title, url in sources:
        try:
            content = fetch_fn(url)
        except Exception as exc:  # noqa: BLE001 — one dead source must not stop the rest
            failures.record("researcher", "fetch_failed", f"{url[:70]}: {exc}")
            continue
        if not content:
            continue
        facts = extract_fn(content) or []
        facts_found += len(facts)
        for fact in facts:
            try:
                store_fn(fact)
                stored += 1
            except Exception as exc:  # noqa: BLE001 — admission/embccept failures
                failures.record("researcher", "store_failed", f"{str(fact)[:50]}: {exc}")

    if facts_found == 0:
        failures.record("researcher", "no_facts",
                        f"{query[:60]}: {len(sources)} sources, 0 durable facts extracted")
    log.info("research %r: sources=%d facts=%d stored=%d", query, len(sources), facts_found, stored)
    return {"query": query, "sources": len(sources), "facts": facts_found, "stored": stored}


__all__ = ["search", "fetch", "research", "sanitize_fetched_text", "SearchBlocked",
           "extract_research_facts"]

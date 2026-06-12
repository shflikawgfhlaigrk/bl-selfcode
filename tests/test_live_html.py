"""Structural contract tests for the Black Gold deck (live.html).

The deck is one HTML file of real JS against the web bridge — historically zero
test coverage, so a renamed endpoint, a dropped element id, or an unescaped quote
in an attribute (DOM-XSS via scraped lead names / web-learned memory facts) only
surfaced by clicking around. These tests pin the contracts: the inline script
parses, ``esc()`` is attribute-safe, no dynamic data flows into inline handlers,
every fetched endpoint is actually served by ``web.build_app()``, every DOM id the
script touches exists, and the deck stays fully local (no CDN/external resources).
"""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from utah.interface import web

SRC = web.LIVE.read_text(encoding="utf-8")


def _inline_script() -> str:
    m = re.search(r"<script>(.*)</script>", SRC, re.S)
    assert m, "live.html must carry its inline deck script"
    return m.group(1)


# --- the script is valid JS ------------------------------------------------------

def test_inline_script_parses_under_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed — JS syntax check unavailable")
    js = tmp_path / "deck.js"
    js.write_text(_inline_script(), encoding="utf-8")
    proc = subprocess.run([node, "--check", str(js)], capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, f"deck script has a JS syntax error:\n{proc.stderr}"


# --- XSS surface ------------------------------------------------------------------

def test_esc_escapes_attribute_breakers(tmp_path):
    """``esc()`` output lands inside double-quoted attributes (title=, aria-label=)
    fed with REAL scraped/learned text — it must neutralize quotes, not just <>&."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    m = re.search(r"const esc=(.*);\s*$", _inline_script(), re.M)
    assert m, "esc() helper must exist"
    probe = (f"const esc={m.group(1)};"
             """const out=esc('<i x="y">&\\'"');"""
             "if(/[<>&\"']/.test(out.replace(/&(amp|lt|gt|quot|#39);/g,'')))"
             "{console.error('unescaped:',out);process.exit(1)}")
    proc = subprocess.run([node, "-e", probe], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"esc() leaves attribute-breaking chars: {proc.stderr or proc.stdout}"


def test_no_dynamic_data_inside_inline_event_handlers():
    """HTML entities decode BEFORE inline-handler JS parses, so esc() cannot protect
    a '${...}' interpolated into onclick="fn('...')" — dynamic values must bind via
    data-attributes + addEventListener instead."""
    offenders = re.findall(r'onclick="[^"]*\$\{', _inline_script())
    assert not offenders, f"dynamic interpolation inside inline handlers: {offenders}"


def test_every_interpolation_is_escaped_or_static():
    """Every ``${...}`` that reaches an HTML string must run through esc()/known-safe
    builders. Heuristic tripwire: raw `${r.` / `${d.` / `${e.data` (API-fed objects)
    without esc() is a regression."""
    script = _inline_script()
    raw = re.findall(r"\$\{(?:r|o|g|f)\.[a-z_]+\}", script)
    allowed = {"${r.drill}"}                      # internal constant route keys only
    assert set(raw) <= allowed, f"unescaped API-fed interpolations: {sorted(set(raw) - allowed)}"


# --- endpoint contract (deck ↔ bridge) ---------------------------------------------

def _route_templates() -> list[re.Pattern]:
    pats = []
    for route in web.build_app().routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        rx = re.sub(r"\{[^}]*:path\}", ".*", path)
        rx = re.sub(r"\{[^}]+\}", "[^/]+", rx)
        pats.append(re.compile("^" + rx + "$"))
    return pats


def test_every_fetched_endpoint_is_served_by_the_bridge():
    script = _inline_script()
    fetched = set(re.findall(r'(?:fetch|EventSource)\(\s*"([^"]+)"', script))
    fetched |= set(re.findall(r'getPanel\("([^":]+)"\)', script) and set())  # panels below
    templates = _route_templates()
    explicit = [p for p in (u.split("?")[0] for u in fetched) if p]
    # the deck's catch-all serves /<domain> only for KNOWN deck-state domains
    known_domains = set(web._deck_state({"memory": {}})) | {"state", "status", "health", "ledger", "lab"}
    for path in explicit:
        if path.endswith("/"):                       # concat prefix: /panel/ , /api/selfcode/job/
            path = path + "x"
        served = any(t.match(path) for t in templates)
        assert served, f"deck fetches {path!r} but the bridge serves no such route"
        # if ONLY the catch-all matched, the data route must be a known deck domain
        non_catchall = [t for t in templates if t.pattern != "^/.*$"]
        if not any(t.match(path) for t in non_catchall):
            seg = path.strip("/").split("/")[0]
            assert seg in known_domains, f"{path!r} would 404 through the catch-all"


def test_panel_keys_fetched_match_drill_titles():
    script = _inline_script()
    panels = set(re.findall(r'getPanel\("([^"]+)"\)', script))
    # every statically-fetched panel name is a plain token the daemon can dispatch on
    assert panels, "deck must drill into real panels"
    for p in panels:
        assert re.fullmatch(r"[a-z_:]+", p), f"suspicious panel key {p!r}"


# --- DOM id contract ----------------------------------------------------------------

def test_every_dom_id_the_script_references_exists():
    script = _inline_script()
    wanted = set(re.findall(r'\$\("([\w-]+)"\)', script))
    wanted |= set(re.findall(r'getElementById\("([\w-]+)"\)', script))
    missing = {i for i in wanted if f'id="{i}"' not in SRC}
    assert not missing, f"script references DOM ids that exist nowhere: {sorted(missing)}"


def test_core_panels_present():
    for el in ("app", "spine", "fns", "ticker", "dtabs", "dbody", "stream", "q",
               "drill", "drill-body", "legend-body", "clock", "mic"):
        assert f'id="{el}"' in SRC, f"deck shell element #{el} missing"


# --- fully local & honest -------------------------------------------------------------

def test_no_external_resources():
    """Tailnet deck: no CDN scripts, styles or fonts — everything ships in the file."""
    assert not re.search(r'<script[^>]+src=', SRC), "external <script src> found"
    assert not re.search(r'<link[^>]+href="http', SRC), "external stylesheet found"
    assert "url(http" not in SRC, "external CSS asset found"


def test_no_fabricated_live_numbers_in_markup():
    """Every metric element boots as an em-dash placeholder, never a hardcoded number
    that could read as live data before the first real poll lands."""
    for mid in ("h-load", "h-mem", "h-bus", "h-gates", "t-load", "t-bus"):
        m = re.search(rf'id="{mid}"[^>]*>([^<]*)<', SRC)
        assert m and m.group(1).strip() in {"—", ""}, f"#{mid} boots with a fabricated value"


def test_every_dash_tab_has_a_plain_language_intro():
    script = _inline_script()
    tabs = set(re.findall(r'\{id:"(\w+)",\s*label:', script))
    m = re.search(r"const TAB_INTRO=\{(.*?)\};", script, re.S)
    assert m, "TAB_INTRO map must exist"
    intros = set(re.findall(r'(\w+):"', m.group(1)))
    missing = tabs - intros
    assert not missing, f"dash tabs with no plain-language intro: {sorted(missing)}"

"""SMB site generator — the FULFILLMENT machine for the $700 website product. The cold
pitch promises "I'll show you what it will look like before you buy"; this module keeps
that promise: one lead row → one complete, polished, single-file preview site in seconds.

HONESTY RULES (product-grade, same doctrine as everything else):
- No fabricated testimonials, reviews, ratings, or claims. Ever.
- Only render what the lead actually has (name, kind, phone, address); placeholders are
  clearly placeholders ("Your hours here") for the customization conversation.
- Footer credits Black Label Bots — the preview itself is the ad.

Pure render (tested) + a thin write boundary. No deps, no build step, no JS required.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote_plus

from utah import config

log = logging.getLogger("utah.product.sitegen")

#: Public preview host for outreach fulfillment (Worker route TBD).
PREVIEW_BASE = "https://previews.blacklabelbots.com"

#: kind → (headline verb phrase, one service line). Honest, generic-per-trade copy.
_KIND_COPY: dict[str, tuple[str, str]] = {
    "handyman": ("Repairs done right, the first time",
                 "Small jobs, big jobs, honest quotes — one call covers it."),
    "general_contractor": ("Built to last. Managed end-to-end",
                           "From site prep to final walkthrough, one accountable team."),
    "plumber": ("Fast, clean plumbing work",
                "Leaks, installs, and emergencies — straight pricing, tidy work."),
    "electrician": ("Safe, code-clean electrical work",
                    "Panels, fixtures, troubleshooting — licensed and careful."),
    "landscaper": ("Outdoor spaces worth coming home to",
                   "Design, install, and maintenance on your schedule."),
    "restaurant": ("Good food, close to home",
                   "See the menu, find us, and call ahead — all in one place."),
    "salon": ("Look your best, book in seconds",
              "Cuts, color, and care — call or stop by."),
    "auto_repair": ("Honest wrenches. Clear estimates",
                    "Diagnostics to full repairs — we explain before we fix."),
}
_DEFAULT_COPY = ("Quality local service, one call away",
                 "Honest quotes, careful work, and a name your neighbors know.")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "site"


def _clean_region(region: str) -> str:
    """'Maps Augusta GA [handyman]' → 'Augusta GA' (lead regions carry scout tags)."""
    return re.sub(r"\[[^\]]*\]", "", (region or "")).replace("Maps", "").strip()


def render(lead: dict, *, checkout_url: str | None = None) -> str:
    """Lead row → complete single-file HTML site. Pure; deterministic; no fabrication.

    Every lead-supplied value (name, region, address, phone) is HTML-escaped before it
    reaches the markup. Real lead names routinely carry ``&`` ("Mom & Pop's"), quotes
    ('Joe "Big Tony" Pizza'), and angle brackets — unescaped they would break the meta
    tags, mangle the heading, or inject markup into a customer-facing preview. URLs use
    the RAW value through ``quote_plus``/digit-stripping, which is their correct encoder.

    *checkout_url* (a Stripe Payment Link) turns the preview into a real sales page: when
    given, a "Buy this site — $700" button is rendered; when absent, the page falls back to
    the contact CTA only (no fake button). Pure — the caller (``generate``) reads config.
    """
    name = (lead.get("name") or "Your Business").strip()
    kind = (lead.get("kind") or "").strip().lower()
    contact = lead.get("contact") or {}
    phone = (contact.get("phone") or "").strip()
    address = (contact.get("address") or "").strip()
    region = _clean_region(lead.get("region") or "")
    head, sub = _KIND_COPY.get(kind, _DEFAULT_COPY)
    tel = re.sub(r"[^+\d]", "", phone)          # URL context — raw, digit-only
    place = address or (f"{name} {region}".strip())
    maps_q = quote_plus(place)                   # URL context — percent-encoded
    kind_label = kind.replace("_", " ").title() if kind else "Local Business"

    # HTML contexts — collapse scraper whitespace (stray \n/\t) to single spaces, then
    # escape &, <, >, and " so values are safe in text AND double-quoted attributes (the
    # only attribute style this template uses). The apostrophe is left intact on purpose:
    # it is harmless in double-quoted attributes and "Joe's Diner" should read naturally.
    def _h(s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    e_name = _h(name)
    e_region = _h(region)
    e_address = _h(address)
    e_phone = _h(phone)
    e_kind_label = _h(kind_label)
    name, region, address, kind_label = e_name, e_region, e_address, e_kind_label

    call_btn = (f'<a class="btn" href="tel:{tel}">Call {e_phone}</a>' if tel else
                '<a class="btn" href="#contact">Get in touch</a>')
    # The deliverable IS the sales page: a real checkout link turns "preview" into "buy".
    # No link configured -> no button (honest), the page still drives to contact/call.
    e_checkout = _h(checkout_url) if checkout_url else ""
    buy_btn = (f'<a class="btn buy" href="{e_checkout}">Buy this site — $700</a>'
               if e_checkout else "")
    map_block = (f'<iframe title="map" loading="lazy" '
                 f'src="https://maps.google.com/maps?q={maps_q}&output=embed"></iframe>'
                 if place else "")
    addr_line = f"<p>{e_address}</p>" if address else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — {kind_label}{(' · ' + region) if region else ''}</title>
<meta name="description" content="{name}: {sub}">
<style>
:root{{--ink:#15181d;--paper:#fafaf7;--accent:#1f5f43;--soft:#e8e6df}}
*{{box-sizing:border-box;margin:0}}
body{{font:17px/1.6 -apple-system,'Segoe UI',Roboto,sans-serif;color:var(--ink);background:var(--paper)}}
header{{background:linear-gradient(160deg,var(--ink),#2a313b);color:#fff;padding:72px 24px 64px;text-align:center}}
header .kicker{{letter-spacing:.18em;text-transform:uppercase;font-size:13px;opacity:.75}}
h1{{font-size:clamp(30px,6vw,52px);margin:10px 0 8px}}
header p{{max-width:560px;margin:0 auto 28px;opacity:.9}}
.btn{{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;
padding:14px 30px;border-radius:8px;font-weight:600;margin:6px 4px}}
.btn.buy{{background:#c0492b}}
section{{max-width:880px;margin:0 auto;padding:48px 24px}}
h2{{font-size:24px;margin-bottom:12px}}
.grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:18px}}
.card{{background:#fff;border:1px solid var(--soft);border-radius:10px;padding:20px}}
iframe{{width:100%;height:320px;border:0;border-radius:10px;margin-top:14px}}
.placeholder{{color:#8a8678;font-style:italic}}
footer{{text-align:center;padding:28px;color:#777;font-size:14px;border-top:1px solid var(--soft)}}
footer a{{color:var(--accent)}}
</style></head><body>
<header>
  <div class="kicker">{kind_label}{(' · ' + region) if region else ''}</div>
  <h1>{name}</h1>
  <p>{head}. {sub}</p>
  {call_btn}
  {buy_btn}
</header>
<section>
  <h2>What we do</h2>
  <div class="grid">
    <div class="card"><strong>Our work</strong><p class="placeholder">Photos of your real
    jobs go here — send 3–5 favorites.</p></div>
    <div class="card"><strong>Hours</strong><p class="placeholder">Your hours here —
    tell us and we'll set them.</p></div>
    <div class="card"><strong>Service area</strong><p>{region or '<span class="placeholder">Your service area here.</span>'}</p></div>
  </div>
</section>
<section id="contact">
  <h2>Find us</h2>
  {addr_line}
  {map_block}
  <p style="margin-top:18px">{call_btn} {buy_btn}</p>
</section>
<footer>Site preview by <a href="https://blacklabelbots.com">Black Label Bots</a> —
built same-day, $700 flat, you approve the design before you pay.</footer>
</body></html>
"""


def generate(lead: dict, *, out_dir: str | Path | None = None, ledger=None) -> dict:
    """Render + write ``<slug>.html``, and (when *ledger* is given) record the artifact
    through the REAL ledger path so the deck's revenue panel lights up.

    Returns path + size; honest-empty on a nameless lead (nothing to build a site for).

    Recording is IDEMPOTENT: the preview is merged onto the lead's own row
    (``ledger.update_lead`` → ``contact.site`` jsonb merge, keyed UNIQUE(name, region)),
    so re-generating the same lead never creates a second artifact row — it merges the
    same ``site`` payload and the lead carries exactly one live preview. The ledger's
    own ``_emit`` fans the merge out to the bus/deck on the ``leads`` channel, so no
    separate publish is needed here (that is the "optional bus publish", handled by the
    real write path). A missing ``region`` means the lead was never persisted, so there
    is nothing to merge onto — we still write the file and report ``recorded: False``
    honestly rather than fabricating a row."""
    if not (lead.get("name") or "").strip():
        return {"written": False, "reason": "lead has no business name"}
    checkout = config.checkout_url()            # real Stripe link -> a live "Buy" button
    html = render(lead, checkout_url=checkout)
    d = Path(out_dir) if out_dir else Path.home() / "Desktop" / "site-previews"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug(lead['name'])}.html"
    path.write_text(html, encoding="utf-8")
    lead_slug = slug(lead["name"])
    preview_url = f"{PREVIEW_BASE}/{lead_slug}"
    log.info("sitegen: %s -> %s (%d bytes) preview=%s", lead.get("name"), path, len(html), preview_url)
    out = {
        "written": True,
        "path": str(path),
        "bytes": len(html),
        "slug": lead_slug,
        "preview_url": preview_url,
        "preview_published": False,
        "checkout_ready": bool(checkout),       # honest: False until a Stripe link is set
        "recorded": False,
    }
    out["recorded"] = _record_site(ledger, lead, out) if ledger is not None else False
    return out


def _record_site(ledger, lead: dict, result: dict) -> bool:
    """Merge the generated-site artifact onto the lead row via the ledger's idempotent
    ``update_lead`` (UNIQUE(name, region)). Returns True iff a row was updated. A failed
    record must NEVER lose the rendered file — the artifact is already on disk — so any
    ledger/store error degrades to ``False`` (honest "not recorded"), never a raise."""
    name = (lead.get("name") or "").strip()
    region = (lead.get("region") or "").strip()
    if not name or not region:
        return False
    site = {"slug": result["slug"], "preview_url": result["preview_url"],
            "bytes": result["bytes"], "published": False}
    try:
        return bool(ledger.update_lead(name, region, contact={"site": site}))
    except Exception as exc:  # noqa: BLE001 — recording is observability, not the artifact
        log.warning("sitegen record failed for %r: %s", name, exc)
        return False


__all__ = ["render", "generate", "slug"]

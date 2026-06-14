"""enrich → sitegen → ledger wiring (utah/product/enrich.py + sitegen.py).

The fulfilment promise — "I'll show you what it will look like before you buy" — is the
sitegen artifact, so a freshly-enriched lead must arrive at outreach already carrying a
real preview recorded through the REAL ledger path. These tests drive a fake lead through
``enrich.enrich_and_generate`` with an injected fake ledger + bus (no Postgres, no network)
and prove:

  * the email enrichment merges onto the lead row AND is reflected in the rendered site;
  * the site renders ONLY fields the lead actually has — missing fields stay clearly-marked
    placeholders, never fabricated content;
  * boilerplate / placeholder email domains stay filtered (enrich never writes one);
  * recording the artifact is IDEMPOTENT — generating twice leaves ONE site on the lead row
    (UNIQUE(name, region) merge), and each merge publishes to the bus exactly as the real
    ``_emit`` path would.
"""
from __future__ import annotations

from utah.product import enrich, sitegen


# --- a fake ledger that mirrors the real update_lead semantics + bus publish ---

class FakeBus:
    """Records every event the ledger fans out (the real ``_emit`` → bus.publish path)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, channel: str, event: dict) -> int:
        self.events.append((channel, dict(event)))
        return 1


class FakeLedger:
    """In-memory mirror of the real ``Ledger.update_lead`` contract: an idempotent
    jsonb merge onto a row keyed UNIQUE(name, region). A merge of an EXISTING key only
    overwrites that key (deep-merging the nested ``site`` bag the way ``contact || jsonb``
    behaves for our usage), returns True iff a row matched, and — exactly like the real
    ledger — publishes the enrichment to the bus. Unknown rows return False (no fabrication)."""

    def __init__(self, rows: dict[tuple[str, str], dict], bus: FakeBus | None = None) -> None:
        # rows: {(name, region): contact_dict}
        self.rows = rows
        self.bus = bus
        self.update_calls: list[tuple[str, str, dict]] = []

    def update_lead(self, name, region, *, contact=None) -> bool:
        self.update_calls.append((name, region, contact))
        if not contact:
            return False
        key = (name, region)
        if key not in self.rows:
            return False                      # no row to merge onto — honest False
        merged = dict(self.rows[key])
        for k, v in contact.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                inner = dict(merged[k]); inner.update(v); merged[k] = inner
            else:
                merged[k] = v
        self.rows[key] = merged
        if self.bus is not None:              # the real ledger's _emit → bus.publish
            self.bus.publish("leads", {"name": name, "region": region, "enriched": True})
        return True


def _lead(**over) -> dict:
    base = {"name": "Joe's Diner", "kind": "restaurant", "region": "Newnan GA",
            "contact": {"phone": "+17705551234"}}
    base.update(over)
    return base


# --- the happy path: enrich fills the email, sitegen renders + records it ----

def test_enrich_to_sitegen_records_real_artifact_through_ledger(tmp_path):
    bus = FakeBus()
    lead = _lead()
    ledger = FakeLedger({("Joe's Diner", "Newnan GA"): dict(lead["contact"])}, bus=bus)

    # enrich finds a real, on-entity, mail-accepting email (injected — no network)
    def fake_find(l, **kw):
        return {"email": "info@joesdiner.com", "source_url": "https://joesdiner.com"}

    res = enrich.enrich_and_generate(ledger, lead, find_fn=fake_find, out_dir=tmp_path)

    # email enriched + merged onto the row
    assert res["email"] == "info@joesdiner.com"
    row = ledger.rows[("Joe's Diner", "Newnan GA")]
    assert row["email"] == "info@joesdiner.com"

    # the artifact was written AND recorded through the real ledger path
    site = res["site"]
    assert site["written"] is True
    assert site["recorded"] is True
    assert (tmp_path / "joe-s-diner.html").exists()

    # the site is recorded idempotently on the lead row (contact.site jsonb merge)
    assert row["site"]["preview_url"].endswith("/joe-s-diner")
    assert row["site"]["published"] is False

    # the merge published to the bus exactly as the real _emit path would
    assert ("leads", {"name": "Joe's Diner", "region": "Newnan GA",
                      "enriched": True}) in bus.events


# --- sitegen renders ONLY real fields; missing fields stay placeholders -------

def test_site_renders_real_fields_only_missing_stay_placeholder(tmp_path):
    bus = FakeBus()
    # lead has a phone + address but NO website and NO hours
    lead = _lead(contact={"phone": "+17705551234",
                          "address": "12 Main St, Newnan, GA 30263"})
    ledger = FakeLedger({("Joe's Diner", "Newnan GA"): dict(lead["contact"])}, bus=bus)

    def fake_find(l, **kw):
        return {"email": "info@joesdiner.com", "source_url": "https://joesdiner.com"}

    res = enrich.enrich_and_generate(ledger, lead, find_fn=fake_find, out_dir=tmp_path)
    html = (tmp_path / "joe-s-diner.html").read_text(encoding="utf-8")

    # REAL fields the lead actually has are rendered verbatim
    assert "Joe's Diner" in html
    assert "12 Main St, Newnan, GA 30263" in html      # real address
    assert "tel:+17705551234" in html                   # real phone → call button
    assert "Newnan GA" in html                          # real service area
    # honest per-kind copy (generic, not fabricated specifics)
    assert "Good food, close to home" in html           # restaurant kind copy

    # fields the lead does NOT have stay clearly-marked placeholders (no fabrication)
    assert 'class="placeholder">Your hours here' in html
    assert "Photos of your real" in html                # photos placeholder, not invented
    # never fabricates a testimonial / rating / fake hours
    low = html.lower()
    for invented in ("★", "5-star", "5 star", "reviews say", "open mon"):
        assert invented not in low

    assert res["site"]["recorded"] is True


def test_site_with_no_region_renders_placeholder_service_area(tmp_path):
    # a region-less lead can't be recorded (never persisted) but still renders honestly:
    # the service-area card falls back to a clearly-marked placeholder, not a guess.
    lead = {"name": "Ray's Nursery", "kind": "landscaper", "region": "",
            "contact": {"phone": "+17705559999"}}
    ledger = FakeLedger({}, bus=FakeBus())
    res = enrich.enrich_and_generate(ledger, lead, find_fn=lambda l, **k: {"email": None},
                                     out_dir=tmp_path)
    html = (tmp_path / "ray-s-nursery.html").read_text(encoding="utf-8")
    assert 'class="placeholder">Your service area here.' in html
    # no row to record onto → honest "not recorded", artifact still written
    assert res["site"]["written"] is True
    assert res["site"]["recorded"] is False


# --- boilerplate / placeholder email domains stay filtered --------------------

def test_boilerplate_domains_filtered_out_of_enrichment(tmp_path):
    """A page whose only emails are vendor/placeholder boilerplate yields NO email, so the
    ledger row is never poisoned with junk and sitegen still renders honestly (no email)."""
    bus = FakeBus()
    lead = _lead(contact={"website": "https://joesdiner.com"})
    ledger = FakeLedger({("Joe's Diner", "Newnan GA"): dict(lead["contact"])}, bus=bus)

    # the scraped page carries ONLY boilerplate: wix vendor, sentry, example.com, role inbox
    page = ("support@wixpress.com sentry@sentry.io test@example.com "
            "noreply@joesdiner.com you@yourdomain.com")
    res = enrich.enrich_and_generate(
        ledger, lead,
        find_fn=lambda l, **k: {"email": enrich.best_email(enrich.extract_emails(page))},
        out_dir=tmp_path,
    )

    # nothing survived the junk/boilerplate filter → no email written
    assert res["email"] is None
    assert "email" not in ledger.rows[("Joe's Diner", "Newnan GA")]
    # the demo site is still produced + recorded (the artifact, just without an email)
    assert res["site"]["recorded"] is True


def test_extract_emails_drops_boilerplate_keeps_real():
    # direct proof the filter is robust: only the genuine business address survives
    html = ("info@joesdiner.com privacy@joesdiner.com user@wix.com hi@squarespace.com "
            "team@example.org webmaster@joesdiner.com a@godaddy.com")
    assert enrich.extract_emails(html) == ["info@joesdiner.com"]


# --- recording the artifact is idempotent ------------------------------------

def test_record_artifact_is_idempotent(tmp_path):
    """Generating the same lead twice leaves exactly ONE site on the row (UNIQUE(name,
    region) merge), and the second pass is still a real recorded write — never a dupe row."""
    bus = FakeBus()
    lead = _lead()
    ledger = FakeLedger({("Joe's Diner", "Newnan GA"): dict(lead["contact"])}, bus=bus)
    find = lambda l, **k: {"email": "info@joesdiner.com"}

    first = enrich.enrich_and_generate(ledger, lead, find_fn=find, out_dir=tmp_path)
    second = enrich.enrich_and_generate(ledger, lead, find_fn=find, out_dir=tmp_path)

    assert first["site"]["recorded"] is True
    assert second["site"]["recorded"] is True

    # the row holds ONE site bag (merge, not append) with a stable slug across runs
    row = ledger.rows[("Joe's Diner", "Newnan GA")]
    assert row["site"]["slug"] == "joe-s-diner"
    assert first["site"]["slug"] == second["site"]["slug"]

    # exactly one html file on disk (same slug overwritten, never duplicated)
    htmls = sorted(tmp_path.glob("*.html"))
    assert htmls == [tmp_path / "joe-s-diner.html"]


def test_generate_records_directly_through_injected_ledger(tmp_path):
    """sitegen.generate itself records through the ledger when one is passed — the write
    boundary is the real path, independent of the enrich wrapper."""
    bus = FakeBus()
    lead = _lead()
    ledger = FakeLedger({("Joe's Diner", "Newnan GA"): dict(lead["contact"])}, bus=bus)
    out = sitegen.generate(lead, out_dir=tmp_path, ledger=ledger)
    assert out["recorded"] is True
    assert ledger.rows[("Joe's Diner", "Newnan GA")]["site"]["slug"] == "joe-s-diner"
    # no ledger passed → pure render, never touches a store
    out2 = sitegen.generate(lead, out_dir=tmp_path)
    assert out2["recorded"] is False


def test_record_degrades_honestly_when_ledger_raises(tmp_path):
    """A dead ledger must never lose the rendered file — recording degrades to False."""
    class Boom:
        def update_lead(self, *a, **k):
            raise RuntimeError("pg down")

    out = sitegen.generate(_lead(), out_dir=tmp_path, ledger=Boom())
    assert out["written"] is True            # artifact survived
    assert out["recorded"] is False          # honest "not recorded", no raise
    assert (tmp_path / "joe-s-diner.html").exists()

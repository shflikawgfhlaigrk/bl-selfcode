"""Probate capability — Ace's realestate/GPN producer transitions here (NOT an agent):
scrape Georgia Public Notice for estate/probate notices in the Coweta metro ring, extract
the decedent name + county, write to the product ledger. GPN is a fragile ASP.NET scrape,
so EVERY failure path records a documented reason to the failure log (the goal: track
failures, document why). Pure parse/extract is tested on real notice text; the fetch is
injectable so these run offline."""
from __future__ import annotations

from utah import failures
from utah.product import probate
from tests.fakes import FakeFailureStore


def test_extract_estate_name_full_multiword():
    assert probate._extract_name("In re: Estate of FRANCES SKINNER REEVES, deceased",
                                 "probate") == "FRANCES SKINNER REEVES"


def test_extract_name_debtors_phrasing():
    t = "NOTICE TO DEBTORS AND CREDITORS: all persons having demands against DOROTHY D. KOCHER, late of Coweta County"
    assert probate._extract_name(t, "probate") == "DOROTHY D. KOCHER"


def test_extract_name_rejects_court_roles():
    assert probate._extract_name("petition of the Sheriff of Fulton County", "probate") == ""


def test_county_of_phrasing_beats_state_word():
    assert probate._county_from_text("STATE OF GEORGIA COUNTY OF EFFINGHAM") == "effingham"
    assert probate._county_from_text("...GLYNN COUNTY WHEREAS...") == "glynn"
    assert probate._county_from_text("estate notice with no locale stated") == ""


_PAGE = (
    '<td class="info x">County: Coweta <br/></td>'
    '<td colspan="3">In re: Estate of JOHN A LAMBERT, deceased. Estate No 103039. '
    "Notice to debtors and creditors.</td>"
    '<td class="info x">County: Fulton <br/></td>'
    '<td colspan="3">Estate of MARY ELLEN SMITH, deceased, late of Fulton County.</td>'
    '<td class="info x">County: Cobb <br/></td>'   # Cobb is OUTSIDE the ring -> filtered
    '<td colspan="3">Estate of NOBODY HERE, deceased.</td>'
)


def test_find_probate_parses_filters_ring_and_extracts():
    found = probate.find_probate([_PAGE], counties=set(probate.DEFAULT_COUNTIES))
    counties = {f["county"] for f in found}
    assert "coweta" in counties and "fulton" in counties
    assert "cobb" not in counties                         # outside the ring -> dropped
    names = {f["case_name"] for f in found}
    assert "JOHN A LAMBERT" in names and "MARY ELLEN SMITH" in names


class _RecLedger:
    def __init__(self):
        self.calls, self._seen = [], set()

    def record_probate(self, case_name, county, **kw):
        key = (case_name, county)
        if key in self._seen:
            return False
        self._seen.add(key)
        self.calls.append((case_name, county))
        return True


def test_scout_records_probate_and_dedups():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    r = probate.scout(lg, fetch=lambda **k: [_PAGE])
    assert r["new"] == 2 and r["found"] == 2
    assert probate.scout(lg, fetch=lambda **k: [_PAGE])["new"] == 0   # never-twice


def test_fetch_failure_is_documented_not_crashed():
    store = FakeFailureStore()
    failures.set_store(store)
    lg = _RecLedger()
    def boom(**k):
        raise RuntimeError("GPN session GET timed out")
    r = probate.scout(lg, fetch=boom)
    assert r["found"] == 0 and "error" in r              # honest, no crash
    kinds = [row[2] for row in store.rows]               # (seq, source, kind, detail)
    assert any("fetch" in k for k in kinds)              # documented WHY in the log
    assert any("GPN session GET timed out" in row[3] for row in store.rows)


def test_zero_findings_is_documented():
    store = FakeFailureStore()
    failures.set_store(store)
    lg = _RecLedger()
    r = probate.scout(lg, fetch=lambda **k: ["<html>no notices</html>"])
    assert r["found"] == 0
    assert any("zero" in row[2] for row in store.rows)   # 0-notices reason recorded


# --- VOLUME — capture toward 200/day across multiple notice categories --------

def test_daily_max_pages_raised_for_deeper_capture():
    """Live-measured 2026-06-11: GPN returns ~10 notices/page newest-first; 12 pages
    yielded 81 named, 25 pages yielded 153 named in one run. Walk deeper for volume."""
    assert probate.DAILY_MAX_PAGES >= 25


def test_run_scheduled_default_category_is_probate_only():
    """LIVE-MEASURED 2026-06-11: only GPN code 23 (probate) carries extractable decedent
    names — the other popular codes return raw notices but ~0 named estates. So the honest
    default sweeps probate ONLY; the volume lever is DEPTH (DAILY_MAX_PAGES), not a fan-out
    over unproductive categories that would just burn fetches for nothing."""
    assert probate.DAILY_CATEGORIES == ("probate",)
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    seen_cats = []

    def fake_fetch(**k):
        seen_cats.append(k.get("category"))
        return ['<td class="info x">County: Coweta <br/></td>'
                '<td colspan="3">Estate of JANE DOE, deceased, late of Coweta County.</td>']

    res = probate.run_scheduled(ledger=lg, fetch=fake_fetch)
    assert seen_cats == ["probate"]                  # one productive stream, no waste
    assert res["new"] == 1 and res["found"] == 1


def test_run_scheduled_sweep_loop_accumulates_and_dedups_across_categories():
    """The sweep LOOP must stay multi-category-capable (adding a productive GPN code is a
    one-line edit). Given two categories with distinct estates it accumulates; given the
    SAME estate under both it dedups to one (the ledger never double-writes)."""
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()

    names = {"probate": "ALICE PROBATE", "public_sale": "BORIS SALE"}

    def distinct(**k):
        nm = names[k.get("category")]
        return ['<td class="info x">County: Coweta <br/></td>'
                f'<td colspan="3">Estate of {nm}, deceased, late of Coweta County.</td>']
    res = probate.run_scheduled(ledger=lg, fetch=distinct,
                                categories=("probate", "public_sale"))
    assert res["found"] == 2 and res["new"] == 2     # distinct estates accumulate

    lg2 = _RecLedger()
    same = ('<td class="info x">County: Coweta <br/></td>'
            '<td colspan="3">Estate of SAME PERSON, deceased, late of Coweta County.</td>')
    res2 = probate.run_scheduled(ledger=lg2, fetch=lambda **k: [same],
                                 categories=("probate", "public_sale"))
    assert res2["new"] == 1                           # one decedent, swept twice -> still 1

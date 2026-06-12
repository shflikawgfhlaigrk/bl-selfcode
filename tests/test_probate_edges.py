"""Probate edges — notice-id stability (the dedup key), hidden-field extraction (the
fragile ASP.NET session plumbing), county extraction traps, the per-row ledger guard,
and the documented zero-ring / error paths of the cron.

The notice id is what makes 'never-twice' real across daily overlapping windows; if it
drifts between runs the same estate is double-written, so its invariants are locked here.
"""
from __future__ import annotations

import pytest

from utah import failures
from utah.product import probate
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


# --- _stable_notice_id: the dedup key ----------------------------------------------------

def test_notice_id_prefers_the_filing_reference():
    text = "GPN 11 Estate of Jane Doe, deceased. Filing GPN 12345 notice to creditors."
    assert probate._stable_notice_id(text) == "GPN12345"


def test_notice_id_falls_back_to_long_notice_number():
    assert probate._stable_notice_id("Estate of X, deceased. Estate No 103039.") == "103039"


def test_notice_id_hash_is_stable_across_section_code_prefixes():
    """The SAME prose under different section codes (GPN1 vs RN2 — how GPN re-files a
    notice) must hash to the SAME id, or daily overlap double-writes the estate."""
    a = probate._stable_notice_id("GPN1 Estate of Bob Jones, deceased. No creditors yet.")
    b = probate._stable_notice_id("RN2 Estate of Bob Jones, deceased. No creditors yet.")
    assert a == b and a.startswith("h:")


def test_notice_id_empty_text_is_empty():
    assert probate._stable_notice_id("") == ""


# --- _hidden: ASP.NET form-state extraction ------------------------------------------------

def test_hidden_extracts_value_by_name_and_unescapes():
    page = ('<form><input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" '
            'value="abc&quot;d" /><input name="other" value="zzz"/></form>')
    assert probate._hidden(page, "__VIEWSTATE") == 'abc"d'
    assert probate._hidden(page, "other") == "zzz"


def test_hidden_missing_field_or_empty_page_is_empty_string():
    assert probate._hidden("<html></html>", "__VIEWSTATE") == ""
    assert probate._hidden("", "__VIEWSTATE") == ""
    # value attribute absent entirely -> "" not a crash
    assert probate._hidden('<input name="__VIEWSTATE">', "__VIEWSTATE") == ""


# --- county extraction traps ----------------------------------------------------------------

def test_county_never_falls_for_state_or_filler_words():
    assert probate._county_from_text("STATE OF GEORGIA, GEORGIA COUNTY RECORDS") == ""
    assert probate._county_from_text("SAID COUNTY AND STATE") == ""


# --- find_probate: statewide capture + cross-page dedup -------------------------------------

_RING_PAGE = (
    '<td class="info x">County: Coweta <br/></td>'
    '<td colspan="3">In re: Estate of JOHN A LAMBERT, deceased. Estate No 103039.</td>'
)
_FAR_PAGE = (
    '<td class="info x">County: Glynn <br/></td>'
    '<td colspan="3">Estate of COASTAL PERSON, deceased. Estate No 999333.</td>'
)


def test_statewide_capture_keeps_every_county_and_dedups_across_pages():
    found = probate.find_probate([_RING_PAGE, _FAR_PAGE, _RING_PAGE], counties=set())
    assert {f["county"] for f in found} == {"coweta", "glynn"}
    assert len(found) == 2                         # duplicate page never double-counts


# --- scout: per-row ledger guard + zero-ring documentation ----------------------------------

def test_scout_ledger_write_failure_is_documented_per_row(_store):
    class _BoomLedger:
        def record_probate(self, case_name, county, **kw):
            raise RuntimeError("probate table missing")

    r = probate.scout(_BoomLedger(), fetch=lambda **k: [_RING_PAGE])
    assert r["found"] == 1 and r["new"] == 0       # honest: found but NOT recorded
    assert any(row[2] == "ledger_write_failed" for row in _store.rows)


def test_scout_zero_ring_distinguished_from_zero_notices(_store):
    """Statewide notices exist but none in the target ring: the documented reason names
    the ring miss (county filter can't bind), not a parse failure."""
    class _Lg:
        def record_probate(self, *a, **k):
            return True

    r = probate.scout(_Lg(), counties={"coweta"}, fetch=lambda **k: [_FAR_PAGE])
    assert r["found"] == 0 and r["raw_notices"] == 1
    assert any(row[2] == "zero_ring" for row in _store.rows)


# --- run_scheduled: error surfacing ----------------------------------------------------------

def test_run_scheduled_surfaces_error_only_when_nothing_was_found(_store):
    class _Lg:
        def record_probate(self, *a, **k):
            return True

    def boom(**k):
        raise RuntimeError("GPN down")

    out = probate.run_scheduled(ledger=_Lg(), fetch=boom, categories=("probate",))
    assert out["found"] == 0 and out["new"] == 0
    assert "GPN down" in out["error"]
    assert out["categories"]["probate"] == {"found": 0, "new": 0}

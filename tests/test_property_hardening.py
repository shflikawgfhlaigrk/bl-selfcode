"""Property hardening — the ledger-walking crons carry BOUNDED DB access
(connect_timeout + statement_timeout), the ArcGIS WHERE clause is injection-safe
(a hostile case name can never splice predicates or LIKE wildcards into the county
query), and bad geographic points gate honestly instead of crashing.

DB-boundary tests stub psycopg.connect (the driver dependency, never the module
under test) to capture connection kwargs — same convention as test_trading_hardening."""
from __future__ import annotations

import math
import re

import pytest

from utah import failures
from utah.product import property as prop
from tests.fakes import FakeFailureStore


# --- bounded DB access on the cron entries -----------------------------------

class _FakeCursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **kw):
        return _FakeCursor()


class _NoLedger:
    """Sentinel ledger so the cron never builds a real Ledger() in a unit test."""

    def update_probate(self, *a, **kw):
        raise AssertionError("no rows -> update_probate must never be called")


@pytest.mark.parametrize("entry", ["enrich_ledger", "backfill_area_avg",
                                   "backfill_ownership_debt"])
def test_cron_db_reads_are_bounded(monkeypatch, entry):
    """Every probate-walking cron must carry connect_timeout + statement_timeout —
    an unbounded psycopg.connect on a stalled Postgres hung the enrich cron forever."""
    import psycopg

    seen = {}

    def fake_connect(dsn, **kw):
        seen.update(kw)
        return _FakeConn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    out = getattr(prop, entry)(limit=1, ledger=_NoLedger())
    assert out["scanned"] == 0
    assert seen.get("connect_timeout"), f"no connect_timeout on {entry}"
    assert "statement_timeout" in seen.get("options", ""), f"no statement_timeout on {entry}"


# --- WHERE-clause injection safety --------------------------------------------

def test_resolve_property_where_clause_is_injection_safe():
    """The ArcGIS WHERE string is concatenated, not parameterized — owner-name tokens
    must be reduced to bare alphanumerics so quotes/comments/wildcards never reach the
    county server (CWE-89-adjacent)."""
    seen = {}

    def fetch(url, where):
        seen["where"] = where
        return {"features": []}

    prop.resolve_property("ROBERT'; DROP TABLE x;--%_ O'NEIL", "testco", fetch=fetch)
    w = seen["where"]
    assert ";" not in w and "--" not in w
    # every LIKE pattern is exactly '%<ALNUM>%' — no spliced wildcards or quotes
    for pat in re.findall(r"LIKE '([^']*)'", w):
        assert re.fullmatch(r"%[A-Z0-9]+%", pat), f"unsafe LIKE pattern {pat!r}"


def test_resolve_property_unusable_name_gates_never_matches_everything():
    """A name that sanitizes to nothing must GATE — a bare LIKE '%%' would match the
    whole county (a fabricated 'resolution' to the first parcel in the table)."""
    failures.set_store(FakeFailureStore())
    called = []

    def fetch(url, where):
        called.append(where)
        return {"features": [{"attributes": {"OWNER": "WRONG PERSON"}}]}

    r = prop.resolve_property("';--%", "testco", fetch=fetch)
    assert called == []                       # never queried
    assert r["available"] is False and r.get("address") is None


# --- bad geographic points gate honestly --------------------------------------

def test_area_value_avg_invalid_point_gates_never_raises():
    failures.set_store(FakeFailureStore())
    out = prop.area_value_avg("harris", "not-a-lat", -84.87,
                              fetch=lambda u, p: {"features": []})
    assert out["available"] is False and out["gated"] is True
    out2 = prop.area_value_avg("harris", math.nan, -84.87,
                               fetch=lambda u, p: {"features": []})
    assert out2["available"] is False


def test_radius_check_invalid_point_is_honest_empty():
    failures.set_store(FakeFailureStore())
    out = prop.radius_check(None, None, places_fetch=lambda *a, **k: {"places": []},
                            smb_fetch=lambda *a, **k: [])
    assert out["places"] == [] and out["no_website_smbs"] == []
    assert out.get("error")                   # the bad point is documented, not silent

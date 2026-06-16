"""Persistent contact DB — the owned, deduped asset that makes us Apollo, not a renter.

Discovered + verified contacts persist here and dedupe on (name, domain), so the database
compounds into an asset we own. Mirrors the proven leads-ledger ON CONFLICT pattern; tested
offline through an injected connection factory (no live Postgres needed for the logic).
"""
from __future__ import annotations

from utah.product import contacts


class _FakeConn:
    """Minimal psycopg-conn stand-in: context manager + execute().fetchone(), with real
    (name, domain) dedup so ON CONFLICT DO NOTHING behaviour is exercised."""
    def __init__(self, seen: set):
        self.seen = seen
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "INSERT INTO contacts" in sql:
            key = (params[0], params[3])             # (name, domain)
            if key in self.seen:
                self._row = None
            else:
                self.seen.add(key)
                self._row = (len(self.seen),)
        else:
            self._row = None
        return self

    def fetchone(self):
        return self._row


def _store():
    seen: set = set()
    return contacts.ContactStore(conn_factory=lambda: _FakeConn(seen))


def test_record_contact_is_new_once_then_deduped():
    s = _store()
    c = {"name": "John Doe", "domain": "acme.com", "email": "john.doe@acme.com",
         "title": "Owner", "company": "Acme", "confidence": "pattern+mx"}
    assert s.record_contact(c) is True            # new
    assert s.record_contact(c) is False           # same (name, domain) → deduped


def test_save_all_counts_new_vs_total():
    s = _store()
    rows = [
        {"name": "John Doe", "domain": "acme.com", "email": "john.doe@acme.com"},
        {"name": "Jane Smith", "domain": "acme.com", "email": "jane.smith@acme.com"},
        {"name": "John Doe", "domain": "acme.com", "email": "john.doe@acme.com"},  # dup
    ]
    res = s.save_all(rows)
    assert res == {"saved": 3, "new": 2}

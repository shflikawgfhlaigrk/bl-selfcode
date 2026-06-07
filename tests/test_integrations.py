"""Personal-assistant integration capabilities (calendar/notes/contacts) — ready-skeletons,
each GATED on its auth (Google for calendar, macOS perms for notes/contacts). The action path
is wired; with no auth each documents the gate and returns done=False (never fakes). The
client/runner is injectable so the wiring is proven without real auth."""
from __future__ import annotations

from utah import failures
from utah.integrations import calendar, contacts, notes
from tests.fakes import FakeFailureStore


def test_calendar_create_uses_injected_client():
    failures.set_store(FakeFailureStore())
    made = []
    r = calendar.create_event("Standup", "2026-06-07T09:00", "2026-06-07T09:15",
                              client_fn=lambda t, s, e: made.append((t, s, e)) or "evt1")
    assert r["created"] is True and r["gated"] is False and r["id"] == "evt1"
    assert made == [("Standup", "2026-06-07T09:00", "2026-06-07T09:15")]


def test_calendar_gated_without_auth(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(calendar, "auth_available", lambda: False)
    r = calendar.create_event("X", "s", "e")
    assert r["created"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)


def test_notes_add_gated_without_perms(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(notes, "perms_available", lambda: False)
    r = notes.add_note("title", "body")
    assert r["saved"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)


def test_notes_add_uses_injected_runner():
    failures.set_store(FakeFailureStore())
    saved = []
    r = notes.add_note("T", "B", run_fn=lambda title, body: saved.append((title, body)) or True)
    assert r["saved"] is True and r["gated"] is False and saved == [("T", "B")]


def test_contacts_lookup_gated_without_perms(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(contacts, "perms_available", lambda: False)
    r = contacts.lookup("Jane")
    assert r["found"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)


def test_contacts_lookup_uses_injected_runner():
    failures.set_store(FakeFailureStore())
    r = contacts.lookup("Jane", run_fn=lambda name: [{"name": "Jane Doe", "phone": "555"}])
    assert r["found"] is True and r["gated"] is False and r["results"][0]["name"] == "Jane Doe"

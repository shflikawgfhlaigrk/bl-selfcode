"""macOS ops + external data (gated skeletons) + profile/librarian (memory-backed, real)."""
from __future__ import annotations

from utah import failures, profile
from utah.integrations import external, macos
from tests.fakes import FakeFailureStore


def test_macos_clipboard_injected_runner():
    failures.set_store(FakeFailureStore())
    r = macos.read_clipboard(run_fn=lambda: "copied text")
    assert r["ok"] is True and r["text"] == "copied text"


def test_macos_gated_without_perms(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(macos, "perms_available", lambda: False)
    r = macos.run_shortcut("Morning")
    assert r["ok"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)


def test_external_gated_without_key(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(external, "source_available", lambda: False)
    assert external.weather("Newnan GA")["gated"] is True
    assert external.quote("AAPL")["gated"] is True
    assert any("gated" in row[2] for row in store.rows)


def test_external_with_injected_fetch():
    failures.set_store(FakeFailureStore())
    r = external.weather("Newnan GA", fetch=lambda loc: {"temp_f": 78})
    assert r["available"] is True and r["data"]["temp_f"] == 78


def test_profile_remember_and_browse_injected():
    failures.set_store(FakeFailureStore())
    stored = []
    r = profile.remember_profile("Michael lives in Newnan, GA",
                                 store=lambda f: stored.append(f) or type("R", (), {"id": 1})())
    assert r["stored"] is True and stored == ["Michael lives in Newnan, GA"]

    class _H:
        content = "Michael lives in Newnan, GA"
        score = 0.9
    out = profile.browse("where does Michael live", recall=lambda q, k: [_H()])
    assert out and out[0]["content"].startswith("Michael lives")

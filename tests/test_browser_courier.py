"""Browser (gated skeleton) + courier (delivery router over mail/notify)."""
from __future__ import annotations

from utah import courier, failures
from utah.integrations import browser
from tests.fakes import FakeFailureStore


def test_browser_gated_without_chrome(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(browser, "available", lambda: False)
    r = browser.render("https://x.com")
    assert r["rendered"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)


def test_browser_injected_renderer():
    failures.set_store(FakeFailureStore())
    r = browser.render("https://x.com", render_fn=lambda u: "<html>ok</html>")
    assert r["rendered"] is True and r["html"] == "<html>ok</html>"


def test_courier_routes_email_when_to_given():
    sent = []
    r = courier.deliver("hi", via="email", to="a@b.com",
                        mail_send=lambda to, s, b: sent.append((to, s, b)) or {"sent": True, "gated": False})
    assert r["via"] == "email" and r["sent"] is True and sent


def test_courier_routes_notify_by_default():
    pinged = []
    r = courier.deliver("done", notify_fn=lambda m: pinged.append(m) or {"sent": True, "gated": False})
    assert r["via"] == "notify" and pinged == ["done"]

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


def test_courier_notify_real_path_carries_subject_as_title(monkeypatch):
    """With no injected notify_fn, courier hands the subject to notify.notify as the
    title — the desktop mirror's cleaned title survives the courier hop."""
    from utah.integrations import notify
    seen = {}
    monkeypatch.setattr(notify, "notify",
                        lambda m, *, title="Utah": seen.update(m=m, title=title)
                        or {"sent": True, "gated": False})
    r = courier.deliver("daemon down", subject="Utah CRITICAL")
    assert r["via"] == "notify" and r["sent"] is True
    assert seen == {"m": "daemon down", "title": "Utah CRITICAL"}


def test_courier_push_passes_url_through():
    """The brief stream's deck tap-through (url/url_title) must survive courier
    routing — pushover.send accepts them; courier may not drop them."""
    seen = {}
    r = courier.deliver("UTAH MORNING BRIEF", via="push", subject="☀️",
                        url="http://deck.tailnet:8766", url_title="Open the Utah deck",
                        push_send=lambda m, **k: seen.update(k) or {"sent": True, "gated": False})
    assert r["via"] == "push" and r["sent"] is True
    assert seen["url"] == "http://deck.tailnet:8766"
    assert seen["url_title"] == "Open the Utah deck"


def test_browser_autodetects_chrome_and_reports_chars(monkeypatch):
    # available() now derives from chrome_binary() — merged real driver, not a flag file.
    monkeypatch.setattr(browser, "chrome_binary", lambda: "/Applications/Google Chrome.app/x")
    assert browser.available() is True
    r = browser.render("https://x.com", render_fn=lambda u: "<html><h1>hi</h1></html>")
    assert r["rendered"] is True and r["chars"] == len("<html><h1>hi</h1></html>")


def test_browser_render_failure_is_recorded():
    store = FakeFailureStore(); failures.set_store(store)
    def boom(u):
        raise RuntimeError("chrome crashed")
    r = browser.render("https://x.com", render_fn=boom)
    assert r["rendered"] is False and r["gated"] is False
    assert any("render_failed" in row[2] for row in store.rows)


def test_courier_unknown_channel_is_an_honest_error():
    """A misdelivery is worse than no delivery: an unrecognized channel must say so,
    never silently degrade to a desktop notify nobody is watching."""
    pinged = []
    r = courier.deliver("hi", via="sms",
                        notify_fn=lambda m: pinged.append(m) or {"sent": True, "gated": False})
    assert r["via"] == "sms" and r["sent"] is False
    assert "unknown" in r["error"].lower()
    assert pinged == []


def test_courier_email_without_recipient_is_an_honest_error():
    pinged = []
    r = courier.deliver("hi", via="email",
                        notify_fn=lambda m: pinged.append(m) or {"sent": True, "gated": False})
    assert r["via"] == "email" and r["sent"] is False
    assert "recipient" in r["error"].lower()
    assert pinged == []

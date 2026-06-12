"""SITE-12 — the Signals product's deliverable: real-time delivery of graded engine
fires to SUBSCRIBERS. Fires already land in Postgres (``ledger.record_fire``) and page
Michael (``alerts.trade_fire``); this lane fans each REAL fire out to the
``signals_subscribers`` table by email (``utah.mail.send`` — inherits its creds gate)
plus ONE Discord post (the existing webhook transport). Everything here runs on
injected fakes — zero network, zero SMTP, zero real pushes — except the marked
Postgres integration test, which skips when the cluster is unreachable (Michael's
standard, like test_ledger). Dedup is FILE-BACKED per fire id (the alerts.py
2026-06-10 page-storm doctrine: a restart must never re-deliver a live fire).
"""
from __future__ import annotations

import json
import threading

import pytest

from utah import alerts, config
from utah.product import ledger as ledger_mod
from utah.product import signals


FIRE = {"id": 101, "engine": "breakout", "direction": "long", "entry": 29376.5,
        "symbol": "CM.NQM6", "stop": 29350.0, "target": 29420.0,
        "rationale": "20-bar breakout", "ts": "2026-06-12 09:30:00"}


def _mail(calls, result=None):
    """A capturing mail transport matching utah.mail.send(to, subject, body) -> dict."""
    def send(to, subject, body):
        calls.append({"to": to, "subject": subject, "body": body})
        return dict(result or {"sent": True, "gated": False, "to": to})
    return send


def _discord(calls, result=True):
    """A capturing Discord post seam: (title, content) -> bool|dict."""
    def post(title, content):
        calls.append({"title": title, "content": content})
        return result
    return post


def _subs(*emails, status="active"):
    return lambda: [{"email": e, "status": status} for e in emails]


# --- the honest gates ----------------------------------------------------------

def test_empty_subscriber_table_is_an_honest_gate():
    mail_calls, dc_calls = [], []
    r = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                             discord_post=_discord(dc_calls), subscribers_fn=lambda: [])
    assert r["sent"] == 0 and r["gated"] is True
    assert r["reason"] == "no active subscribers"
    assert not mail_calls and not dc_calls            # nothing faked, nothing sent


def test_gated_fire_is_not_marked_delivered():
    # The no-subscriber gate must NOT burn the dedup key: the first real subscriber
    # should still receive fires recorded after they sign up.
    r1 = signals.deliver_fire(FIRE, mail_send=_mail([]), discord_post=_discord([]),
                              subscribers_fn=lambda: [])
    assert r1["gated"] is True
    mail_calls = []
    r2 = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                              discord_post=_discord([]), subscribers_fn=_subs("a@b.com"))
    assert r2["sent"] == 1 and len(mail_calls) == 1


def test_flag_off_is_a_noop(monkeypatch):
    monkeypatch.setattr(config, "SIGNALS_DELIVERY", False)
    mail_calls = []
    r = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                             discord_post=_discord([]), subscribers_fn=_subs("a@b.com"))
    assert r["sent"] == 0 and r["gated"] is True and "disabled" in r["reason"]
    assert not mail_calls
    # ...and the flag-off gate must not burn the dedup key either.
    monkeypatch.setattr(config, "SIGNALS_DELIVERY", True)
    r2 = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                              discord_post=_discord([]), subscribers_fn=_subs("a@b.com"))
    assert r2["sent"] == 1


def test_subscriber_store_failure_is_honest_not_a_crash():
    def boom():
        raise signals.SignalsError("signals store unreachable: down")
    mail_calls = []
    r = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                             discord_post=_discord([]), subscribers_fn=boom)
    assert r["sent"] == 0 and r["gated"] is True and "unreachable" in r["reason"]
    assert not mail_calls


# --- the fan-out ----------------------------------------------------------------

def test_fan_out_emails_every_active_subscriber_and_posts_discord_once():
    mail_calls, dc_calls = [], []
    r = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                             discord_post=_discord(dc_calls),
                             subscribers_fn=_subs("a@b.com", "c@d.com"))
    assert r["sent"] == 2 and r["gated"] is False and r["fire_id"] == 101
    assert [c["to"] for c in mail_calls] == ["a@b.com", "c@d.com"]
    assert len(dc_calls) == 1                          # ONE post, not one per subscriber
    assert r["discord"]["posted"] is True
    blob = mail_calls[0]["subject"] + "\n" + mail_calls[0]["body"]
    for token in ("breakout", "LONG", "CM.NQM6", "29376.5", "29350.0", "29420.0",
                  "2026-06-12 09:30"):
        assert token in blob                           # the honest fire facts reach the inbox


def test_paused_subscribers_are_skipped():
    mail_calls = []
    subs = lambda: [{"email": "on@x.com", "status": "active"},
                    {"email": "off@x.com", "status": "paused"}]
    r = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                             discord_post=_discord([]), subscribers_fn=subs)
    assert r["sent"] == 1 and [c["to"] for c in mail_calls] == ["on@x.com"]


def test_one_failing_email_never_blocks_the_rest():
    delivered = []
    def flaky(to, subject, body):
        if to == "bad@x.com":
            raise RuntimeError("smtp died")
        delivered.append(to)
        return {"sent": True, "gated": False, "to": to}
    r = signals.deliver_fire(FIRE, mail_send=flaky, discord_post=_discord([]),
                             subscribers_fn=_subs("bad@x.com", "good@x.com"))
    assert r["sent"] == 1 and delivered == ["good@x.com"]
    bad = [e for e in r["email"] if e["to"] == "bad@x.com"]
    assert bad and bad[0]["sent"] is False and "smtp died" in bad[0]["error"]


def test_discord_gate_is_honest_and_never_blocks_email():
    mail_calls = []
    r = signals.deliver_fire(FIRE, mail_send=_mail(mail_calls),
                             discord_post=lambda t, c: False,
                             subscribers_fn=_subs("a@b.com"))
    assert r["sent"] == 1 and len(mail_calls) == 1
    assert r["discord"]["posted"] is False             # reported honestly, not faked


# --- the honest message ----------------------------------------------------------

def test_ungraded_fire_never_invents_an_outcome():
    subject, body = signals.compose(FIRE)
    assert "not yet graded" in body
    assert "win" not in body.lower() and "%" not in body  # no invented performance


def test_graded_outcome_is_included_truthfully():
    subject, body = signals.compose(dict(FIRE, outcome="target", pnl=2.5))
    assert "target" in body and "+2.5" in body


# --- dedup: a fire never delivers twice ------------------------------------------

def test_a_fire_never_delivers_twice():
    mail_calls = []
    kw = dict(mail_send=_mail(mail_calls), discord_post=_discord([]),
              subscribers_fn=_subs("a@b.com"))
    r1 = signals.deliver_fire(FIRE, **kw)
    r2 = signals.deliver_fire(FIRE, **kw)
    assert r1["sent"] == 1
    assert r2["sent"] == 0 and r2["gated"] is True and r2["reason"] == "already delivered"
    assert len(mail_calls) == 1


def test_dedup_survives_a_process_restart():
    kw = dict(mail_send=_mail([]), discord_post=_discord([]),
              subscribers_fn=_subs("a@b.com"))
    assert signals.deliver_fire(FIRE, **kw)["sent"] == 1
    signals._reset_seen_for_tests()                    # simulate a restart: reload from disk
    r = signals.deliver_fire(FIRE, **kw)
    assert r["gated"] is True and r["reason"] == "already delivered"


def test_newly_graded_fire_delivers_its_result_exactly_once():
    # The signal alert (ungraded) and the graded RESULT are distinct states: each
    # delivers once — that is what 'ungraded-or-newly-graded' means for the cron lane.
    kw = dict(mail_send=_mail([]), discord_post=_discord([]),
              subscribers_fn=_subs("a@b.com"))
    assert signals.deliver_fire(FIRE, **kw)["sent"] == 1
    graded = dict(FIRE, outcome="stop", pnl=-1.0)
    assert signals.deliver_fire(graded, **kw)["sent"] == 1     # new state -> delivers
    r3 = signals.deliver_fire(graded, **kw)
    assert r3["gated"] is True and r3["reason"] == "already delivered"


# --- malformed input never raises -------------------------------------------------

@pytest.mark.parametrize("bad", [None, {}, {"engine": "x"}, {"id": "nope"}, {"id": 0}, 42])
def test_malformed_fire_never_raises(bad):
    r = signals.deliver_fire(bad, mail_send=_mail([]), discord_post=_discord([]),
                             subscribers_fn=_subs("a@b.com"))
    assert r["sent"] == 0 and r["gated"] is True and "fire" in r["reason"]


# --- the live wiring: record_fire fans out ----------------------------------------

class _Cur:
    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return (77,)


class _Conn:
    def __enter__(self):
        return _Cur()

    def __exit__(self, *a):
        return False


def _stub_ledger(monkeypatch):
    lg = ledger_mod.Ledger.__new__(ledger_mod.Ledger)   # no DSN, no connect
    monkeypatch.setattr(lg, "_conn", lambda: _Conn(), raising=False)
    monkeypatch.setattr(lg, "_emit", lambda *a, **k: None, raising=False)
    return lg


def test_record_fire_fans_out_to_signals(monkeypatch):
    monkeypatch.setattr(alerts, "trade_fire", lambda *a, **k: {"sent": True})
    fired = []
    monkeypatch.setattr(signals, "deliver_fire_async", lambda fire, **k: fired.append(fire))
    lg = _stub_ledger(monkeypatch)
    fid = lg.record_fire("breakout", "long", entry=29376.5, symbol="CM.NQM6",
                         stop=29350.0, target=29420.0, rationale="r1")
    assert fid == 77
    (fire,) = fired
    assert fire["id"] == 77 and fire["engine"] == "breakout"
    assert fire["direction"] == "long" and fire["entry"] == 29376.5
    assert fire["symbol"] == "CM.NQM6" and fire["stop"] == 29350.0
    assert fire["target"] == 29420.0


def test_record_fire_synthetic_never_fans_out(monkeypatch):
    fired = []
    monkeypatch.setattr(signals, "deliver_fire_async", lambda fire, **k: fired.append(fire))
    lg = _stub_ledger(monkeypatch)
    lg.record_fire("demo", "long", entry=1.0, synthetic=True)
    assert fired == []                                  # demo fires never reach subscribers


def test_signals_failure_never_breaks_fire_recording(monkeypatch):
    monkeypatch.setattr(alerts, "trade_fire", lambda *a, **k: {"sent": True})
    def boom(fire, **k):
        raise RuntimeError("delivery exploded")
    monkeypatch.setattr(signals, "deliver_fire_async", boom)
    lg = _stub_ledger(monkeypatch)
    assert lg.record_fire("breakout", "long", entry=1.0) == 77   # the write survives


def test_deliver_fire_async_runs_on_a_thread_and_never_raises():
    done = threading.Event()
    signals.deliver_fire_async(dict(FIRE), deliver=lambda f: done.set())
    assert done.wait(2.0)
    signals.deliver_fire_async(dict(FIRE), deliver=lambda f: 1 / 0)  # swallowed, no crash


# --- the cron lane: deliver-latest -------------------------------------------------

def test_deliver_latest_delivers_the_newest_fire():
    mail_calls = []
    r = signals.deliver_latest(fire_fn=lambda: dict(FIRE), mail_send=_mail(mail_calls),
                               discord_post=_discord([]), subscribers_fn=_subs("a@b.com"))
    assert r["sent"] == 1 and r["fire_id"] == 101 and len(mail_calls) == 1


def test_deliver_latest_with_no_fires_is_honest():
    r = signals.deliver_latest(fire_fn=lambda: None, mail_send=_mail([]),
                               discord_post=_discord([]), subscribers_fn=_subs("a@b.com"))
    assert r["sent"] == 0 and r["gated"] is True and r["reason"] == "no fires recorded"


def test_deliver_latest_store_failure_is_honest():
    def boom():
        raise signals.SignalsError("signals store unreachable: down")
    r = signals.deliver_latest(fire_fn=boom, mail_send=_mail([]),
                               discord_post=_discord([]), subscribers_fn=_subs("a@b.com"))
    assert r["sent"] == 0 and r["gated"] is True and "unreachable" in r["reason"]


def test_cli_deliver_latest_prints_honest_json(monkeypatch, capsys):
    monkeypatch.setattr(signals, "deliver_latest",
                        lambda **k: {"sent": 0, "gated": True,
                                     "reason": "no active subscribers"})
    rc = signals._main(["deliver-latest"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["reason"] == "no active subscribers"


# --- the real table (skips without Postgres — Michael's standard) ------------------

MARK = "__pytest_signals__"


def test_subscribers_roundtrip_on_real_postgres():
    try:
        signals.init_schema()
    except signals.SignalsError:
        pytest.skip("Postgres not reachable — signals integration test skipped")
    a, b = f"{MARK}a@example.test", f"{MARK}b@example.test"
    try:
        assert signals.subscribe(a) is True
        assert signals.subscribe(a) is False            # never the same subscriber twice
        assert signals.subscribe(b) is True
        assert signals.set_status(b, "paused") is True
        active = {s["email"] for s in signals.subscribers()}
        assert a in active and b not in active          # paused rows never get fires
    finally:
        import psycopg

        with psycopg.connect(config.DB_DSN, autocommit=True) as c:
            c.execute("DELETE FROM signals_subscribers WHERE email LIKE %s", (MARK + "%",))

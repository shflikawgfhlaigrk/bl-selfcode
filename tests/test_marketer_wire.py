"""Marketer/reel pipeline wired UP TO the human gate.

The reel pipeline renders reels into a folder; posting is HUMAN-GATED on
IG/TikTok creds ("reels rendered, nothing posted"). These tests prove the
missing wiring: a rendered/queued reel is recorded in the ledger (idempotent,
never-twice) and publishes a REAL bus event so the deck reflects queued reels —
while the social POST stays honestly gated and never fakes a confirmation.

No Postgres, no network: a fake ledger (backed by a real ``Bus`` publisher) and
a temp render dir stand in for the live store/socket.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from utah.daemon.bus import Bus
from utah.product import reel_queue
from utah.integrations import social_post


class FakeLedger:
    """Stand-in for utah.product.ledger.Ledger.record_post — enforces the real
    never-twice contract (UNIQUE(channel, media_ref)) and emits to a real Bus on
    each NEW row, exactly as the live ledger pushes the deck on a write."""

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.rows: list[dict] = []
        self._seen: set[tuple[str, str]] = set()

    def record_post(self, channel, caption, media_ref, subject="", status="posted",
                    post_id=None) -> bool:
        key = (channel, media_ref)
        if key in self._seen:           # ON CONFLICT (channel, media_ref) DO NOTHING
            return False
        self._seen.add(key)
        self.rows.append({"channel": channel, "caption": caption, "media_ref": media_ref,
                          "subject": subject, "status": status, "post_id": post_id})
        # Mirror Ledger._emit("marketer", ...): a write lights the deck by push.
        self.bus.publish("marketer", {"channel": channel, "subject": subject,
                                       "status": status})
        return True


def _render_reel(render_dir: Path, stem: str, caption: str) -> None:
    """Simulate the renderer dropping a finished .mp4 + .txt caption pair."""
    (render_dir / f"{stem}.mp4").write_bytes(b"\x00fake-mp4")
    (render_dir / f"{stem}.txt").write_text(caption)


@pytest.fixture
def bus_sub():
    """A real Bus with one firehose subscriber draining delivered events."""
    bus = Bus()
    sub = bus.subscribe()  # all channels
    received: list[dict] = []

    def drain():
        while True:
            try:
                received.append(sub.receive.receive_nowait())
            except Exception:
                break

    return bus, received, drain


# --- the wire: a rendered reel yields a ledger row + a real bus event ------------------

def test_rendered_reel_yields_ledger_row_and_bus_event(tmp_path, bus_sub):
    bus, received, drain = bus_sub
    lg = FakeLedger(bus)
    _render_reel(tmp_path, "tabby_house_promo", "Spotlight: Tabby House #local")

    reels = reel_queue._scan_reels(tmp_path)
    newly = reel_queue.record_queued(reels, lg)

    # Ledger row recorded, with the public media_ref as the never-twice key.
    assert len(lg.rows) == 1
    row = lg.rows[0]
    assert row["channel"] == "tiktok"
    assert row["status"] == "queued"
    assert row["subject"] == "tabby_house_promo"
    assert row["media_ref"].endswith("/tabby_house_promo.mp4")
    assert [r["id"] for r in newly] == ["tabby_house_promo"]

    # A REAL bus event landed on the 'marketer' deck channel.
    drain()
    assert len(received) == 1
    msg = received[0]
    assert msg["channel"] == "marketer"
    assert msg["event"]["status"] == "queued"
    assert msg["event"]["subject"] == "tabby_house_promo"


def test_queueing_is_idempotent_never_twice(tmp_path, bus_sub):
    """Re-scanning the same render dir must not light the deck twice (UNIQUE key)."""
    bus, received, drain = bus_sub
    lg = FakeLedger(bus)
    _render_reel(tmp_path, "dup_reel", "caption")

    reels = reel_queue._scan_reels(tmp_path)
    first = reel_queue.record_queued(reels, lg)
    second = reel_queue.record_queued(reels, lg)  # same reels, run again

    assert [r["id"] for r in first] == ["dup_reel"]
    assert second == []              # nothing new the second pass
    assert len(lg.rows) == 1         # one ledger row, ever
    drain()
    assert len(received) == 1        # one bus event, ever


def test_record_queued_no_ledger_is_a_safe_noop(tmp_path):
    _render_reel(tmp_path, "no_ledger", "caption")
    reels = reel_queue._scan_reels(tmp_path)
    assert reel_queue.record_queued(reels, None) == []


def test_record_queued_ledger_hiccup_never_loses_render(tmp_path):
    """A ledger write failure is recorded as a failure, never raised — the render
    is not lost and the batch continues."""
    from utah import failures
    from tests.fakes import FakeFailureStore

    store = FakeFailureStore()
    failures.set_store(store)
    try:
        class _Boom:
            def record_post(self, *a, **k):
                raise RuntimeError("marketer_posts unreachable")

        _render_reel(tmp_path, "boom_reel", "caption")
        reels = reel_queue._scan_reels(tmp_path)
        newly = reel_queue.record_queued(reels, _Boom())  # must not raise
        assert newly == []
        assert any(row[2] == "ledger_write_failed" for row in store.rows)
    finally:
        failures.set_store(None)


# --- the gate: posting stays HUMAN-GATED, never a fake confirmation --------------------

def test_run_scheduled_records_queue_then_stays_gated(tmp_path, bus_sub, monkeypatch):
    """End-to-end up to the gate: rendered reels are recorded + lit on the deck,
    but with no TikTok creds NOTHING is posted and the tally is honestly gated."""
    bus, received, drain = bus_sub
    lg = FakeLedger(bus)
    _render_reel(tmp_path, "gated_reel", "caption")

    # Avoid the Postgres-backed queue table: stub the DB-touching steps. The
    # ledger/bus wiring (record_queued) is exercised for real.
    monkeypatch.setattr(reel_queue, "_ensure_schema", lambda: None)

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): return self
        def fetchall(self): return []
    monkeypatch.setattr(reel_queue, "_pool", lambda: type("P", (), {"connection": staticmethod(lambda: _Conn())})())

    # Force the gate shut regardless of the host's real ~/.utah/secrets.
    monkeypatch.setattr(social_post, "creds_available", lambda channel: False)

    out = reel_queue.run_scheduled(lg, render_dir=tmp_path)

    # Posting is gated — nothing published to a social network, honest note.
    assert out["gated"] is True
    assert "GATED" in out["note"]
    assert out["tiktok"]["posted"] == 0
    assert out["tiktok"]["gated"] >= 0
    # But the reel WAS queued + recorded + lit the deck.
    assert len(lg.rows) == 1
    assert lg.rows[0]["status"] == "queued"
    drain()
    assert any(m["channel"] == "marketer" for m in received)


def test_post_next_is_gated_without_creds(monkeypatch):
    """The post boundary itself never fabricates: no creds -> gated tally, no post."""
    monkeypatch.setattr(reel_queue, "_ensure_schema", lambda: None)

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): return self
        def fetchall(self): return []
    monkeypatch.setattr(reel_queue, "_pool", lambda: type("P", (), {"connection": staticmethod(lambda: _Conn())})())
    monkeypatch.setattr(social_post, "creds_available", lambda channel: False)

    tally = reel_queue.post_next(max_posts=1, channel="tiktok")
    assert tally["posted"] == 0

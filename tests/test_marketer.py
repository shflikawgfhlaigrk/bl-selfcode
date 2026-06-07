"""Marketer capability — ready-skeleton GATED on IG/TikTok posting creds. Builds the post
(caption + media ref) and routes through the publish boundary; with no creds it documents
the gate and returns posted=False (never fakes a post). Publisher injectable."""
from __future__ import annotations

from utah import failures
from utah.product import marketer
from tests.fakes import FakeFailureStore


def test_compose_caption_includes_subject_and_cta():
    cap = marketer.compose_caption({"name": "Foxtail Coffee Co.", "kind": "cafe"})
    assert "Foxtail Coffee Co." in cap and len(cap) <= marketer.MAX_CAPTION


def test_post_uses_injected_publisher():
    failures.set_store(FakeFailureStore())
    posted = []
    r = marketer.post("caption here", media_ref="reel.mp4", channel="instagram",
                      publish_fn=lambda cap, media, ch: posted.append((cap, media, ch)) or "post123")
    assert r["posted"] is True and r["gated"] is False and r["id"] == "post123"
    assert posted == [("caption here", "reel.mp4", "instagram")]


def test_post_gated_without_creds(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(marketer, "creds_available", lambda ch: False)
    r = marketer.post("cap", media_ref="reel.mp4", channel="tiktok")
    assert r["posted"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)


def test_post_failure_documented():
    store = FakeFailureStore(); failures.set_store(store)
    def boom(cap, media, ch):
        raise RuntimeError("ig api 401")
    r = marketer.post("cap", media_ref="r.mp4", channel="instagram", publish_fn=boom)
    assert r["posted"] is False and r.get("gated") is False
    assert any("post_failed" in row[2] for row in store.rows)

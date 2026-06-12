"""The IG/TikTok publish adapter contract: gated without creds, post() never fabricates."""
from __future__ import annotations

import pytest

from utah.integrations import social_post
from utah.product import marketer


def test_tiktok_publish_requires_creds(monkeypatch, tmp_path):
    monkeypatch.setattr(social_post, "_SECRETS", tmp_path)
    with pytest.raises(RuntimeError, match="gated"):
        social_post.publish_tiktok("cap", "https://x/v.mp4")


def test_post_gates_without_creds(monkeypatch, tmp_path):
    monkeypatch.setattr(social_post, "_SECRETS", tmp_path)
    res = marketer.post("caption", media_ref="https://x/v.mp4", channel="instagram")
    assert res == {"posted": False, "gated": True, "channel": "instagram"}


def test_post_publishes_with_injected_publisher():
    res = marketer.post("caption", media_ref="https://x/v.mp4",
                        publish_fn=lambda c, m, ch: "ig_17900000")
    assert res["posted"] is True and res["id"] == "ig_17900000"

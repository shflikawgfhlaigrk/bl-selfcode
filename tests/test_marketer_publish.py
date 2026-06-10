"""The IG publish adapter contract: instagram-only, gated without creds, and the
post() seam still never fabricates. (The old _real_publish RAISED even with creds —
the marketer could never post; this locks the repaired contract.)"""
from __future__ import annotations

import pytest

from utah.product import marketer


def test_unimplemented_channel_raises_honestly():
    with pytest.raises(RuntimeError, match="not implemented"):
        marketer._real_publish("cap", "https://x/v.mp4", "tiktok")


def test_post_gates_without_creds(monkeypatch, tmp_path):
    monkeypatch.setattr(marketer, "_SECRETS", tmp_path)
    res = marketer.post("caption", media_ref="https://x/v.mp4", channel="instagram")
    assert res == {"posted": False, "gated": True, "channel": "instagram"}


def test_post_publishes_with_injected_publisher():
    res = marketer.post("caption", media_ref="https://x/v.mp4",
                        publish_fn=lambda c, m, ch: "ig_17900000")
    assert res["posted"] is True and res["id"] == "ig_17900000"

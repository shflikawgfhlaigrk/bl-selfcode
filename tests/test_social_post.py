"""Social posting boundary — gated without creds, honest status, injectable publish."""
from __future__ import annotations

import json

import pytest

from utah.integrations import social_post


def test_status_email_live_when_mail_ready(monkeypatch):
    monkeypatch.setattr(social_post, "creds_available", lambda ch: False)
    out = social_post.status(mail_ready=True)
    assert out["email"] == "live"
    assert out["channels"]["instagram"] == "gated"
    assert out["channels"]["x"] == "not_implemented"


def test_post_gated_without_creds(monkeypatch, tmp_path):
    monkeypatch.setattr(social_post, "_SECRETS", tmp_path)
    res = social_post.post("cap", media_ref="https://x/v.mp4", channel="instagram")
    assert res == {"posted": False, "gated": True, "channel": "instagram"}


def test_post_uses_injected_publisher():
    seen = []

    def pub(cap, media):
        seen.append((cap, media))
        return "ig_123"

    res = social_post.post(
        "hello",
        media_ref="https://cdn.example/v.mp4",
        channel="instagram",
        publish_fn=pub,
    )
    assert res["posted"] is True and res["id"] == "ig_123"
    assert seen == [("hello", "https://cdn.example/v.mp4")]


def test_creds_from_env(monkeypatch, tmp_path):
    monkeypatch.setattr(social_post, "_SECRETS", tmp_path)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1789")
    assert social_post.creds_available("instagram") is True


def test_creds_from_secrets_file(tmp_path, monkeypatch):
    monkeypatch.setattr(social_post, "_SECRETS", tmp_path)
    (tmp_path / "tiktok.json").write_text(json.dumps({"access_token": "tt"}))
    assert social_post.creds_available("tiktok") is True


def test_unsupported_channel_is_honest():
    res = social_post.post("cap", media_ref="x", channel="linkedin")
    assert res["posted"] is False and "not implemented" in res["error"]


def test_publish_instagram_requires_creds(monkeypatch, tmp_path):
    monkeypatch.setattr(social_post, "_SECRETS", tmp_path)
    with pytest.raises(RuntimeError, match="gated"):
        social_post.publish_instagram("cap", "https://x/v.mp4")

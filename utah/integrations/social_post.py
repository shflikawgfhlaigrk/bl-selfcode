"""Social posting boundary — Instagram Graph API + TikTok Content Posting API.

Creds land at ``~/.utah/secrets/{channel}.json`` or via env (``INSTAGRAM_*``,
``TIKTOK_*``). Without creds: ``status()`` reports ``gated``; ``post()`` returns
``posted=False`` — never fakes a publish. Other platforms (X, Facebook, LinkedIn,
Threads) are honestly ``not_implemented`` until wired.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from utah.daemon import runtime

_SECRETS = runtime.UTAH_HOME / "secrets"
_SUPPORTED = frozenset({"instagram", "tiktok"})


def _secret_path(channel: str) -> os.PathLike[str]:
    return _SECRETS / f"{channel}.json"


def creds_available(channel: str) -> bool:
    """True when secrets file or env vars supply posting creds for *channel*."""
    if channel not in _SUPPORTED:
        return False
    if _secret_path(channel).exists():
        return True
    if channel == "instagram":
        return bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN") and os.environ.get("INSTAGRAM_USER_ID"))
    return bool(os.environ.get("TIKTOK_ACCESS_TOKEN"))


def _load_creds(channel: str) -> dict[str, str]:
    path = _secret_path(channel)
    if path.exists():
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return {k: str(v) for k, v in data.items()}
    if channel == "instagram":
        token, user = os.environ.get("INSTAGRAM_ACCESS_TOKEN"), os.environ.get("INSTAGRAM_USER_ID")
        if token and user:
            return {"access_token": token, "ig_user_id": user}
    if channel == "tiktok":
        token = os.environ.get("TIKTOK_ACCESS_TOKEN")
        if token:
            return {"access_token": token}
    raise RuntimeError(
        f"{channel} gated: no creds at {_secret_path(channel)} "
        f"or {channel.upper()}_* env vars"
    )


def _http_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _http_json_post(url: str, payload: dict, *, headers: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode())


def publish_instagram(caption: str, media_ref: str) -> str:
    """Publish an Instagram Reel via Graph API. *media_ref* must be a public https mp4."""
    creds = _load_creds("instagram")
    token, user = creds["access_token"], creds["ig_user_id"]
    base = f"https://graph.facebook.com/v21.0/{user}"

    container = _http_form(f"{base}/media", {
        "media_type": "REELS",
        "video_url": media_ref,
        "caption": caption[:2190],
        "access_token": token,
    })["id"]

    for _ in range(30):
        st = _http_get_json(
            f"https://graph.facebook.com/v21.0/{container}"
            f"?fields=status_code&access_token={urllib.parse.quote(token)}"
        )
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"IG container processing failed: {st}")
        time.sleep(5)

    return _http_form(f"{base}/media_publish", {
        "creation_id": container,
        "access_token": token,
    })["id"]


def publish_tiktok(caption: str, media_ref: str) -> str:
    """Publish a TikTok video via Content Posting API (PULL_FROM_URL)."""
    creds = _load_creds("tiktok")
    token = creds["access_token"]
    init = _http_json_post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        {
            "post_info": {
                "title": caption[:150],
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {"source": "PULL_FROM_URL", "video_url": media_ref},
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
    )
    if init.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"TikTok init failed: {init}")
    return init["data"]["publish_id"]


def post(caption: str, *, media_ref: str, channel: str = "instagram", publish_fn=None) -> dict[str, Any]:
    """Publish to *channel* when creds exist; otherwise document the gate."""
    if channel not in _SUPPORTED:
        return {
            "posted": False,
            "gated": False,
            "channel": channel,
            "error": f"{channel} posting not implemented",
        }
    if publish_fn is None and not creds_available(channel):
        return {"posted": False, "gated": True, "channel": channel}
    publisher = publish_fn or (publish_instagram if channel == "instagram" else publish_tiktok)
    try:
        post_id = publisher(caption, media_ref)
        return {"posted": True, "gated": False, "channel": channel, "id": post_id}
    except Exception as exc:  # noqa: BLE001
        return {"posted": False, "gated": False, "channel": channel, "error": str(exc)}


def channel_state(channel: str) -> dict[str, str]:
    if channel in _SUPPORTED:
        return {"channel": channel, "status": "ready" if creds_available(channel) else "gated"}
    return {"channel": channel, "status": "not_implemented"}


def status(*, mail_ready: bool | None = None) -> dict[str, Any]:
    """Connection truth for the marketing deck and public site."""
    if mail_ready is None:
        from utah import mail

        mail_ready = mail.creds_available()

    channels = {
        "email_spotlight": "live" if mail_ready else "gated",
        "instagram": channel_state("instagram")["status"],
        "tiktok": channel_state("tiktok")["status"],
        "x": "not_implemented",
        "facebook": "not_implemented",
        "linkedin": "not_implemented",
        "threads": "not_implemented",
    }
    social_ready = sum(1 for k, v in channels.items() if k != "email_spotlight" and v == "ready")
    return {
        "email": channels["email_spotlight"],
        "channels": channels,
        "social_ready": social_ready,
        "social_total": 6,
        "note": (
            "email_spotlight LIVE on com.utah.marketer cron when gmail creds present; "
            "IG/TikTok post when creds + public media URL land"
        ),
    }


__all__ = [
    "creds_available",
    "post",
    "publish_instagram",
    "publish_tiktok",
    "channel_state",
    "status",
]

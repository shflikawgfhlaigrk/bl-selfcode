"""Reel posting queue — reads staged .mp4/.txt pairs from REEL_DIR, tracks
posted status in Postgres, posts via the social_post boundary.

TikTok's Content Posting API requires a public URL (PULL_FROM_URL), so videos
are served temporarily via the site's /data/ path on Cloudflare before posting.
Instagram is suspended — queue targets TikTok first, YouTube Shorts second.

Gate: posts nothing until creds land at ~/.utah/secrets/tiktok.json:
  {"access_token": "<user_access_token>"}

Credentials guide:
  1. Register a TikTok developer app at developers.tiktok.com
  2. Add scope: video.upload
  3. Complete OAuth, save the access_token to ~/.utah/secrets/tiktok.json
  4. This queue fires automatically on the next marketer run.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from utah import config
from utah.db_pool import get_pool
from utah.integrations import social_post

log = logging.getLogger("utah.product.reel_queue")

REEL_DIR = Path.home() / "Desktop" / "REELS-TO-POST"
POSTED_DIR = REEL_DIR / "posted"
# Public base URL where videos are served (Cloudflare site /data/reels/)
PUBLIC_BASE = "https://blacklabelbots.com/data/reels"

_DDL = """
CREATE TABLE IF NOT EXISTS reel_queue (
    id            text PRIMARY KEY,          -- stem of the mp4 filename
    mp4_path      text NOT NULL,
    caption       text NOT NULL DEFAULT '',
    channel       text NOT NULL DEFAULT 'tiktok',
    status        text NOT NULL DEFAULT 'pending',  -- pending | posted | skipped | error
    post_id       text,
    error         text,
    queued_at     timestamptz NOT NULL DEFAULT now(),
    posted_at     timestamptz
);
"""


def _pool():
    return get_pool(config.DB_DSN)


def _ensure_schema() -> None:
    with _pool().connection() as c:
        c.execute(_DDL)


def _scan_reels(render_dir: Path | None = None) -> list[dict]:
    """Return all .mp4 files in *render_dir* (default REEL_DIR) with their captions."""
    reels = []
    root = render_dir if render_dir is not None else REEL_DIR
    if not root.exists():
        return reels
    for mp4 in sorted(root.glob("*.mp4")):
        txt = mp4.with_suffix(".txt")
        caption = txt.read_text().strip() if txt.exists() else ""
        reels.append({"id": mp4.stem, "mp4_path": str(mp4), "caption": caption})
    return reels


def media_ref_for(reel: dict, channel: str = "tiktok") -> str:
    """The public URL a rendered reel WILL post from once creds land — the same
    string used as the ledger's never-twice key, so queueing and posting agree."""
    return f"{PUBLIC_BASE}/{Path(reel['mp4_path']).name}"


def record_queued(reels: list[dict], ledger, *, channel: str = "tiktok") -> list[dict]:
    """Wire a batch of rendered reels UP TO the human gate: record each in the
    revenue ledger (``marketer_posts``, status='queued') and let that write
    publish the real ``marketer`` bus event so the deck reflects queued reels.

    Idempotent by construction — ``Ledger.record_post`` is UNIQUE(channel,
    media_ref) ON CONFLICT DO NOTHING, so re-running a render scan never lights
    the deck twice for the same media. Returns the reels that were NEW to the
    ledger. NOTHING is posted here; posting stays gated in :func:`post_next`."""
    if ledger is None:
        return []
    newly: list[dict] = []
    for r in reels:
        media_ref = media_ref_for(r, channel)
        try:
            is_new = ledger.record_post(
                channel, r.get("caption", ""), media_ref,
                subject=r["id"], status="queued", post_id=None,
            )
        except Exception as exc:  # noqa: BLE001 — a ledger hiccup must not lose the render
            from utah import failures
            failures.record("reel_queue", "ledger_write_failed", f"{r['id']}: {exc}")
            continue
        if is_new:
            newly.append(r)
            log.info("reel_queue: queued reel %s recorded in ledger (deck lit)", r["id"])
    return newly


def seed_queue(*, ledger=None, render_dir: Path | None = None) -> int:
    """Register all discovered reels as pending rows (idempotent) and, when a
    *ledger* is supplied, record each in ``marketer_posts`` (status='queued') so
    a rendered/queued reel lights the deck via a real bus event. Posting stays
    gated — see :func:`post_next`."""
    _ensure_schema()
    reels = _scan_reels(render_dir)
    registered = 0
    with _pool().connection() as c:
        for r in reels:
            c.execute(
                """INSERT INTO reel_queue (id, mp4_path, caption)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (r["id"], r["mp4_path"], r["caption"]),
            )
            registered += 1
    record_queued(reels, ledger)
    return registered


def pending() -> list[dict]:
    """Return pending reels in posting order (oldest first)."""
    _ensure_schema()
    with _pool().connection() as c:
        rows = c.execute(
            "SELECT id, mp4_path, caption, channel FROM reel_queue "
            "WHERE status='pending' ORDER BY queued_at ASC"
        ).fetchall()
    return [{"id": r[0], "mp4_path": r[1], "caption": r[2], "channel": r[3]} for r in rows]


def _mark(reel_id: str, status: str, *, post_id: str = "", error: str = "") -> None:
    with _pool().connection() as c:
        c.execute(
            """UPDATE reel_queue SET status=%s, post_id=%s, error=%s,
               posted_at=CASE WHEN %s='posted' THEN now() ELSE posted_at END
               WHERE id=%s""",
            (status, post_id or None, error or None, status, reel_id),
        )


def post_next(*, max_posts: int = 1, channel: str = "tiktok") -> dict:
    """Post up to max_posts pending reels. Gated on channel credentials.

    TikTok requires a public URL — videos must already be accessible at
    PUBLIC_BASE/<stem>.mp4 (deployed to Cloudflare via the site pipeline).
    Returns a tally dict.
    """
    tally = {"posted": 0, "gated": 0, "error": 0, "skipped": 0, "auth_error": 0}

    if not social_post.creds_available(channel):
        log.info("reel_queue: %s gated — no credentials at ~/.utah/secrets/%s.json", channel, channel)
        tally["gated"] = len(pending())
        return tally

    queue = pending()
    for reel in queue[:max_posts]:
        public_url = media_ref_for(reel, channel)
        result = social_post.post(reel["caption"], media_ref=public_url, channel=channel)

        if result.get("auth_error"):
            # Creds present but token rejected (401/403): a GLOBAL auth failure. Do NOT
            # poison this reel as a content error (it would be skipped forever once the
            # token is fixed). Leave it pending, record loudly, and stop — every reel
            # this run would hit the same 401.
            from utah import failures
            err = result.get("error", "auth rejected")
            failures.record(
                "reel_queue", "auth_invalid",
                f"{channel} token rejected ({err}) — refresh ~/.utah/secrets/{channel}.json; "
                f"reel {reel['id']} left pending for retry",
            )
            tally["auth_error"] += 1
            log.warning("reel_queue: %s auth rejected (%s) — leaving %s pending, stopping run",
                        channel, err, reel["id"])
            break
        if result.get("gated"):
            tally["gated"] += 1
            break
        elif result.get("posted"):
            _mark(reel["id"], "posted", post_id=result.get("id", ""))
            _move_to_posted(reel["mp4_path"])
            tally["posted"] += 1
            log.info("reel_queue: posted %s → %s (%s)", reel["id"], channel, result.get("id"))
        else:
            err = result.get("error", "unknown error")
            _mark(reel["id"], "error", error=err)
            tally["error"] += 1
            log.warning("reel_queue: error posting %s: %s", reel["id"], err)

    return tally


def requeue_errors() -> int:
    """Reset reels stuck in ``error`` back to ``pending`` so a fixed credential or a newly
    deployed public media URL gets a fresh attempt. Returns the number requeued.

    Recovery path for the auth case: before auth-aware :func:`post_next`, a single invalid
    token (401) marked every attempted reel a permanent ``error``; those are real, good
    reels that should retry once the token is refreshed. Idempotent — a no-op when nothing
    is errored."""
    _ensure_schema()
    with _pool().connection() as c:
        rows = c.execute(
            "UPDATE reel_queue SET status='pending', error=NULL "
            "WHERE status='error' RETURNING id"
        ).fetchall()
    n = len(rows)
    if n:
        log.info("reel_queue: requeued %d errored reel(s) to pending", n)
    return n


def _move_to_posted(mp4_path: str) -> None:
    POSTED_DIR.mkdir(exist_ok=True)
    src = Path(mp4_path)
    if src.exists():
        shutil.move(str(src), POSTED_DIR / src.name)
        txt = src.with_suffix(".txt")
        if txt.exists():
            shutil.move(str(txt), POSTED_DIR / txt.name)


def status() -> dict:
    """Queue health summary."""
    _ensure_schema()
    with _pool().connection() as c:
        rows = c.execute(
            "SELECT status, count(*) FROM reel_queue GROUP BY status"
        ).fetchall()
    counts = {r[0]: r[1] for r in rows}
    return {
        "pending": counts.get("pending", 0),
        "posted": counts.get("posted", 0),
        "error": counts.get("error", 0),
        "tiktok_ready": social_post.creds_available("tiktok"),
        "instagram_ready": social_post.creds_available("instagram"),
    }


def run_scheduled(ledger=None, *, render_dir: Path | None = None) -> dict:
    """Called by the marketer cron. Seeds the queue from rendered reels — recording
    each in the ledger so the deck lights up (real bus event) — then attempts to
    post one reel per channel. The POST stays HUMAN-GATED: with no TikTok creds at
    ~/.utah/secrets/tiktok.json the tally is ``gated`` and NOTHING is published to
    a social network. Never fakes a post confirmation."""
    seed_queue(ledger=ledger, render_dir=render_dir)
    post = post_next(max_posts=1, channel="tiktok")
    gated = not social_post.creds_available("tiktok")
    auth_rejected = bool(post.get("auth_error"))
    if gated:
        note = "GATED — IG/TikTok creds required; reels queued + recorded, nothing posted"
    elif auth_rejected:
        note = ("CREDS REJECTED — tiktok.json token returned 401/403; reels queued + kept "
                "pending, nothing posted until the token is refreshed")
    else:
        note = "tiktok creds present — posting live reels"
    tally = {
        "tiktok": post,
        "gated": gated,
        "auth_rejected": auth_rejected,
        "note": note,
    }
    log.info("reel_queue: %s", tally)
    return tally

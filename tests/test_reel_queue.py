"""reel_queue — the DB-backed queue + posting paths that test_marketer_wire.py
doesn't reach.

test_marketer_wire.py proves the ledger/bus wiring and the human gate (no creds
→ nothing posted). These tests cover the rest of the surface against fakes (no
live Postgres, no network): seed_queue's INSERT, pending() ordering, _mark's
UPDATE, post_next's SUCCESS and ERROR branches, _move_to_posted's file move,
status()'s rollup, and media_ref_for's never-twice key.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from utah.product import reel_queue
from utah.integrations import social_post


# ── a minimal Postgres stand-in matching reel_queue's `with _pool().connection() as c` ──

class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Records every (sql, params) and replays canned fetch rows."""

    def __init__(self, fetch_rows=None):
        self.executed: list[tuple[str, object]] = []
        self._fetch_rows = fetch_rows or []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return FakeCursor(self._fetch_rows)


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


def _use_pool(monkeypatch, conn):
    monkeypatch.setattr(reel_queue, "_pool", lambda: FakePool(conn))


# ── media_ref_for: the public URL = the ledger's never-twice key ──────────────────────

def test_media_ref_for_is_public_url_keyed_on_filename():
    ref = reel_queue.media_ref_for({"mp4_path": "/x/y/tabby_house.mp4"}, "tiktok")
    assert ref == f"{reel_queue.PUBLIC_BASE}/tabby_house.mp4"
    # Same file → same key regardless of where it sits on disk (idempotency anchor).
    assert reel_queue.media_ref_for({"mp4_path": "/other/tabby_house.mp4"}) == ref


# ── _scan_reels: pairs .mp4 with its .txt caption, tolerates a missing caption ────────

def test_scan_reels_pairs_caption_and_tolerates_missing(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"\x00")
    (tmp_path / "a.txt").write_text("caption A")
    (tmp_path / "b.mp4").write_bytes(b"\x00")  # no .txt
    reels = reel_queue._scan_reels(tmp_path)
    by_id = {r["id"]: r for r in reels}
    assert by_id["a"]["caption"] == "caption A"
    assert by_id["b"]["caption"] == ""


def test_scan_reels_missing_dir_is_empty():
    assert reel_queue._scan_reels(Path("/no/such/render/dir")) == []


# ── seed_queue: registers each discovered reel as a pending row (idempotent INSERT) ────

def test_seed_queue_inserts_pending_rows(tmp_path, monkeypatch):
    (tmp_path / "r1.mp4").write_bytes(b"\x00")
    (tmp_path / "r1.txt").write_text("cap1")
    (tmp_path / "r2.mp4").write_bytes(b"\x00")
    conn = FakeConn()
    _use_pool(monkeypatch, conn)
    monkeypatch.setattr(reel_queue, "_ensure_schema", lambda: None)

    n = reel_queue.seed_queue(render_dir=tmp_path)  # no ledger → pure DB path

    assert n == 2
    inserts = [e for e in conn.executed if "INSERT INTO reel_queue" in e[0]]
    assert len(inserts) == 2
    assert all("ON CONFLICT (id) DO NOTHING" in sql for sql, _ in inserts)
    ids = {params[0] for _, params in inserts}
    assert ids == {"r1", "r2"}


# ── pending: parses rows into dicts in queue order ────────────────────────────────────

def test_pending_returns_dicts(monkeypatch):
    rows = [("r1", "/d/r1.mp4", "cap1", "tiktok"), ("r2", "/d/r2.mp4", "", "tiktok")]
    _use_pool(monkeypatch, FakeConn(rows))
    monkeypatch.setattr(reel_queue, "_ensure_schema", lambda: None)
    out = reel_queue.pending()
    assert out == [
        {"id": "r1", "mp4_path": "/d/r1.mp4", "caption": "cap1", "channel": "tiktok"},
        {"id": "r2", "mp4_path": "/d/r2.mp4", "caption": "", "channel": "tiktok"},
    ]


# ── _mark: issues an UPDATE carrying status/post_id/error ──────────────────────────────

def test_mark_issues_update(monkeypatch):
    conn = FakeConn()
    _use_pool(monkeypatch, conn)
    reel_queue._mark("r1", "posted", post_id="pub_123")
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert "UPDATE reel_queue SET status=" in sql
    assert params == ("posted", "pub_123", None, "posted", "r1")


# ── post_next: SUCCESS branch marks posted + moves the file ───────────────────────────

def test_post_next_success_marks_and_moves(monkeypatch):
    monkeypatch.setattr(social_post, "creds_available", lambda ch: True)
    monkeypatch.setattr(reel_queue, "pending",
                        lambda: [{"id": "r1", "mp4_path": "/d/r1.mp4", "caption": "cap"}])
    monkeypatch.setattr(social_post, "post",
                        lambda cap, *, media_ref, channel: {"posted": True, "id": "pub_9"})
    marked, moved = [], []
    monkeypatch.setattr(reel_queue, "_mark",
                        lambda rid, status, **k: marked.append((rid, status, k.get("post_id"))))
    monkeypatch.setattr(reel_queue, "_move_to_posted", lambda p: moved.append(p))

    tally = reel_queue.post_next(max_posts=1, channel="tiktok")

    assert tally == {"posted": 1, "gated": 0, "error": 0, "skipped": 0, "auth_error": 0}
    assert marked == [("r1", "posted", "pub_9")]
    assert moved == ["/d/r1.mp4"]


# ── post_next: ERROR branch marks error, never moves, never raises ────────────────────

def test_post_next_error_marks_error(monkeypatch):
    monkeypatch.setattr(social_post, "creds_available", lambda ch: True)
    monkeypatch.setattr(reel_queue, "pending",
                        lambda: [{"id": "r1", "mp4_path": "/d/r1.mp4", "caption": "cap"}])
    monkeypatch.setattr(social_post, "post",
                        lambda cap, *, media_ref, channel: {"posted": False, "error": "tiktok 401"})
    marked, moved = [], []
    monkeypatch.setattr(reel_queue, "_mark",
                        lambda rid, status, **k: marked.append((rid, status, k.get("error"))))
    monkeypatch.setattr(reel_queue, "_move_to_posted", lambda p: moved.append(p))

    tally = reel_queue.post_next(max_posts=1, channel="tiktok")

    assert tally["error"] == 1 and tally["posted"] == 0
    assert marked == [("r1", "error", "tiktok 401")]
    assert moved == []  # a failed post must not move the file out of the queue


# ── post_next: gate is honest — no creds → gated tally counts the backlog, posts nothing

def test_post_next_gated_counts_backlog(monkeypatch):
    monkeypatch.setattr(social_post, "creds_available", lambda ch: False)
    monkeypatch.setattr(reel_queue, "pending",
                        lambda: [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    posted = []
    monkeypatch.setattr(social_post, "post",
                        lambda *a, **k: posted.append(1) or {"posted": True})
    tally = reel_queue.post_next(channel="tiktok")
    assert tally["gated"] == 3 and tally["posted"] == 0
    assert posted == []  # gate shut → publisher never called


# ── _move_to_posted: moves the mp4 AND its caption sidecar into posted/ ────────────────

def test_move_to_posted_moves_pair(tmp_path, monkeypatch):
    src_mp4 = tmp_path / "done.mp4"
    src_txt = tmp_path / "done.txt"
    src_mp4.write_bytes(b"\x00vid")
    src_txt.write_text("caption")
    posted_dir = tmp_path / "posted"
    monkeypatch.setattr(reel_queue, "POSTED_DIR", posted_dir)

    reel_queue._move_to_posted(str(src_mp4))

    assert not src_mp4.exists() and not src_txt.exists()
    assert (posted_dir / "done.mp4").read_bytes() == b"\x00vid"
    assert (posted_dir / "done.txt").read_text() == "caption"


def test_move_to_posted_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(reel_queue, "POSTED_DIR", tmp_path / "posted")
    reel_queue._move_to_posted(str(tmp_path / "ghost.mp4"))  # must not raise


# ── post_next: a 401/403 is a GLOBAL auth fault — never poison the reel, stop the run ──

def test_post_next_auth_error_leaves_reel_pending(monkeypatch):
    from utah import failures
    from tests.fakes import FakeFailureStore

    store = FakeFailureStore()
    failures.set_store(store)
    try:
        monkeypatch.setattr(social_post, "creds_available", lambda ch: True)
        monkeypatch.setattr(reel_queue, "pending", lambda: [
            {"id": "r1", "mp4_path": "/d/r1.mp4", "caption": "cap"},
            {"id": "r2", "mp4_path": "/d/r2.mp4", "caption": "cap"},
        ])
        monkeypatch.setattr(social_post, "post", lambda cap, *, media_ref, channel: {
            "posted": False, "auth_error": True, "error": "HTTP Error 401: Unauthorized"})
        marked, moved = [], []
        monkeypatch.setattr(reel_queue, "_mark", lambda *a, **k: marked.append(a))
        monkeypatch.setattr(reel_queue, "_move_to_posted", lambda p: moved.append(p))

        tally = reel_queue.post_next(max_posts=5, channel="tiktok")

        assert tally["auth_error"] == 1 and tally["error"] == 0 and tally["posted"] == 0
        assert marked == []   # reel NOT marked error — stays pending for retry
        assert moved == []
        # The run STOPS after the first 401 (every reel would hit the same token).
        assert any(row[2] == "auth_invalid" for row in store.rows)
    finally:
        failures.set_store(None)


# ── requeue_errors: recovery path — reset errored reels back to pending ────────────────

def test_requeue_errors_resets_errored(monkeypatch):
    conn = FakeConn([("e1",), ("e2",), ("e3",)])  # RETURNING id rows
    _use_pool(monkeypatch, conn)
    monkeypatch.setattr(reel_queue, "_ensure_schema", lambda: None)
    n = reel_queue.requeue_errors()
    assert n == 3
    sql, _ = conn.executed[-1]
    assert "UPDATE reel_queue SET status='pending'" in sql and "WHERE status='error'" in sql


def test_requeue_errors_none_is_zero(monkeypatch):
    _use_pool(monkeypatch, FakeConn([]))
    monkeypatch.setattr(reel_queue, "_ensure_schema", lambda: None)
    assert reel_queue.requeue_errors() == 0


# ── status: rolls grouped counts up and reports per-channel readiness ──────────────────

def test_status_rolls_up_counts_and_readiness(monkeypatch):
    _use_pool(monkeypatch, FakeConn([("pending", 4), ("posted", 2), ("error", 1)]))
    monkeypatch.setattr(reel_queue, "_ensure_schema", lambda: None)
    monkeypatch.setattr(social_post, "creds_available",
                        lambda ch: ch == "tiktok")
    s = reel_queue.status()
    assert s["pending"] == 4 and s["posted"] == 2 and s["error"] == 1
    assert s["tiktok_ready"] is True and s["instagram_ready"] is False

"""Marketing tool stress + adversarial-input tests.

Hammers the marketing surface the way real lead data does: business names with
ampersands and quotes ("Mom & Pop's", 'Joe "Big" Tony'), HTML metacharacters,
unicode, control chars, absurd lengths, high concurrency, and high volume. The
tool must never crash, never fabricate, never produce broken/injectable HTML,
and must stay idempotent under repeat/parallel load.
"""
from __future__ import annotations

import html as _html
import threading

import pytest

from utah import failures
from utah.product import marketer, sitegen, reel_queue
from utah.daemon.bus import Bus
from tests.fakes import FakeFailureStore


# Adversarial business names — every one of these is a plausible real lead.
ADVERSARIAL_NAMES = [
    "Mom & Pop's Diner",
    'Joe "Big Tony" Pizza',
    "Smith & Sons <Plumbing>",
    "Café Niño — Español",
    "A/B Auto & Body",
    "<script>alert(1)</script>",
    "Bob's \"100%\" Honest Cars & Trucks",
    "日本料理 すし",
    "Tom\t&\nJerry  Cleaning",
    "'; DROP TABLE leads;--",
    "♥ Heartfelt Flowers ♥",
    "x" * 5000,
    "",
    "   ",
]


# ── sitegen.render: customer-facing HTML must be SAFE for any lead name ────────────────

@pytest.mark.parametrize("name", ADVERSARIAL_NAMES)
def test_render_escapes_user_values_no_broken_html(name):
    """A lead name with &, <, >, or quotes must NOT break or inject the preview."""
    out = sitegen.render({
        "name": name, "kind": "restaurant", "region": "Augusta GA <x>",
        "contact": {"address": '12 "Main" & 3rd <st>', "phone": "706-555-0100"},
    })
    # No unescaped script tag from the name can survive into the document.
    assert "<script>alert(1)</script>" not in out
    # The escaped form is present instead when the name carried one.
    if name == "<script>alert(1)</script>":
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    # The meta description attribute stays intact: exactly two double-quotes on that line
    # (the attribute's own), i.e. user quotes were escaped, not leaked into the markup.
    desc_line = next(ln for ln in out.splitlines() if 'name="description"' in ln)
    assert desc_line.count('"') == 4  # name="..." content="..."  → 4 delimiter quotes


@pytest.mark.parametrize("name", ADVERSARIAL_NAMES)
def test_render_never_raises_and_is_closed_html(name):
    out = sitegen.render({"name": name, "kind": "", "region": "", "contact": {}})
    assert out.startswith("<!doctype html>")
    assert out.rstrip().endswith("</html>")
    # Roughly balanced angle brackets — a leaked raw '<' from input would skew this hard.
    assert out.count("<") == out.count(">")


def test_render_ampersand_is_entity_encoded():
    out = sitegen.render({"name": "Mom & Pop's", "kind": "restaurant",
                          "contact": {}, "region": ""})
    assert "Mom &amp; Pop&#x27;s" in out or "Mom &amp; Pop's" in out
    assert "Mom & Pop" not in out  # the raw, invalid-HTML form must be gone


def test_slug_always_safe_and_nonempty():
    for name in ADVERSARIAL_NAMES:
        s = sitegen.slug(name)
        assert s and all(c.isalnum() or c == "-" for c in s)


# ── compose_caption: bounded + crash-proof on any subject ─────────────────────────────

@pytest.mark.parametrize("name", ADVERSARIAL_NAMES)
def test_compose_caption_bounded_and_safe(name):
    cap = marketer.compose_caption({"name": name, "kind": "café & bar"})
    assert isinstance(cap, str)
    assert len(cap) <= marketer.MAX_CAPTION


# ── reel_queue.media_ref_for: stable never-twice key under odd filenames ───────────────

@pytest.mark.parametrize("fn", ["a b.mp4", "wéird.mp4", "x" * 200 + ".mp4", "a&b.mp4"])
def test_media_ref_for_stable(fn):
    ref = reel_queue.media_ref_for({"mp4_path": f"/d/{fn}"})
    assert ref.endswith(fn) and ref.startswith(reel_queue.PUBLIC_BASE)


# ── concurrency: 64 parallel posts via injected publisher, no lost/duplicated work ─────

def test_post_is_threadsafe_under_concurrency():
    failures.set_store(FakeFailureStore())
    results: list[dict] = []
    lock = threading.Lock()
    N = 64

    def worker(i):
        r = marketer.post(f"cap{i}", media_ref=f"r{i}.mp4", channel="instagram",
                          publish_fn=lambda c, m, ch: f"id-{m}")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == N
    assert all(r["posted"] and not r["gated"] for r in results)
    assert len({r["id"] for r in results}) == N  # every post got its own id


# ── volume + idempotency: re-scanning 2000 rendered reels lights the deck exactly once ─

class _UniqLedger:
    """marketer_posts UNIQUE(channel, media_ref) ON CONFLICT DO NOTHING. Counts publishes
    directly — the real idempotency contract is "one publish per NEW row, zero on re-scan",
    independent of the live Bus subscriber's bounded buffer (which drops when undrained)."""

    def __init__(self, bus: Bus):
        self.bus = bus
        self.rows: list[tuple] = []
        self._seen: set[tuple] = set()
        self.publishes = 0

    def record_post(self, channel, caption, media_ref, subject="", status="posted",
                    post_id=None) -> bool:
        key = (channel, media_ref)
        if key in self._seen:
            return False
        self._seen.add(key)
        self.rows.append(key)
        self.bus.publish("marketer", {"channel": channel, "subject": subject})
        self.publishes += 1
        return True


def test_record_queued_high_volume_idempotent():
    bus = Bus()
    lg = _UniqLedger(bus)
    reels = [{"id": f"reel_{i}", "mp4_path": f"/d/reel_{i}.mp4", "caption": f"c{i}"}
             for i in range(2000)]

    first = reel_queue.record_queued(reels, lg)
    publishes_after_first = lg.publishes
    second = reel_queue.record_queued(reels, lg)  # exact re-scan

    assert len(first) == 2000
    assert second == []                     # nothing new on the second pass
    assert len(lg.rows) == 2000             # one ledger row per reel, ever
    assert publishes_after_first == 2000    # one deck event per NEW row
    assert lg.publishes == 2000             # re-scan published nothing — idempotent


# ── run_scheduled resilience: any pick failure degrades, never raises ──────────────────

@pytest.mark.parametrize("exc", [
    RuntimeError("pg down"),
    TimeoutError("statement timeout"),
    ConnectionError("no route to host"),
    ValueError("garbage row"),
])
def test_run_scheduled_degrades_on_any_pick_failure(exc):
    failures.set_store(FakeFailureStore())

    def boom():
        raise exc

    out = marketer.run_scheduled(
        foundation_gate=lambda name: None,  # gate open
        pick_fn=boom,
        ledger=object(),  # never touched: pick fails first
    )
    assert out["status"] == "store_unreachable"
    assert "error" in out


def test_run_scheduled_no_fresh_lead_is_clean():
    failures.set_store(FakeFailureStore())
    out = marketer.run_scheduled(foundation_gate=lambda name: None,
                                 pick_fn=lambda: None, ledger=object())
    assert out["status"] == "no_fresh_lead"

"""MicLiveness is fed from the CoreAudio callback thread while the monitor thread
polls — the episode bookkeeping (alerted flag + pending queue) must be coherent
under that concurrency: every deaf episode that ends pairs with EXACTLY one
recovered event, no event is duplicated or dropped, and the hammer never raises."""
from __future__ import annotations

import threading

from utah.voice.liveness import MicLiveness


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.t

    def advance(self, dt: float) -> None:
        with self._lock:
            self.t += dt


def test_episodes_pair_deaf_with_exactly_one_recovered():
    """Many sequential episodes: deaf/recovered alternate strictly — the flag and
    the queue can never double-report an episode edge."""
    clk = Clock()
    ml = MicLiveness(threshold=0.001, alert_after_s=30.0, cooldown_s=0.0, now=clk)
    events = []
    for _ in range(25):
        clk.advance(31.0)
        events += ml.poll()
        ml.feed_frame(0.05)       # recover
        ml.feed_frame(0.05)       # a second loud frame must NOT queue a 2nd recovered
        events += ml.poll()
    kinds = [e["event"] for e in events]
    assert kinds == ["deaf", "recovered"] * 25


def test_concurrent_feed_and_poll_is_safe_and_coherent():
    """Hammer feed_frame/feed_muted from two threads while a third polls: no
    exception, every event is well-formed, and recovered never outnumbers deaf."""
    clk = Clock()
    ml = MicLiveness(threshold=0.001, alert_after_s=0.5, cooldown_s=0.0, now=clk)
    stop = threading.Event()
    errors: list[BaseException] = []
    events: list[dict] = []

    def feeder(loud: bool):
        try:
            while not stop.is_set():
                ml.feed_frame(0.05 if loud else 0.0)
                ml.feed_muted() if not loud else None
        except BaseException as exc:  # noqa: BLE001 — the test asserts none happen
            errors.append(exc)

    def poller():
        try:
            while not stop.is_set():
                clk.advance(0.3)
                events.extend(ml.poll())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=feeder, args=(True,)),
               threading.Thread(target=feeder, args=(False,)),
               threading.Thread(target=poller)]
    for t in threads:
        t.start()
    threading.Event().wait(0.5)
    stop.set()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert errors == []
    deaf = sum(1 for e in events if e["event"] == "deaf")
    rec = sum(1 for e in events if e["event"] == "recovered")
    assert rec <= deaf                     # an episode can only recover after it fired
    for e in events:
        assert e["event"] in ("deaf", "recovered")
        assert all(isinstance(v, (int, float, str)) for v in e.values())


def test_negative_rms_is_quiet_not_audio():
    """A negative RMS (impossible from a real meter, possible from a buggy caller)
    must never reset the quiet clock."""
    clk = Clock()
    ml = MicLiveness(threshold=0.001, alert_after_s=30.0, cooldown_s=0.0, now=clk)
    clk.advance(31.0)
    ml.feed_frame(-1.0)
    events = ml.poll()
    assert len(events) == 1 and events[0]["event"] == "deaf"

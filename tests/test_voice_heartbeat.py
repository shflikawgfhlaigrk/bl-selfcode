"""The voice liveness heartbeat decision — keeps a long turn (or a slow diagnostic monitor)
from looking 'wedged' to the supervisor, while a truly frozen loop still gets restarted.

WHY (Michael, 2026-06-18): "make sure the voice fully functions and doesn't break per turn."
The supervisor kills voice when its heartbeat goes stale > VOICE_DEAF_RESTART_S. The heartbeat
was written only by the diagnostic _monitor thread, whose body does blocking work (Postgres
failures.record, sd.query_devices) — if that stalls or the thread dies, a HEALTHY loop gets
false-wedged. A dedicated heartbeat thread driven by this pure decision fixes that.
"""
from __future__ import annotations

from utah.voice import loop


def test_beats_while_busy_in_a_turn_regardless_of_callback():
    # Mic is CLOSED during brain+TTS (no callback frames), so last_alive is old — but the
    # loop is legitimately working. It must keep beating so a long turn never false-wedges.
    assert loop._should_heartbeat(now=1000.0, last_alive=0.0, busy=True,
                                  alive_window_s=20.0) is True


def test_beats_when_callback_delivered_a_frame_recently():
    # Normal/quiet listening: the callback fires every frame (even on silence or device
    # zeros — deafness is reported via the `deaf` flag, not by withholding the heartbeat).
    assert loop._should_heartbeat(now=1000.0, last_alive=997.0, busy=False,
                                  alive_window_s=20.0) is True


def test_stops_beating_when_loop_is_frozen():
    # A frozen CoreAudio handle stops calling the callback entirely AND we're not in a turn:
    # last_alive ages past the window → stop beating → supervisor's stale-heartbeat probe
    # correctly restarts the loop. This is the ONE case we must NOT mask.
    assert loop._should_heartbeat(now=1000.0, last_alive=900.0, busy=False,
                                  alive_window_s=20.0) is False


def test_window_boundary_is_inclusive_of_recent():
    assert loop._should_heartbeat(now=1000.0, last_alive=1000.0 - 19.9, busy=False,
                                  alive_window_s=20.0) is True
    assert loop._should_heartbeat(now=1000.0, last_alive=1000.0 - 20.1, busy=False,
                                  alive_window_s=20.0) is False


def test_constants_present_and_sane():
    # The heartbeat must fire several times inside the supervisor's restart threshold, and the
    # alive window must exceed the monitor cadence so quiet listening never trips it.
    assert loop.HEARTBEAT_S > 0
    assert loop.HEARTBEAT_S < 75.0  # several beats within VOICE_DEAF_RESTART_S (75s)
    assert loop.ALIVE_WINDOW_S > loop.MONITOR_S

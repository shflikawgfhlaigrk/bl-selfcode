"""Doc-3/8 WIN binary data plane — zero-copy numeric frames over the data socket.

The control plane is JSON (human-debuggable); the DATA plane is packed-struct
binary, decoded **zero-copy** via ``np.frombuffer`` (no per-element parse/alloc,
exact floats incl NaN/Inf) and **fail-loud** (a malformed frame raises — it never
silently degrades to JSON, Ace's cbor2-inert failure). Transport reuses the same
length-prefixed framing as the control socket (``KIND_BINARY``).
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import anyio
import numpy as np
import pytest

from utah import win
from utah.daemon import frame


# --- codec: zero-copy, exact, fail-loud -------------------------------------

def test_roundtrip_float64_exact():
    arr = np.array([1.5, -2.0, 3.25, 1e308], dtype=np.float64)
    name, out = win.decode_array(win.encode_array(arr, name="ticks"))
    assert name == "ticks"
    assert out.dtype == np.float64
    assert np.array_equal(out, arr)


def test_roundtrip_preserves_nan_inf():
    # JSON loses NaN/Inf; the binary plane must keep them bit-exact.
    arr = np.array([np.nan, np.inf, -np.inf, 0.0], dtype=np.float64)
    _, out = win.decode_array(win.encode_array(arr))
    assert np.isnan(out[0]) and np.isposinf(out[1]) and np.isneginf(out[2])


def test_roundtrip_2d_ticks_shape():
    arr = np.arange(30, dtype=np.float64).reshape(5, 6)  # ts,o,h,l,c,v
    _, out = win.decode_array(win.encode_array(arr, name="ohlcv"))
    assert out.shape == (5, 6) and np.array_equal(out, arr)


def test_roundtrip_int16_pcm_and_float32():
    for dt in (np.int16, np.float32):
        arr = (np.arange(8) - 4).astype(dt)
        _, out = win.decode_array(win.encode_array(arr))
        assert out.dtype == dt and np.array_equal(out, arr)


def test_decode_is_zero_copy_view_of_the_buffer():
    raw = bytearray(win.encode_array(np.array([10.0, 20.0, 30.0])))
    _, out = win.decode_array(raw)
    # The decoded array must VIEW the input buffer, not copy it (the doc-8 win).
    assert np.shares_memory(out, np.frombuffer(raw, dtype=np.uint8))


def test_bad_magic_is_fail_loud():
    with pytest.raises(win.WinCodecError):
        win.decode_array(b"NOPEclearly not a win frame")


def test_truncated_payload_is_fail_loud():
    buf = win.encode_array(np.arange(100, dtype=np.float64))
    with pytest.raises(win.WinCodecError):
        win.decode_array(buf[:-40])   # body cut short → raise, never half-return


def test_decode_never_returns_json_on_bad_input():
    # Fail-loud means raise — not a silent dict/JSON fallback (Ace's cbor2 sin).
    for bad in (b"", b"{}", b'{"x":1}', b"WIN9bogus"):
        with pytest.raises(win.WinCodecError):
            win.decode_array(bad)


# --- live data server on a real unix socket ---------------------------------

@pytest.fixture
def sock():
    d = tempfile.mkdtemp(prefix="utd", dir="/tmp")
    path = os.path.join(d, "d.sock")
    try:
        yield path
    finally:
        for fn in (lambda: os.unlink(path), lambda: os.rmdir(d)):
            try:
                fn()
            except OSError:
                pass


def test_data_server_roundtrips_stats_over_real_socket(sock):
    from utah.daemon import data_client
    from utah.daemon.data_server import DataServer

    arr = np.array([2.0, 4.0, 6.0, 8.0], dtype=np.float64)
    got = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock))
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            got["stats"] = await data_client.send_array(arr, name="probe", sock_path=sock)
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    stats = got["stats"]              # [count, min, max, mean, sum]
    assert stats[0] == 4 and stats[1] == 2.0 and stats[2] == 8.0
    assert stats[3] == 5.0 and stats[4] == 20.0


def test_data_server_is_fail_loud_on_garbage_frame(sock):
    from utah.daemon.data_server import DataServer

    err = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock))
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            stream = await anyio.connect_unix(sock)
            # send a KIND_BINARY frame that is NOT a valid WIN payload
            await frame.write_frame(stream, frame.KIND_BINARY, b"garbage-not-win")
            kind, payload = await frame.read_frame(stream)
            err["kind"], err["payload"] = kind, payload
            await stream.aclose()
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    # Fail-loud: the server replies with an explicit JSON error frame, never a
    # silent/empty binary "success".
    assert err["kind"] == frame.KIND_JSON
    assert b"error" in err["payload"].lower()

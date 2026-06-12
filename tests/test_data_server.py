"""Data-server resilience on a REAL unix socket (complements test_win.py).

test_win.py proves the happy roundtrip + garbage-frame fail-loud. These pin
the survival contracts: a crashing handler answers a typed JSON error and the
CONNECTION KEEPS SERVING; control-plane frames are ignored on the data socket
(not answered, not fatal); failures are documented to the audit store; an
unauthorized peer is dropped without a byte of reply; a stale socket file is
replaced on bind.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import anyio
import numpy as np
import pytest

from utah import failures, win
from utah.daemon import data_client, frame
from utah.daemon.data_server import DataServer, stats_handler
from utah.daemon.peercred import PeerAuthError


@pytest.fixture
def sock():
    d = tempfile.mkdtemp(prefix="utds", dir="/tmp")
    path = os.path.join(d, "d.sock")
    try:
        yield path
    finally:
        for fn in (lambda: os.unlink(path), lambda: os.rmdir(d)):
            try:
                fn()
            except OSError:
                pass


def test_handler_crash_is_typed_error_and_connection_survives(sock):
    calls = {"n": 0}

    def flaky(payload: bytes) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")  # a generic handler crash, not a codec error
        return stats_handler(payload)

    got = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock), handler=flaky)
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            stream = await anyio.connect_unix(sock)
            async with stream:
                wire = win.encode_array(np.array([1.0, 3.0]), name="t")
                # 1st frame: handler crashes → explicit JSON error frame.
                await frame.write_frame(stream, frame.KIND_BINARY, wire)
                got["k1"], got["p1"] = await frame.read_frame(stream)
                # 2nd frame on the SAME connection: still served.
                await frame.write_frame(stream, frame.KIND_BINARY, wire)
                got["k2"], got["p2"] = await frame.read_frame(stream)
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert got["k1"] == frame.KIND_JSON and b"error" in got["p1"].lower()
    assert got["k2"] == frame.KIND_BINARY
    _, stats = win.decode_array(got["p2"])
    assert stats[0] == 2 and stats[1] == 1.0 and stats[2] == 3.0
    # The crash was documented to the audit store, never swallowed.
    kinds = [(r.source, r.kind) for r in failures.recent(10)]
    assert ("data", "handler_crash") in kinds


def test_json_frames_are_skipped_not_answered(sock):
    got = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock))
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            stream = await anyio.connect_unix(sock)
            async with stream:
                # A control-plane frame on the data socket: ignored, not fatal.
                await frame.write_frame(stream, frame.KIND_JSON, b'{"method":"ping"}')
                # The next binary frame is answered — proving the JSON frame got
                # no reply of its own (the first reply belongs to this frame).
                wire = win.encode_array(np.array([5.0]), name="t")
                await frame.write_frame(stream, frame.KIND_BINARY, wire)
                got["kind"], got["payload"] = await frame.read_frame(stream)
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert got["kind"] == frame.KIND_BINARY
    _, stats = win.decode_array(got["payload"])
    assert stats[0] == 1 and stats[4] == 5.0


def test_decode_failure_is_documented_to_audit(sock):
    async def scenario():
        server = DataServer(sock_path=Path(sock))
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            stream = await anyio.connect_unix(sock)
            async with stream:
                await frame.write_frame(stream, frame.KIND_BINARY, b"not-win")
                await frame.read_frame(stream)  # the explicit error reply
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    kinds = [(r.source, r.kind) for r in failures.recent(10)]
    assert ("data", "decode_failed") in kinds


def test_unauthorized_peer_is_dropped_without_reply(sock, monkeypatch):
    def deny(raw):
        raise PeerAuthError("peer uid 999 != owner — denied")

    monkeypatch.setattr("utah.daemon.data_server.authorize", deny)
    got = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock))
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            stream = await anyio.connect_unix(sock)
            try:
                await frame.write_frame(
                    stream, frame.KIND_BINARY, win.encode_array(np.array([1.0]))
                )
                with anyio.fail_after(2.0):
                    await frame.read_frame(stream)
                got["served"] = True  # pragma: no cover — must not happen
            except (frame.FrameError, anyio.BrokenResourceError,
                    anyio.ClosedResourceError):
                got["served"] = False  # connection closed: fail-closed, zero bytes
            finally:
                await stream.aclose()
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert got["served"] is False


def test_stale_socket_file_is_replaced_on_bind(sock):
    Path(sock).write_bytes(b"")  # a leftover path from a dead process
    got = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock))
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)  # must bind despite the stale file
            got["stats"] = await data_client.send_array(
                np.array([2.0, 4.0]), sock_path=sock
            )
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert got["stats"][0] == 2 and got["stats"][4] == 6.0


def test_socket_is_owner_only_mode(sock):
    got = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock))
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            got["mode"] = os.stat(sock).st_mode & 0o777
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert got["mode"] == 0o600


def test_oversized_frame_ends_connection_but_server_keeps_serving(sock):
    """A declared length over max_frame must drop THAT connection before any
    payload allocation — and the server must keep accepting new connections."""
    got = {}

    async def scenario():
        server = DataServer(sock_path=Path(sock), max_frame=1024)
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            stream = await anyio.connect_unix(sock)
            try:
                # Header promising 1 MiB on a 1 KiB-cap server.
                await stream.send(frame._HEADER.pack(frame.KIND_BINARY, 1 << 20))
                with anyio.fail_after(2.0):
                    await frame.read_frame(stream)
                got["dropped"] = False  # pragma: no cover — must not happen
            except (frame.FrameError, anyio.BrokenResourceError,
                    anyio.ClosedResourceError):
                got["dropped"] = True
            finally:
                await stream.aclose()
            # A fresh connection is still served after the bad peer was dropped.
            fresh = await anyio.connect_unix(sock)
            async with fresh:
                wire = win.encode_array(np.array([7.0]), name="t")
                await frame.write_frame(fresh, frame.KIND_BINARY, wire)
                got["kind"], got["payload"] = await frame.read_frame(fresh)
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert got["dropped"] is True
    assert got["kind"] == frame.KIND_BINARY
    _, stats = win.decode_array(got["payload"])
    assert stats[0] == 1 and stats[4] == 7.0


def test_stats_handler_empty_array_returns_zeros_not_crash():
    out = stats_handler(win.encode_array(np.array([], dtype=np.float64)))
    _, stats = win.decode_array(out)
    assert list(stats) == [0.0, 0.0, 0.0, 0.0, 0.0]


def test_stats_handler_flattens_2d_and_keeps_exact_floats():
    arr = np.array([[1.0, 2.0], [3.0, 4.0]])
    _, stats = win.decode_array(stats_handler(win.encode_array(arr)))
    assert stats[0] == 4 and stats[1] == 1.0 and stats[2] == 4.0
    assert stats[3] == 2.5 and stats[4] == 10.0

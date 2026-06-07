"""Data-plane client — send a numpy array over the WIN binary socket, get the
reply array back (docs 3 & 8). Mirror of :mod:`utah.daemon.client`, but binary.

Fail-loud: if the server answers with a JSON error frame (a malformed payload),
this raises :class:`~utah.win.WinCodecError` rather than returning garbage.
"""
from __future__ import annotations

import anyio

from utah import win
from utah.daemon import frame, runtime


async def send_array(arr, name: str = "", *, sock_path=runtime.DATA_SOCK,
                     timeout: float = 30.0):
    """Round-trip *arr* through the data plane → the server's reply ndarray."""
    stream = await anyio.connect_unix(str(sock_path))
    async with stream:
        with anyio.fail_after(timeout):
            await frame.write_frame(stream, frame.KIND_BINARY, win.encode_array(arr, name))
            kind, payload = await frame.read_frame(stream)
    if kind == frame.KIND_BINARY:
        _name, out = win.decode_array(payload)
        return out
    if kind == frame.KIND_JSON:                  # explicit fail-loud error from the server
        raise win.WinCodecError("data server error: " + payload.decode("utf-8", "replace"))
    raise win.WinCodecError(f"unexpected data reply kind {kind}")


def send_array_sync(arr, name: str = "", **kwargs):
    """Blocking convenience (CLI / scripts / benchmarks)."""
    return anyio.run(lambda: send_array(arr, name, **kwargs))

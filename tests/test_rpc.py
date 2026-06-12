"""JSON-RPC envelope edges: strict parse (spec codes, never a traceback to the
peer), notification semantics, and error objects that survive hostile payloads
— an unencodable `data` must degrade to a valid error frame, because the error
path is the one place the daemon cannot afford a second failure."""
from __future__ import annotations

import json

import pytest

from utah.daemon import rpc


# -- parse: strictness -------------------------------------------------------------

def test_parse_rejects_an_empty_method():
    with pytest.raises(rpc.RpcError) as exc:
        rpc.parse(b'{"jsonrpc":"2.0","method":"","id":1}')
    assert exc.value.code == rpc.INVALID_REQUEST


def test_parse_rejects_a_batch_array():
    """msgspec decodes a single Request struct — a JSON-RPC batch (array) is a
    typed INVALID_REQUEST, not a crash or a silent first-element pick."""
    with pytest.raises(rpc.RpcError) as exc:
        rpc.parse(b'[{"jsonrpc":"2.0","method":"ping","id":1}]')
    assert exc.value.code in (rpc.INVALID_REQUEST, rpc.PARSE_ERROR)


def test_parse_rejects_wrong_param_type():
    """params must be an object or array per spec — a scalar is refused."""
    with pytest.raises(rpc.RpcError):
        rpc.parse(b'{"jsonrpc":"2.0","method":"x","params":5,"id":1}')


def test_parse_accepts_string_ids_and_list_params():
    req = rpc.parse(b'{"jsonrpc":"2.0","method":"m","params":[1,2],"id":"abc"}')
    assert req.id == "abc"
    assert req.params == [1, 2]
    assert req.is_notification is False


def test_parse_missing_id_is_a_notification():
    note = rpc.parse(b'{"jsonrpc":"2.0","method":"event"}')
    assert note.is_notification is True


def test_parse_error_carries_the_spec_code_and_never_a_traceback():
    with pytest.raises(rpc.RpcError) as exc:
        rpc.parse(b"\xff\xfe garbage")
    assert exc.value.code == rpc.PARSE_ERROR
    assert "Traceback" not in str(exc.value)


# -- response envelopes -------------------------------------------------------------

def test_ok_envelope_with_string_id():
    body = json.loads(rpc.ok("req-9", {"x": 1}))
    assert body == {"jsonrpc": "2.0", "id": "req-9", "result": {"x": 1}}


def test_notify_envelope_has_no_id():
    body = json.loads(rpc.notify("event", {"channel": "fires"}))
    assert body == {"jsonrpc": "2.0", "method": "event", "params": {"channel": "fires"}}
    assert "id" not in body


def test_err_omits_data_when_none():
    body = json.loads(rpc.err(3, rpc.UNAVAILABLE, "memory down"))
    assert body["error"] == {"code": rpc.UNAVAILABLE, "message": "memory down"}


def test_err_with_unencodable_data_degrades_to_a_valid_error_object():
    """The error path must not have its own error path: if a handler attached
    un-JSON-able data, the peer still gets code+message, never a crash."""
    body = json.loads(rpc.err(5, rpc.INTERNAL_ERROR, "boom", data=object()))
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 5
    assert body["error"]["code"] == rpc.INTERNAL_ERROR
    assert body["error"]["message"] == "boom"
    assert "data" not in body["error"]


def test_all_utah_error_codes_live_in_the_reserved_application_block():
    """JSON-RPC reserves -32768..-32000 for the spec/server — Utah's app codes
    sit in the -32000 block and never collide with the spec constants."""
    spec = {rpc.PARSE_ERROR, rpc.INVALID_REQUEST, rpc.METHOD_NOT_FOUND,
            rpc.INVALID_PARAMS, rpc.INTERNAL_ERROR}
    app = {rpc.OVERLOADED, rpc.UNAVAILABLE, rpc.DENIED, rpc.SHUTTING_DOWN, rpc.TIMEOUT}
    assert len(app) == 5 and not (spec & app)
    assert all(-32099 <= c <= -32000 for c in app)

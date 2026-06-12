"""The local lane's REAL HTTP runners, proven without Ollama: the transport seam
(``_post``) is faked so the actual parsing/deadline/cleanup code runs. Every wire
failure mode — half-read bodies (http.client.HTTPException is NOT an OSError),
socket errors mid-stream, error payloads, deadline overrun — must surface as
:class:`LocalUnavailable` (the caller escalates to the brain, never crashes)."""
from __future__ import annotations

import http.client
import json

import pytest

from utah import local


@pytest.fixture(autouse=True)
def _restore_runners():
    yield
    local.set_runner(None)
    local.set_stream_runner(None)
    local.set_load_probe(None)


class FakeResp:
    """Stands in for the urlopen response: iterable lines + read() + close()."""

    def __init__(self, lines=None, body=b"", read_exc=None, iter_exc=None):
        self._lines = list(lines or [])
        self._body = body
        self._read_exc = read_exc
        self._iter_exc = iter_exc
        self.closed = False

    def read(self):
        if self._read_exc:
            raise self._read_exc
        return self._body

    def __iter__(self):
        for ln in self._lines:
            yield ln
        if self._iter_exc:
            raise self._iter_exc

    def close(self):
        self.closed = True


def _chunk(content="", thinking="", done=False, **extra):
    return (json.dumps({"message": {"content": content, "thinking": thinking},
                        "done": done, **extra}) + "\n").encode()


# --- the real streaming runner (fake transport) --------------------------------

def test_stream_runner_yields_parsed_chunks_and_stops_on_done(monkeypatch):
    resp = FakeResp(lines=[_chunk(thinking="hmm "), _chunk(content="The answer.", done=True),
                           _chunk(content="NEVER SEEN")])
    monkeypatch.setattr(local, "_post", lambda payload, timeout: resp)
    out = list(local._http_stream_runner({"stream": True}, 30))
    assert out == [{"content": "", "thinking": "hmm "},
                   {"content": "The answer.", "thinking": ""}]   # stopped AT done
    assert resp.closed


def test_stream_runner_skips_blank_and_non_json_lines(monkeypatch):
    resp = FakeResp(lines=[b"\n", b"not json\n", _chunk(content="ok", done=True)])
    monkeypatch.setattr(local, "_post", lambda payload, timeout: resp)
    out = list(local._http_stream_runner({}, 30))
    assert out == [{"content": "ok", "thinking": ""}]


def test_stream_runner_raises_on_midstream_error_payload(monkeypatch):
    resp = FakeResp(lines=[_chunk(content="par"),
                           (json.dumps({"error": "model blew up"}) + "\n").encode()])
    monkeypatch.setattr(local, "_post", lambda payload, timeout: resp)
    with pytest.raises(local.LocalUnavailable, match="model blew up"):
        list(local._http_stream_runner({}, 30))
    assert resp.closed                                   # cleaned up even on failure


def test_stream_runner_wraps_socket_death_midstream(monkeypatch):
    resp = FakeResp(lines=[_chunk(content="par")], iter_exc=OSError("connection reset"))
    monkeypatch.setattr(local, "_post", lambda payload, timeout: resp)
    with pytest.raises(local.LocalUnavailable, match="stream failed"):
        list(local._http_stream_runner({}, 30))
    assert resp.closed


def test_stream_runner_wraps_incomplete_read(monkeypatch):
    """http.client.IncompleteRead is an HTTPException, NOT an OSError — without an
    explicit catch it would crash the voice/chat caller instead of escalating."""
    resp = FakeResp(lines=[_chunk(content="par")],
                    iter_exc=http.client.IncompleteRead(b"partial"))
    monkeypatch.setattr(local, "_post", lambda payload, timeout: resp)
    with pytest.raises(local.LocalUnavailable):
        list(local._http_stream_runner({}, 30))


def test_stream_runner_enforces_overall_deadline(monkeypatch):
    resp = FakeResp(lines=[_chunk(content="a"), _chunk(content="b")])
    monkeypatch.setattr(local, "_post", lambda payload, timeout: resp)
    clock = iter([0.0, 100.0, 200.0])                    # already past deadline at line 2
    monkeypatch.setattr(local.time, "monotonic", lambda: next(clock))
    with pytest.raises(local.LocalUnavailable, match="timed out"):
        list(local._http_stream_runner({}, 30))


# --- the real non-streaming runner ----------------------------------------------

def test_http_runner_parses_and_closes(monkeypatch):
    resp = FakeResp(body=json.dumps(
        {"message": {"content": "A", "thinking": "T"}, "done": True}).encode())
    monkeypatch.setattr(local, "_post", lambda payload, timeout: resp)
    assert local._http_runner({}, 30) == {"content": "A", "thinking": "T"}
    assert resp.closed


def test_http_runner_wraps_read_failures(monkeypatch):
    for exc in (OSError("reset"), http.client.IncompleteRead(b"x")):
        resp = FakeResp(read_exc=exc)
        monkeypatch.setattr(local, "_post", lambda payload, timeout, r=resp: r)
        with pytest.raises(local.LocalUnavailable):
            local._http_runner({}, 30)
        assert resp.closed


# --- the connect boundary --------------------------------------------------------

def test_post_wraps_bad_status_line(monkeypatch):
    """A garbage status line raises http.client.BadStatusLine straight out of
    urlopen — an HTTPException, not URLError/OSError — and must still become
    LocalUnavailable."""
    def boom(req, timeout):
        raise http.client.BadStatusLine("HTP/9.9 banana")

    monkeypatch.setattr(local.urllib.request, "urlopen", boom)
    with pytest.raises(local.LocalUnavailable):
        local._post({"model": "m"}, 5)


def test_post_joins_url_with_trailing_slash(monkeypatch):
    seen = {}

    def capture(req, timeout):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["ctype"] = req.get_header("Content-type")
        return FakeResp(body=b"{}")

    monkeypatch.setattr(local.urllib.request, "urlopen", capture)
    monkeypatch.setattr(local.config, "OLLAMA_URL", "http://127.0.0.1:11434/")
    local._post({"model": "m"}, 7)
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"   # no double slash
    assert seen["timeout"] == 7 and seen["ctype"] == "application/json"


# --- parse seam edge cases --------------------------------------------------------

def test_parse_message_rejects_non_object():
    with pytest.raises(local.LocalUnavailable, match="non-object"):
        local._parse_message("[1, 2]")


def test_parse_message_rejects_error_payload():
    with pytest.raises(local.LocalUnavailable, match="boom"):
        local._parse_message(json.dumps({"error": "boom"}))


def test_payload_token_budgets_differ_quick_vs_heavy():
    quick = local._payload("q", "", heavy=False, stream=False)
    heavy = local._payload("q", "", heavy=True, stream=False)
    assert quick["options"]["num_predict"] == local.config.LOCAL_QUICK_MAX_TOKENS
    assert heavy["options"]["num_predict"] == local.config.LOCAL_HEAVY_MAX_TOKENS

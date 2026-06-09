"""Unit tests for the SELF-CODE console — pure command dispatch, no DB/git/claude."""
from __future__ import annotations

from utah.product import console


def test_parse_bare_line_is_ask():
    assert console.parse("what is the leads count") == ("ask", "what is the leads count")


def test_parse_slash_commands():
    assert console.parse("/help") == ("help", "")
    assert console.parse("/code fix the outreach default channel") == (
        "code", "fix the outreach default channel")
    assert console.parse("/do 2") == ("do", "2")
    assert console.parse("/cycles 5") == ("cycles", "5")
    assert console.parse("") == ("", "")


def test_parse_unknown_command():
    cmd, arg = console.parse("/frobnicate now")
    assert cmd == "unknown" and arg == "frobnicate"


def test_is_slow():
    assert console.is_slow("code") and console.is_slow("do")
    assert not console.is_slow("status") and not console.is_slow("findings")


def test_help_lists_commands():
    out = console.run_fast("help")["output"]
    for c in ("/code", "/findings", "/status", "/do"):
        assert c in out


def test_status_uses_injected_selfcode():
    class FakeSC:
        @staticmethod
        def enabled():
            return True            # not killed

        @staticmethod
        def automerge_enabled():
            return False

        @staticmethod
        def _read_supervised():
            return 2

    out = console.run_fast("status", selfcode=FakeSC, loadavg=lambda: 0.3)["output"]
    assert "off (enabled)" in out and "propose-only" in out and "2 green" in out
    assert "0.30/core" in out


def test_findings_lists_pending_and_numbers_them():
    pending = [("frontend", "Hide the dormant risk panel", {"ts": 1}),
               ("research", "Add backoff to the GPN fetch", {"ts": 2})]
    out = console.run_fast("findings", pending_fn=lambda: pending)["output"]
    assert "[1] (frontend)" in out and "[2] (research)" in out


def test_findings_empty_is_honest():
    out = console.run_fast("findings", pending_fn=lambda: [])["output"]
    assert "No pending" in out


def test_cycles_renders_injected_rows():
    rows = [{"domain": "leads", "utility": 0.8, "passed": True, "merged": False, "task": "x"}]
    out = console.run_fast("cycles", cycles_fn=lambda n: rows)["output"]
    assert "leads" in out and "passed" in out


def test_goals_renders_bars():
    gs = [{"name": "Autonomous merges", "pct": 40, "detail": "10 of 25"}]
    out = console.run_fast("goals", goals_fn=lambda: gs)["output"]
    assert "40%" in out and "Autonomous merges" in out


def test_diff_requires_branch():
    assert "usage:" in console.run_fast("diff", "")["output"]
    out = console.run_fast("diff", "selfcode/x", diff_fn=lambda b: "--- a\n+++ b\n")["output"]
    assert "+++ b" in out


def test_unknown_command_hint():
    assert "/help" in console.run_fast("unknown", "frobnicate")["output"]


def test_resolve_slow_code_is_the_arg():
    assert console.resolve_slow_task("code", "do the thing") == ("do the thing", None)


def test_resolve_slow_do_picks_the_finding():
    pending = [("frontend", "task one", {"ts": 1}), ("research", "task two", {"ts": 2})]
    task, rec = console.resolve_slow_task("do", "2", pending_fn=lambda: pending)
    assert task == "task two" and rec == {"ts": 2}


def test_resolve_slow_do_out_of_range_is_empty():
    assert console.resolve_slow_task("do", "9", pending_fn=lambda: []) == ("", None)


# --- live agentic stream formatter ---------------------------------------------------------

def _evt(etype, **inner):
    return {"type": "stream_event", "event": {"type": etype, **inner}}


def _drive(events):
    state, out = {}, []
    for e in events:
        out += console.format_stream_event(e, state)
    return out


def test_stream_bash_command_is_shown():
    events = [
        _evt("content_block_start", content_block={"type": "tool_use", "name": "Bash"}),
        _evt("content_block_delta", delta={"type": "input_json_delta", "partial_json": '{"command":'}),
        _evt("content_block_delta", delta={"type": "input_json_delta", "partial_json": '"ls -la"}'}),
        _evt("content_block_stop"),
    ]
    assert _drive(events) == ["  $ ls -la"]


def test_stream_text_accumulates():
    events = [
        _evt("content_block_delta", delta={"type": "text_delta", "text": "hello "}),
        _evt("content_block_delta", delta={"type": "text_delta", "text": "world"}),
        _evt("content_block_stop"),
    ]
    assert _drive(events) == ["hello world"]


def test_stream_write_tool_shows_file():
    events = [
        _evt("content_block_start", content_block={"type": "tool_use", "name": "Write"}),
        _evt("content_block_delta", delta={"type": "input_json_delta", "partial_json": '{"file_path":"/x.py"}'}),
        _evt("content_block_stop"),
    ]
    assert _drive(events) == ["  ✎ Write /x.py"]


def test_stream_result_error_surfaces():
    assert console.format_stream_event({"type": "result", "subtype": "error_max_turns"}, {}) == ["  · error_max_turns"]


def test_stream_malformed_event_never_raises():
    assert console.format_stream_event({"weird": True}, {}) == []

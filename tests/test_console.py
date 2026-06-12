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


def test_effort_mode_round_trips_and_shapes_jobs():
    """Claude-style /effort: list, set, budget + rigor suffix for /code jobs."""
    from utah.product import console as con
    assert "medium" in con.run_fast("effort")["output"]          # listing shows levels
    out = con.run_fast("effort", "ultracode")["output"]
    assert "ultracode" in out
    assert con.effort() == "ultracode" and con.effort_budget() == 2400.0
    assert "self-review" in con.effort_suffix()
    assert "unknown effort" in con.run_fast("effort", "warp")["output"]
    con.run_fast("effort", "medium")                              # restore default
    assert con.effort_suffix() == ""


def test_skills_lists_the_real_registries():
    from utah.product import console as con
    out = con.run_fast("skills")["output"]
    assert "daemon capabilities" in out and "panel" in out
    assert "/code" in out and "/effort" in out                    # console surface listed


def test_history_shows_the_conversation_newest_last():
    from utah.product import console as con
    rows = [("2026-06-10 09:45", "Q: hi A: hey"), ("2026-06-10 09:40", "Q: a A: b")]
    out = con.run_fast("history", "2", turns_fn=lambda n: rows)["output"]
    lines = out.splitlines()
    assert lines[0].startswith("[2026-06-10 09:40")               # oldest first
    assert lines[-1].startswith("[2026-06-10 09:45")              # newest last
    assert "Q: hi" in out


# --- hardening: findings self-heal harvest seam ---------------------------------------------

def test_findings_selfheal_harvest_seam_is_wired():
    """run_fast must thread harvest_fn through so the self-heal refresh is provable."""
    calls = []
    pending = [("selfheal", "fix the wcfeed pgrep heal path", {"ts": 1})]
    out = console.run_fast("findings", pending_fn=lambda: pending,
                           harvest_fn=lambda: calls.append(1))["output"]
    assert calls == [1]
    assert "[1] (selfheal)" in out


def test_findings_default_harvest_actually_runs(monkeypatch):
    """The production path must call sica_discover.harvest_failure_findings (the
    self-heal refresh documented in the command) — not skip it silently."""
    from utah import sica_discover
    calls = []
    monkeypatch.setattr(sica_discover, "harvest_failure_findings",
                        lambda **k: calls.append(1) or {})
    monkeypatch.setattr(sica_discover, "pending_findings", lambda **k: [])
    out = console.run_fast("findings")["output"]
    assert calls == [1]
    assert "No pending" in out


def test_findings_injected_pending_stays_hermetic(monkeypatch):
    """An injected pending_fn (a test double) must NOT trigger the real harvest."""
    from utah import sica_discover

    def boom(**k):
        raise AssertionError("real harvest must not run under an injected pending_fn")

    monkeypatch.setattr(sica_discover, "harvest_failure_findings", boom)
    out = console.run_fast("findings", pending_fn=lambda: [])["output"]
    assert "No pending" in out


def test_findings_harvest_crash_never_hides_findings():
    def boom():
        raise RuntimeError("failure feed down")
    out = console.run_fast("findings", pending_fn=lambda: [("a", "task", {})],
                           harvest_fn=boom)["output"]
    assert "[1] (a)" in out


def test_findings_source_failure_is_honest():
    def boom():
        raise RuntimeError("disk gone")
    out = console.run_fast("findings", pending_fn=boom)["output"]
    assert "unavailable" in out and "RuntimeError" in out


def test_findings_malformed_record_never_raises():
    out = console.run_fast("findings", pending_fn=lambda: [("d", None, {})])["output"]
    assert "[1] (d)" in out                                       # None task renders empty


# --- hardening: renderers never raise on degenerate rows -------------------------------------

def test_goals_renders_safe_on_missing_pct():
    gs = [{"name": "X", "pct": None, "detail": "no pct yet"}]
    out = console.run_fast("goals", goals_fn=lambda: gs)["output"]
    assert "0%" in out


def test_goals_renders_clamped_on_overflow_pct():
    gs = [{"name": "X", "pct": 250, "detail": "over"}]
    out = console.run_fast("goals", goals_fn=lambda: gs)["output"]
    assert "100%" in out                                          # clamped, never a 250% bar
    assert "250%" not in out


def test_cycles_renders_honest_on_non_dict_row():
    out = console.run_fast("cycles", cycles_fn=lambda n: ["not-a-dict"])["output"]
    assert "unavailable" in out


# --- hardening: /history default DB boundary is bounded --------------------------------------

def test_history_default_db_read_is_bounded(monkeypatch):
    import sys
    import types
    seen = {}
    fake = types.ModuleType("psycopg")

    def connect(dsn, **kw):
        seen.update(kw)
        raise RuntimeError("no db in unit tests")

    fake.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    out = console.run_fast("history", "3")["output"]
    assert "history unavailable" in out                           # honest degradation
    assert seen.get("connect_timeout"), "DB connect must carry a real timeout"
    assert "statement_timeout" in seen.get("options", ""), \
        "the SELECT itself must be bounded, not just the connect"


# --- hardening: run_claude_streamed (real subprocess boundary) --------------------------------

import json as _json  # noqa: E402
import subprocess as _sp  # noqa: E402
import time as _time  # noqa: E402


def _fake_claude(tmp_path, body: str) -> str:
    p = tmp_path / "fakeclaude.sh"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return str(p)


def test_run_claude_streamed_happy_path(tmp_path):
    evt = _json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": "done"}}})
    stop = _json.dumps({"type": "stream_event", "event": {"type": "content_block_stop"}})
    body = f"cat >/dev/null\necho '{evt}'\necho '{stop}'\n"
    lines: list[str] = []
    console.run_claude_streamed("task", cwd=str(tmp_path), on_line=lines.append,
                                timeout=15.0, brain_cmd=_fake_claude(tmp_path, body))
    assert lines == ["done"]


def test_run_claude_streamed_nonzero_exit_raises_with_stderr(tmp_path):
    body = "cat >/dev/null\necho 'boom detail' >&2\nexit 3\n"
    import pytest
    with pytest.raises(RuntimeError) as ei:
        console.run_claude_streamed("task", cwd=str(tmp_path), on_line=lambda l: None,
                                    timeout=15.0, brain_cmd=_fake_claude(tmp_path, body))
    assert "3" in str(ei.value) and "boom detail" in str(ei.value)


def test_run_claude_streamed_missing_binary_is_runtime_error(tmp_path):
    import pytest
    with pytest.raises(RuntimeError):
        console.run_claude_streamed("task", cwd=str(tmp_path), on_line=lambda l: None,
                                    timeout=5.0, brain_cmd=str(tmp_path / "no-such-claude"))


def test_run_claude_streamed_silent_child_times_out(tmp_path):
    """A child that produces NO output must still hit the timeout (a blocked readline
    never reaches an in-loop deadline check) — and be killed, not awaited to term."""
    import pytest
    body = "sleep 5\n"
    t0 = _time.monotonic()
    with pytest.raises(_sp.TimeoutExpired):
        console.run_claude_streamed("task", cwd=str(tmp_path), on_line=lambda l: None,
                                    timeout=1.0, brain_cmd=_fake_claude(tmp_path, body))
    assert _time.monotonic() - t0 < 4.0, "child must be killed at the deadline"


def test_run_claude_streamed_stderr_flood_never_deadlocks(tmp_path):
    """>64KB of stderr fills an undrained PIPE and deadlocks the child — the boundary
    must drain stderr concurrently."""
    body = ("cat >/dev/null\n"
            "i=0\nwhile [ $i -lt 4000 ]; do\n"
            "  echo 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' >&2\n"
            "  i=$((i+1))\ndone\nexit 0\n")
    console.run_claude_streamed("task", cwd=str(tmp_path), on_line=lambda l: None,
                                timeout=30.0, brain_cmd=_fake_claude(tmp_path, body))


def test_run_claude_streamed_broken_sink_never_kills_run(tmp_path):
    evt = _json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}})
    stop = _json.dumps({"type": "stream_event", "event": {"type": "content_block_stop"}})
    body = f"cat >/dev/null\necho '{evt}'\necho '{stop}'\n"

    def bad_sink(line):
        raise RuntimeError("sink broke")

    console.run_claude_streamed("task", cwd=str(tmp_path), on_line=bad_sink,
                                timeout=15.0, brain_cmd=_fake_claude(tmp_path, body))

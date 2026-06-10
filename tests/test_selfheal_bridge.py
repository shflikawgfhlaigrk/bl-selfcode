"""The two self-healing bridges (Michael 2026-06-10: 'the voice and the coding agent
don't use each other to fix itself'): recurring failures auto-file selfcode findings,
and a spoken 'code <task>' / 'fix your <x>' dispatches a real propose-only job."""
from types import SimpleNamespace

from utah import sica_discover
from utah.voice import agent


def _rows(*pairs):
    return [SimpleNamespace(source=s, kind=k, detail="") for s, k in pairs]


def test_recurring_failures_become_pending_findings(tmp_path):
    log = tmp_path / "discoveries.jsonl"
    rows = _rows(*([("voice", "stt_degraded_session")] * 4 + [("trading", "feed_gated")]))
    r = sica_discover.harvest_failure_findings(recent_fn=lambda n: rows, log_path=log,
                                               min_count=3)
    assert r["harvested"] == 1                          # only the recurring one
    pending = sica_discover.pending_findings(log_path=log, used_path=tmp_path / "used")
    assert len(pending) == 1
    domain, task, rec = pending[0]
    assert domain == "selfheal" and "voice/stt_degraded_session" in task
    assert rec["count"] == 4
    # re-harvest with the same feed: deduped, no spam
    r2 = sica_discover.harvest_failure_findings(recent_fn=lambda n: rows, log_path=log,
                                                min_count=3)
    assert r2["harvested"] == 0 and r2["skipped"] == 1


def test_voice_code_command_dispatches_a_real_job():
    spoken, jobs = [], []
    r = agent.handle_utterance(
        "ace code add a retry to the mail sender",
        tell=lambda t: None, tell_stream=lambda *a, **k: iter([]),
        speak_stream=lambda chunks, on_start=None: spoken.extend(chunks),
        publish=lambda *a, **k: None,
        dispatch=lambda task: jobs.append(task) or "job123",
    )
    assert jobs == ["add a retry to the mail sender"]
    assert r["source"] == "selfcode" and r["job"] == "job123"
    assert any("coding that now" in s for s in spoken)


def test_voice_fix_your_command_carries_the_whole_utterance():
    jobs = []
    r = agent.handle_utterance(
        "ace fix your voice latency",
        speak_stream=lambda chunks, on_start=None: list(chunks),
        publish=lambda *a, **k: None,
        dispatch=lambda task: jobs.append(task) or "j9",
    )
    assert jobs == ["fix your voice latency"] and r["job"] == "j9"


def test_normal_questions_never_touch_the_coder():
    assert agent.coding_task("what is the weather") is None
    assert agent.coding_task("decode this message") is None   # 'code' inside a word
    assert agent.coding_task("code ") is None                 # empty task

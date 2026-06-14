import textwrap

from utah import agents


def test_loads_and_routes_a_single_file_agent(tmp_path, monkeypatch):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "echo_test.py").write_text(textwrap.dedent('''
        KEYWORDS = ["pingxyz"]
        def run(ctx):
            return "pong" if "pingxyz" in ctx.get("message", "").lower() else None
    '''))
    monkeypatch.setattr(agents, "_DIR", d)
    agents.reload()
    assert "echo_test" in agents.names()
    assert agents.route("please say pingxyz") == "pong"
    assert agents.route("an unrelated question") is None   # declines -> falls through to brain


def test_a_broken_agent_never_crashes_ace(tmp_path, monkeypatch):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "boom.py").write_text("KEYWORDS=['zzq']\ndef run(ctx): raise RuntimeError('boom')")
    (d / "good.py").write_text("KEYWORDS=['zzq']\ndef run(ctx): return 'ok'")
    monkeypatch.setattr(agents, "_DIR", d)
    agents.reload()
    assert agents.route("zzq please") == "ok"   # broken one is skipped, good one answers

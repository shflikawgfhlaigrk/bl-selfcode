"""The git_state hot-loaded agent gives Ace REAL source-control visibility.

It must: fire only on git/work-safety turns, decline everything else, report the
real branch + dirty + ahead/behind from an actual repo, flag work-at-risk when
there are uncommitted or unpushed commits, and never crash Ace when git can't be
read.
"""
import importlib.util
import pathlib
import subprocess

_AGENT = pathlib.Path(__file__).resolve().parent.parent / "agents" / "git_state.py"


def _load():
    spec = importlib.util.spec_from_file_location("git_state_under_test", _AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    _git(path, "commit", "--allow-empty", "-q", "-m", "root")


def test_declines_non_git_turns():
    mod = _load()
    assert mod.run("what's the weather like") is None
    assert mod.run("tell me about the leads pipeline") is None
    assert mod.run({"text": "how much disk space is left"}) is None


def test_fires_on_git_intent():
    mod = _load()
    assert mod.matches("do you have uncommitted changes?")
    assert mod.matches("are you ahead of origin?")
    assert mod.matches("is your work safe?")
    assert mod.matches("what's your git status")


def test_reports_clean_repo(tmp_path, monkeypatch):
    repo = tmp_path / "clean"
    _init_repo(repo)
    mod = _load()
    monkeypatch.setattr(mod, "_REPO", repo)
    out = mod.run("git status please")
    assert "Working tree clean" in out
    assert "Nothing at risk" in out


def test_flags_uncommitted_work_at_risk(tmp_path, monkeypatch):
    repo = tmp_path / "dirty"
    _init_repo(repo)
    (repo / "scratch.txt").write_text("unsaved work")
    mod = _load()
    monkeypatch.setattr(mod, "_REPO", repo)
    out = mod.run("do I have uncommitted changes?")
    assert "1 uncommitted file" in out
    assert "Work at risk" in out


def test_flags_unpushed_commits(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare")
    repo = tmp_path / "local"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "HEAD")
    # one commit ahead of the now-tracked upstream
    _git(repo, "commit", "--allow-empty", "-q", "-m", "ahead")
    mod = _load()
    monkeypatch.setattr(mod, "_REPO", repo)
    out = mod.run("am I ahead of origin?")
    assert "1 commit ahead" in out
    assert "unpushed" in out
    assert "Work at risk" in out


def test_honest_when_git_unreadable(tmp_path, monkeypatch):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    mod = _load()
    monkeypatch.setattr(mod, "_REPO", not_a_repo)
    out = mod.run("git status")
    assert out is not None
    assert "git repo" in out.lower()

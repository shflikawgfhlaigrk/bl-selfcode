"""ops/verify.py — Utah's self-verifier, exercised against a FAKE pytest binary.

The verifier's whole reason to exist is reporting truthfully when everything
else is broken, so the tests hold it to that: truth = the suite's exit code
(never parsed prose), a hung suite becomes rc 124 not a hung verifier, a
missing interpreter rc 125, the status write is atomic, the --loop triple-
confirm logic clears build-write races but confirms real reds, and a crashing
cycle never kills the loop (a dead verifier = permanently stale verify.json).
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import stat
import time

import pytest

VERIFY_PY = pathlib.Path(__file__).resolve().parents[1] / "ops" / "verify.py"


def _write_exe(path: pathlib.Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _load(tmp_path, env: dict | None = None):
    """Fresh module sandboxed to *tmp_path* (HOME, ROOT, and PY all faked)."""
    root = tmp_path / "root"
    (root / "utah").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    
    saved_timeout = os.environ.get("UTAH_VERIFY_TIMEOUT")
    if "UTAH_VERIFY_TIMEOUT" not in (env or {}):
        os.environ.pop("UTAH_VERIFY_TIMEOUT", None)

    env = {
        "UTAH_HOME": str(tmp_path / ".utah"),
        "UTAH_ROOT": str(root),
        "UTAH_PY": str(tmp_path / "fake_python"),
        **(env or {}),
    }
    saved = {}
    for key, val in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = val
    try:
        spec = importlib.util.spec_from_file_location("ops_verify_under_test", VERIFY_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        if saved_timeout is not None:
            os.environ["UTAH_VERIFY_TIMEOUT"] = saved_timeout
        else:
            os.environ.pop("UTAH_VERIFY_TIMEOUT", None)


# ---------------------------------------------------------------------------
# run_suite — truth is the exit code, bounded, honest rc on every failure mode
# ---------------------------------------------------------------------------

def test_run_suite_green_on_exit_zero(tmp_path):
    mod = _load(tmp_path)
    _write_exe(tmp_path / "fake_python", 'echo "all good"\nexit 0\n')
    rc, dur, fails = mod.run_suite()
    assert rc == 0 and fails == []


def test_run_suite_collects_failure_lines_on_red(tmp_path):
    mod = _load(tmp_path)
    _write_exe(tmp_path / "fake_python",
               'echo "FAILED tests/test_x.py::test_y - boom"\n'
               'echo "ERROR collecting tests/test_z.py"\n'
               'echo "ERROR onnxruntime coreml noise line"\n'   # known mac noise
               'exit 1\n')
    rc, _dur, fails = mod.run_suite()
    assert rc == 1
    assert any("test_x.py::test_y" in line for line in fails)
    assert any("collecting" in line for line in fails)
    assert not any("onnxruntime" in line for line in fails), \
        "known onnxruntime noise must not masquerade as a failure"


def test_default_suite_timeout_is_1200_seconds(tmp_path):
    mod = _load(tmp_path)
    assert mod.SUITE_TIMEOUT == 1200
    assert mod._DEFAULT_SUITE_TIMEOUT == 1200


def test_run_suite_hung_suite_is_bounded_rc_124(tmp_path):
    mod = _load(tmp_path, {"UTAH_VERIFY_TIMEOUT": "1"})
    _write_exe(tmp_path / "fake_python", "exec sleep 30\n")
    t0 = time.monotonic()
    rc, _dur, fails = mod.run_suite()
    assert time.monotonic() - t0 < 20, "a hung suite must not hang the verifier"
    assert rc == 124 and "timed out" in fails[0]


def test_run_suite_missing_interpreter_is_rc_125(tmp_path):
    mod = _load(tmp_path)   # fake_python never written
    rc, _dur, fails = mod.run_suite()
    assert rc == 125 and "could not run pytest" in fails[0]


def test_run_suite_passes_test_dsn_to_the_child(tmp_path):
    mod = _load(tmp_path, {"UTAH_TEST_DSN": "host=/tmp dbname=utah_test_sentinel"})
    _write_exe(tmp_path / "fake_python", 'echo "$UTAH_TEST_DSN" > "$PWD/seen_dsn"\nexit 0\n')
    mod.run_suite()
    assert "utah_test_sentinel" in (tmp_path / "root" / "seen_dsn").read_text(), \
        "the suite must run against the TEST database, never the live one"


# ---------------------------------------------------------------------------
# verify_once — status payload shape (consumed by sica_goals + daemon CLI)
# ---------------------------------------------------------------------------

def test_verify_once_payload_shape_green(tmp_path):
    mod = _load(tmp_path)
    res = mod.verify_once(runner=lambda: (0, 1.5, []))
    assert res["ok"] is True and res["state"] == "green" and res["exit_code"] == 0
    assert set(res) >= {"ok", "state", "exit_code", "duration_s", "pyfiles",
                        "failures", "ts"}


def test_verify_once_red_keeps_failure_lines(tmp_path):
    mod = _load(tmp_path)
    res = mod.verify_once(runner=lambda: (1, 2.0, ["FAILED tests/test_a.py::t"]))
    assert res["ok"] is False and res["state"] == "red"
    assert res["failures"] == ["FAILED tests/test_a.py::t"]


def test_pyfiles_counts_only_real_sources(tmp_path):
    mod = _load(tmp_path)
    root = tmp_path / "root"
    (root / "utah" / "a.py").write_text("x = 1\n")
    (root / "tests" / "test_a.py").write_text("def test(): pass\n")
    cache = root / "utah" / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-312.py").write_text("compiled\n")
    assert mod._pyfiles() == 2, "__pycache__ must not count as source"


def test_changed_within_detects_fresh_writes(tmp_path):
    mod = _load(tmp_path)
    target = tmp_path / "root" / "utah" / "fresh.py"
    target.write_text("x = 1\n")
    assert mod._changed_within(60) is True
    old = time.time() - 3600
    os.utime(target, (old, old))
    assert mod._changed_within(60) is False


# ---------------------------------------------------------------------------
# _write_status — atomic, no partial snapshots
# ---------------------------------------------------------------------------

def test_write_status_failure_keeps_old_snapshot_and_cleans_tmp(tmp_path):
    mod = _load(tmp_path)
    mod._write_status({"ok": True, "state": "green"})
    before = mod.STATUS.read_text()
    with pytest.raises(TypeError):
        mod._write_status({"ok": True, "junk": object()})
    assert mod.STATUS.read_text() == before
    stray = [p for p in mod.RUN_DIR.iterdir() if p.name.startswith(".verify")]
    assert stray == []


# ---------------------------------------------------------------------------
# loop — triple-confirm + crash survival
# ---------------------------------------------------------------------------

def _seq(*results):
    it = iter(results)
    return lambda: next(it)


def test_loop_clears_a_one_off_red_as_write_race(tmp_path):
    mod = _load(tmp_path)
    written = []
    mod.loop(verify_fn=_seq({"ok": False, "state": "red"},
                            {"ok": True, "state": "green"}),
             write_fn=written.append, sleep_fn=lambda s: None,
             changed_fn=lambda s: False, max_cycles=1)
    final = written[-1]
    assert final["ok"] is True and "race" in final["note"]


def test_loop_confirms_a_persistent_red(tmp_path):
    mod = _load(tmp_path)
    written = []
    red = {"ok": False, "state": "red"}
    mod.loop(verify_fn=_seq(dict(red), dict(red), dict(red)),
             write_fn=written.append, sleep_fn=lambda s: None,
             changed_fn=lambda s: False, max_cycles=1)
    final = written[-1]
    assert final["ok"] is False and final["confirmed"] is True


def test_loop_waits_out_an_active_build_boundedly(tmp_path):
    mod = _load(tmp_path)
    written = []
    mod.loop(verify_fn=_seq({"ok": True, "state": "green"}),
             write_fn=written.append, sleep_fn=lambda s: None,
             changed_fn=lambda s: True,   # builder never pauses
             max_cycles=1)
    states = [w.get("state") for w in written]
    assert "build_active" in states, "an active build must be visible in the status"
    assert written[-1]["ok"] is True, "a relentless builder must still get sampled"


def test_loop_survives_a_crashing_cycle(tmp_path):
    mod = _load(tmp_path)
    cycles = {"n": 0}

    def boom():
        cycles["n"] += 1
        raise OSError("disk gone")

    written = []
    mod.loop(verify_fn=boom, write_fn=written.append, sleep_fn=lambda s: None,
             changed_fn=lambda s: False, max_cycles=3)
    assert cycles["n"] == 3, "a crashing cycle must not kill the verifier"
    assert all(w.get("state") == "error" and w.get("ok") is False
               for w in written), "crashed cycles must publish an honest error state"


# ---------------------------------------------------------------------------
# main — one-shot exit code
# ---------------------------------------------------------------------------

def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    mod = _load(tmp_path)
    monkeypatch.setattr(mod, "verify_once",
                        lambda runner=None: {"ok": True, "state": "green"})
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    monkeypatch.setattr(mod, "verify_once",
                        lambda runner=None: {"ok": False, "state": "red"})
    assert mod.main([]) == 1

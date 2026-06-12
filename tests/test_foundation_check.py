"""ops/foundation_check.py — the standalone substrate probe wrapper.

Contract under test: ``run_once`` is a never-raises boundary that BOUNDS the
underlying ``foundation.check()`` (a wedged probe becomes an honest red, never
a hung launchd job), the status write is atomic and its failure flips the
verdict (a green nobody can read is not green — ``gate_cron`` feeds on this
file), and the ``--loop`` mode survives a crashing tick instead of dying and
leaving ``foundation.json`` permanently stale.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import time

CHECK_PY = pathlib.Path(__file__).resolve().parents[1] / "ops" / "foundation_check.py"


def _load(tmp_path, env: dict | None = None):
    """Fresh module with UTAH_HOME sandboxed to *tmp_path*."""
    env = {"UTAH_HOME": str(tmp_path / ".utah"), **(env or {})}
    saved = {}
    for key, val in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = val
    try:
        spec = importlib.util.spec_from_file_location("ops_foundation_check_under_test",
                                                      CHECK_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


# ---------------------------------------------------------------------------
# run_once — bounded, honest, never raises
# ---------------------------------------------------------------------------

def test_run_once_green_writes_status_with_ts(tmp_path):
    mod = _load(tmp_path)
    payload = mod.run_once(lambda: {"ok": True, "state": "green",
                                    "checks": {"postgres": True}, "anomalies": []})
    assert payload["ok"] is True and payload["ts"] > 0
    on_disk = json.loads(mod.STATUS.read_text())
    assert on_disk["ok"] is True and on_disk["checks"] == {"postgres": True}


def test_run_once_bounds_a_wedged_check(tmp_path):
    mod = _load(tmp_path)

    def wedged():
        time.sleep(10)
        return {"ok": True}

    t0 = time.monotonic()
    payload = mod.run_once(wedged, timeout_s=0.3)
    assert time.monotonic() - t0 < 5, "a wedged check must not hang the probe"
    assert payload["ok"] is False and "probe_timeout" in payload["anomalies"]
    assert json.loads(mod.STATUS.read_text())["ok"] is False, \
        "the timeout verdict must still reach foundation.json"


def test_run_once_crashing_check_is_honest_red_not_an_exception(tmp_path):
    mod = _load(tmp_path)

    def boom():
        raise RuntimeError("substrate probe exploded")

    payload = mod.run_once(boom)
    assert payload["ok"] is False and "probe_crashed" in payload["anomalies"]
    assert "substrate probe exploded" in payload["error"]


def test_run_once_non_dict_payload_is_red(tmp_path):
    mod = _load(tmp_path)
    payload = mod.run_once(lambda: "green!")
    assert payload["ok"] is False and "probe_bad_payload" in payload["anomalies"]


def test_run_once_write_failure_flips_ok(tmp_path):
    """gate_cron reads foundation.json; if the probe can't publish, it must not
    claim green."""
    mod = _load(tmp_path)
    payload = mod.run_once(lambda: {"ok": True, "state": "green",
                                    "junk": object(),   # unserializable
                                    "checks": {}, "anomalies": []})
    assert payload["ok"] is False
    assert "status_write_failed" in payload["anomalies"]
    assert "write_error" in payload


def test_status_write_is_atomic_and_failure_keeps_old_snapshot(tmp_path):
    mod = _load(tmp_path)
    mod.run_once(lambda: {"ok": True, "state": "green", "checks": {}, "anomalies": []})
    before = mod.STATUS.read_text()
    mod.run_once(lambda: {"ok": True, "junk": object(), "checks": {}, "anomalies": []})
    assert mod.STATUS.read_text() == before, "failed write must not corrupt the snapshot"
    stray = [p for p in mod.RUN_DIR.iterdir() if p.name.startswith(".foundation")]
    assert stray == [], "failed write must clean up its temp file"


# ---------------------------------------------------------------------------
# loop — a crashing tick never kills the prober
# ---------------------------------------------------------------------------

def test_loop_survives_crashing_ticks(tmp_path):
    mod = _load(tmp_path, {"UTAH_FOUNDATION_INTERVAL": "1"})
    ticks = {"n": 0}

    def bad_run():
        ticks["n"] += 1
        raise OSError("disk on fire")

    mod.loop(run_fn=bad_run, sleep_fn=lambda s: None, max_ticks=3)
    assert ticks["n"] == 3, "loop must keep probing through tick crashes"


def test_loop_appends_red_log(tmp_path):
    mod = _load(tmp_path)
    mod.loop(run_fn=lambda: {"ok": False, "anomalies": ["postgres_down"]},
             sleep_fn=lambda s: None, max_ticks=1)
    log_file = tmp_path / ".utah" / "logs" / "foundation.log"
    assert log_file.exists() and "postgres_down" in log_file.read_text()


# ---------------------------------------------------------------------------
# main — exit code is the honest signal
# ---------------------------------------------------------------------------

def test_main_exit_zero_on_green(tmp_path, monkeypatch, capsys):
    mod = _load(tmp_path)
    monkeypatch.setattr(mod, "run_once", lambda: {"ok": True, "state": "green"})
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_main_exit_one_on_red(tmp_path, monkeypatch):
    mod = _load(tmp_path)
    monkeypatch.setattr(mod, "run_once", lambda: {"ok": False, "state": "red"})
    assert mod.main([]) == 1

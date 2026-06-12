"""Eval-harness boundary honesty: a failing recall_fn must be DOCUMENTED (logged),
not score 0 invisibly; non-string hit content must coerce, not abort the whole
eval; and the ``main`` CLI gate — the module's concrete consumer — must report
JSON and exit 0/1/2 honestly."""
from __future__ import annotations

import json
import logging

from utah import memory
from utah.memory import eval as recall_eval
from tests.fakes import basis, blend


class _Hit:
    def __init__(self, content):
        self.content = content


# --- recall_fn failure is loud, not silent ------------------------------------------


def test_recall_failure_is_logged_not_silent(caplog):
    """A misconfigured recall_fn scoring 0 invisibly would let a CI gate pass on a
    broken store — the failure must land in the log with the query named."""
    def boom(q, k):
        raise RuntimeError("store down")

    with caplog.at_level(logging.WARNING, logger="utah.memory.eval"):
        m = recall_eval.evaluate_recall(
            [{"query": "where does Michael live", "relevant": ["Utah"]}], boom, k=5)
    assert m.hit_rate == 0.0                      # still scores 0, never raises
    warnings = [r for r in caplog.records if r.name == "utah.memory.eval"]
    assert len(warnings) == 1
    assert "where does Michael live" in warnings[0].getMessage()
    assert "store down" in warnings[0].getMessage()


def test_healthy_recall_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="utah.memory.eval"):
        m = recall_eval.evaluate_recall([{"query": "q", "relevant": ["GOLD"]}],
                                        lambda q, k: [_Hit("GOLD here")], k=3)
    assert m.hit_rate == 1.0
    assert not [r for r in caplog.records if r.name == "utah.memory.eval"]


# --- non-string hit content must not abort the eval ----------------------------------


def test_non_string_hit_content_is_coerced_not_fatal():
    """recall_fn output is untrusted: one hit carrying int/bytes content used to
    AttributeError the WHOLE eval out from under the gate."""
    labeled = [{"query": "q", "relevant": ["8849"]}]
    rf = lambda q, k: [_Hit(8849), _Hit(b"bytes"), _Hit("text 8849")]  # noqa: E731
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.queries == 1 and m.hit_rate == 1.0
    assert m.mrr == 1.0                            # the coerced int hit "8849" matches


# --- the CLI gate: run_eval's concrete consumer ---------------------------------------


def _write_labeled(tmp_path, payload):
    f = tmp_path / "labeled.json"
    f.write_text(json.dumps(payload))
    return str(f)


def test_main_passes_gate_against_the_live_pipeline(mem, tmp_path, capsys):
    mem.embedder.register("Michael lives in Utah", basis(0))
    mem.embedder.register("where does Michael live", blend(basis(0), basis(1), 0.8))
    memory.store("Michael lives in Utah", source="fact")
    path = _write_labeled(tmp_path,
                          [{"query": "where does Michael live", "relevant": ["Utah"]}])
    rc = recall_eval.main([path, "--k", "3", "--min-hit-rate", "1.0"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True and out["hit_rate"] == 1.0 and out["k"] == 3


def test_main_fails_gate_when_recall_misses(mem, tmp_path, capsys):
    path = _write_labeled(tmp_path,
                          [{"query": "capital of France", "relevant": ["Paris"]}])
    rc = recall_eval.main([path, "--min-hit-rate", "0.9"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False and out["hit_rate"] == 0.0


def test_main_threshold_falls_back_to_env(mem, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("UTAH_EVAL_MIN_HIT_RATE", "0.9")
    path = _write_labeled(tmp_path, [{"query": "q", "relevant": ["NOPE"]}])
    assert recall_eval.main([path]) == 1           # env gate enforced
    capsys.readouterr()
    monkeypatch.setenv("UTAH_EVAL_MIN_HIT_RATE", "not-a-number")
    assert recall_eval.main([path]) == 0           # garbage env degrades to report-only
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_main_missing_file_exits_2_with_json_error(tmp_path, capsys):
    rc = recall_eval.main([str(tmp_path / "nope.json")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["ok"] is False and "error" in out


def test_main_malformed_json_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json !!")
    rc = recall_eval.main([str(bad)])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_main_non_list_payload_exits_2(tmp_path, capsys):
    rc = recall_eval.main([_write_labeled(tmp_path, {"query": "q"})])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_main_bad_example_shape_exits_2_named_by_index(mem, tmp_path, capsys):
    rc = recall_eval.main([_write_labeled(tmp_path, ["garbage"])])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and "labeled[0]" in out["error"]

"""Tests for the CE-v2 regression gate (evaluation/ce_v2_gate.py).

Pure-comparison tests - no torch, no models, no gitignored evaluation/out
artifacts.  The real gate run (which needs the checkpoints) is exercised
locally via the pre-commit hook; these tests pin the gate *logic* so CI can
validate it on every push.

Runs in the main validation job (tests/ is on the pytest testpaths).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation import ce_v2_gate as gate
from evaluation.ce_v2_gate import compare

BASELINE = PROJECT_ROOT / "evaluation" / "ce_v2_baseline.json"


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture()
def fixtures(baseline):
    """Deep copies of the baseline's eval + error-analysis sections."""
    return copy.deepcopy(baseline["eval"]), copy.deepcopy(baseline["error_analysis"])


def test_baseline_file_exists_and_shaped(baseline):
    assert baseline["eval"]["ranking"]["v2"]["mrr"] > 0
    assert baseline["error_analysis"]["categories"]["hierarchy_version"] > 0
    assert baseline["error_analysis"]["failures_total"] > 0


def test_passes_at_baseline(fixtures, baseline):
    eval_data, err_data = fixtures
    passed, checks = compare(eval_data, err_data, baseline)
    assert passed
    # Hard gates must all pass at baseline; target gates report ok=False
    # (hierarchy 8 > 4) but are not enforced without --strict-targets.
    assert all(c["ok"] for c in checks if c["kind"] == "hard")
    assert any(c["kind"] == "target" and c["ok"] is False for c in checks)


def test_fails_on_mrr_regression(fixtures, baseline):
    eval_data, err_data = fixtures
    eval_data["ranking"]["v2"]["mrr"] = 0.1
    passed, checks = compare(eval_data, err_data, baseline)
    assert not passed
    mrr = next(c for c in checks if c["name"] == "MRR@10")
    assert mrr["ok"] is False


def test_fails_on_pairwise_regression(fixtures, baseline):
    eval_data, err_data = fixtures
    eval_data["pairwise"]["v2"]["acc"] = 0.1
    passed, _ = compare(eval_data, err_data, baseline)
    assert not passed


def test_fails_on_domain_regression(fixtures, baseline):
    eval_data, err_data = fixtures
    for d in eval_data["per_domain"]:
        if d["domain"] == "epa":
            d["v2"] = 0.0
    passed, checks = compare(eval_data, err_data, baseline)
    assert not passed
    epa = next(c for c in checks if c["name"] == "epa pairwise accuracy")
    assert epa["ok"] is False


def test_targets_reported_but_not_enforced_by_default(fixtures, baseline):
    eval_data, err_data = fixtures  # baseline has hierarchy=8 (> target 4)
    passed, checks = compare(eval_data, err_data, baseline, strict_targets=False)
    assert passed  # targets are informational without --strict-targets
    hier = next(c for c in checks if c["name"] == "hierarchy_version failures")
    assert hier["ok"] is False
    assert hier["kind"] == "target"


def test_targets_fail_with_strict(fixtures, baseline):
    eval_data, err_data = fixtures  # baseline hierarchy=8, same_section=3
    passed, checks = compare(eval_data, err_data, baseline, strict_targets=True)
    assert not passed
    hier = next(c for c in checks if c["name"] == "hierarchy_version failures")
    assert hier["ok"] is False


def test_targets_pass_when_improved(fixtures, baseline):
    eval_data, err_data = fixtures
    err_data["categories"]["hierarchy_version"] = 4
    err_data["categories"]["same_section_hard_neg"] = 1
    err_data["failures_total"] = 12
    passed, _ = compare(eval_data, err_data, baseline, strict_targets=True)
    assert passed


def test_missing_metric_is_fail_not_crash(fixtures, baseline):
    eval_data, err_data = fixtures
    del eval_data["ranking"]["v2"]["mrr"]
    passed, checks = compare(eval_data, err_data, baseline)
    assert not passed
    assert any(c["name"] == "MRR@10" and c["ok"] is False for c in checks)


def test_empty_inputs_fail_cleanly(baseline):
    passed, checks = compare({}, {}, baseline)
    assert not passed
    assert all(c["ok"] is False for c in checks if c["current"] is None)


def test_retrained_since_detects_newer_artifacts(tmp_path, monkeypatch):
    d1, d2 = tmp_path / "m1", tmp_path / "m2"
    d1.mkdir()
    d2.mkdir()
    (d1 / "config.json").write_text("{}")
    (d2 / "config.json").write_text("{}")
    pairs = tmp_path / "pairs.jsonl"
    split = tmp_path / "split.json"
    pairs.write_text("")
    split.write_text("{}")

    monkeypatch.setattr(gate, "_model_dirs", lambda: [d1, d2])
    monkeypatch.setattr(gate, "PAIRS_FILE", pairs)
    monkeypatch.setattr(gate, "SPLIT_FILE", split)

    future = {"frozen_at": "2999-01-01T00:00:00+00:00"}
    assert gate._retrained_since(future) is False  # artifacts older than freeze

    past = {"frozen_at": "2020-01-01T00:00:00+00:00"}
    assert gate._retrained_since(past) is True  # artifacts newer than freeze


def test_retrained_since_without_frozen_at_is_retrained(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_model_dirs", lambda: [tmp_path, tmp_path])
    monkeypatch.setattr(gate, "PAIRS_FILE", tmp_path / "p.jsonl")
    monkeypatch.setattr(gate, "SPLIT_FILE", tmp_path / "s.json")
    assert gate._retrained_since({}) is True


def test_models_available_detects_missing_checkpoints(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(gate, "_model_dirs", lambda: [empty, empty])
    assert gate._models_available() is False

    with_ckpt = tmp_path / "with"
    with_ckpt.mkdir()
    (with_ckpt / "config.json").write_text("{}")
    monkeypatch.setattr(gate, "_model_dirs", lambda: [with_ckpt, with_ckpt])
    assert gate._models_available() is True


def test_checkpoint_only_retrain_triggers_staleness(tmp_path, monkeypatch):
    """A retrain that only touches the checkpoint dir (no data change) is detected."""
    d1, d2 = tmp_path / "m1", tmp_path / "m2"
    d1.mkdir()
    d2.mkdir()
    pairs = tmp_path / "pairs.jsonl"
    split = tmp_path / "split.json"
    pairs.write_text("")
    split.write_text("{}")
    monkeypatch.setattr(gate, "_model_dirs", lambda: [d1, d2])
    monkeypatch.setattr(gate, "PAIRS_FILE", pairs)
    monkeypatch.setattr(gate, "SPLIT_FILE", split)

    # Freeze in the past, checkpoint written now.
    (d2 / "model.safetensors").write_bytes(b"weights")
    past = {"frozen_at": "2020-01-01T00:00:00+00:00"}
    assert gate._retrained_since(past) is True

    # Freeze after the checkpoint -> nothing newer -> not retrained.
    future = {"frozen_at": "2999-01-01T00:00:00+00:00"}
    assert gate._retrained_since(future) is False

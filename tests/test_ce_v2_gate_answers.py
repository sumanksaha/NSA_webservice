"""Tests for CE-v2 answer metrics added alongside retrieval ranking.

Pure-comparison tests - no torch, no models. Extends test_ce_v2_gate.py;
the artifact files they reference are produced by evaluation/build_label_baseline.py
(and live in the gitignored evaluation/out tree).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.ce_v2_gate import _passed, answer_context_checks, answer_pair_checks, load_answers

ANSWER_BASELINE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "answers_baseline.json"
LABEL_BASELINE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "label_baseline.json"
SPLIT_FILE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "pairwise_train_split.json"


@pytest.fixture
def fixture_answers():
    """Two candidate runs: one slightly better, one regressed."""
    baseline = json.loads(ANSWER_BASELINE.read_text(encoding="utf-8"))["answers"]
    qids = sorted(baseline)[:21]
    cur = {q: dict(baseline[q]) for q in qids}
    for q in qids[:5]:
        cur[q]["binary"] = 1
        cur[q]["soft"] = max(cur[q]["soft"], baseline[q]["soft"] + 0.05)
    base = {q: baseline[q] for q in qids}
    return base, cur


def test_load_answers_jsonl_and_json():
    """load_answers tolerates the evaluator_v2 jsonl shape and baseline json."""
    jsonl = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "evaluator_v2_per_question.jsonl"
    by_jsonl = load_answers(jsonl)
    by_json = load_answers(ANSWER_BASELINE)
    assert len(by_jsonl) >= 100
    assert len(by_json) == len(by_jsonl)
    for q in list(by_json)[:3]:
        assert {"soft", "binary", "abstain_credit"} <= set(by_json[q].keys())


def test_answer_pair_checks_passes_improvement(fixture_answers):
    base, cur = fixture_answers
    checks = answer_pair_checks(cur, base)
    names = {c["name"] for c in checks}
    assert "answer binary_correct (paired)" in names
    assert "answer soft score (paired)" in names
    assert "answer paired flips (info)" in names
    hard = [c for c in checks if c["kind"] == "hard"]
    assert all(c["ok"] for c in hard)


def test_answer_pair_checks_fails_regression():
    base = {"Q1": {"soft": 0.8, "binary": 1, "abstain_credit": 0},
            "Q2": {"soft": 0.6, "binary": 1, "abstain_credit": 0}}
    cur = {"Q1": {"soft": 0.3, "binary": 0, "abstain_credit": 0},
           "Q2": {"soft": 0.2, "binary": 0, "abstain_credit": 0}}
    checks = answer_pair_checks(cur, base)
    hard = [c for c in checks if c["kind"] == "hard"]
    assert not all(c["ok"] for c in hard)
    flips = next(c for c in checks if c["name"] == "answer paired flips (info)")
    assert flips["current"] == "0+ / 2-"
    assert flips["ok"] is True


def test_answer_pair_checks_empty_returns_nothing():
    assert answer_pair_checks({"Q1": {"soft": 0.5, "binary": 0, "abstain_credit": 0}}, {}) == []
    assert answer_pair_checks({}, {"Q1": {"soft": 0.5, "binary": 0, "abstain_credit": 0}}) == []


def test_answer_context_checks_reads_baseline():
    if not LABEL_BASELINE.exists():
        pytest.skip("label baseline not built yet")
    lb = json.loads(LABEL_BASELINE.read_text(encoding="utf-8"))
    if not SPLIT_FILE.exists():
        pytest.skip("split file not present")
    split = json.loads(SPLIT_FILE.read_text(encoding="utf-8"))
    checks = answer_context_checks(lb, split["test_qids"])
    assert len(checks) == 3
    assert all(c["kind"] == "info" for c in checks)
    assert all(c["ok"] for c in checks)


def test_info_checks_never_fail_the_gate():
    checks = answer_pair_checks({}, {})
    checks += answer_context_checks({"per_qid": {}}, [])
    # info-kind checks with ok=False are ignored by _passed
    checks.append({"name": "fake", "current": 0, "reference": 1, "ok": False, "kind": "info", "direction": "-"})
    assert _passed(checks, False) is True
    assert _passed(checks, True) is True


def test_target_checks_respect_strict_flag():
    checks = [
        {"name": "a", "ok": False, "kind": "hard"},
        {"name": "b", "ok": False, "kind": "target"},
        {"name": "c", "ok": False, "kind": "info"},
    ]
    assert _passed(checks, False) is False  # hard fails regardless
    assert _passed(checks, True) is False
    checks[0]["ok"] = True
    assert _passed(checks, False) is True
    assert _passed(checks, True) is False
    checks[1]["ok"] = True
    assert _passed(checks, False) is True
    assert _passed(checks, True) is True

"""Offline tests for Step 0 residual labeling helpers (no network, no LLM)."""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.step0_label_residual import (
    STEP0_ENUM,
    normalize_labels,
    parse_residual_worksheet,
    publish_gate_targets,
    validate_labels,
)


RESIDUAL = ["Q001", "Q002", "Q003", "Q004"]


def test_enum_matches_plan():
    assert STEP0_ENUM == ("reference_narrow", "evidence_missing", "model_wrong")


def test_validate_complete_all_valid():
    labels = {
        "Q001": "evidence_missing",
        "Q002": "model_wrong",
        "Q003": "reference_narrow",
        "Q004": "evidence_missing",
    }
    v = validate_labels(labels, RESIDUAL)
    assert v["step0_complete"] is True
    assert v["n_missing"] == 0
    assert v["n_invalid"] == 0
    assert v["label_counts"] == {"evidence_missing": 2, "model_wrong": 1, "reference_narrow": 1}


def test_validate_missing_incomplete():
    labels = {"Q001": "model_wrong"}
    v = validate_labels(labels, RESIDUAL)
    assert v["step0_complete"] is False
    assert v["n_missing"] == 3
    assert set(v["missing_qids"]) == {"Q002", "Q003", "Q004"}


def test_validate_invalid_label_not_counted_as_valid():
    labels = {
        "Q001": "evidence_missing",
        "Q002": "genuinely_wrong",  # not in step0 enum
        "Q003": "model_wrong",
        "Q004": "reference_narrow",
    }
    v = validate_labels(labels, RESIDUAL)
    assert v["step0_complete"] is False
    assert v["n_invalid"] == 1
    assert v["invalid_labels"] == {"Q002": "genuinely_wrong"}
    assert v["n_labeled_valid"] == 3


def test_normalize_labels_dict_and_wrapper():
    raw = {"Q001": "model_wrong", "Q002": {"verdict": "evidence_missing"}}
    got = normalize_labels(raw, RESIDUAL)
    assert got == {"Q001": "model_wrong", "Q002": "evidence_missing"}
    got2 = normalize_labels({"labels": {"Q003": "reference_narrow"}}, RESIDUAL)
    assert got2 == {"Q003": "reference_narrow"}
    got3 = normalize_labels([{"qid": "Q004", "verdict": "model_wrong"}], RESIDUAL)
    assert got3 == {"Q004": "model_wrong"}


def test_normalize_labels_ignores_non_residual_and_empty():
    raw = {"Q999": "model_wrong", "Q001": "", "Q002": "  "}
    got = normalize_labels(raw, RESIDUAL)
    assert got == {}


def test_parse_worksheet_strips_comments_and_blank():
    md = """
### Q001 - EASY | x | [STEP0-RESIDUAL] | [BLANK]

**Your judgment:**
- human_correct: no
- verdict: evidence_missing  # pre-annotated — confirm or override (reference_narrow | evidence_missing | model_wrong)
- model_action: add_instrument_text
- category: A
- notes: water act s25 absent

---

### Q002 - EASY | x | [STEP0-RESIDUAL] | [BLANK]

**Your judgment:**
- human_correct: 
- verdict: # required: reference_narrow | evidence_missing | model_wrong
- model_action:  # fix_reference | add_instrument_text | contrastive_repair
- category: 
- notes: 

---
"""
    path = ROOT / "evaluation" / "out" / "ceiling_v5" / "_test_step0_ws.md"
    path.write_text(md, encoding="utf-8")
    try:
        got = parse_residual_worksheet(path)
        assert got == {"Q001": "evidence_missing"}
    finally:
        path.unlink(missing_ok=True)


def test_publish_gate_targets(tmp_path, monkeypatch):
    import evaluation.step0_label_residual as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    buckets = m.publish_gate_targets(
        {
            "Q001": "evidence_missing",
            "Q002": "model_wrong",
            "Q003": "reference_narrow",
            "Q004": "evidence_missing",
        }
    )
    assert buckets["evidence_missing"] == ["Q001", "Q004"]
    assert (tmp_path / "step0_corpus_fill_targets.json").exists()
    assert (tmp_path / "step0_contrastive_targets.json").exists()
    assert (tmp_path / "step0_dual_score_targets.json").exists()
    cf = json.loads((tmp_path / "step0_corpus_fill_targets.json").read_text())
    assert cf["n"] == 2
    assert cf["label"] == "evidence_missing"

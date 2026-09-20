"""Tests for S28 steps 4-5 — answer-error taxonomy + quantification."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.answer_error_taxonomy import (
    CATEGORIES,
    classify_answer_failure,
    classify_with_reasons,
    is_failure_verdict,
)
from evaluation.quantify_answer_failures import aggregate, iter_records


def _rec(**kw):
    base: dict = {"correct": False, "answer": "Section 31 applies."}
    base.update(kw)
    return base


class TestStep4Taxonomy:
    def test_twelve_categories(self):
        assert len(CATEGORIES) == 12
        assert set(CATEGORIES) == {
            "retrieval",
            "context_assembly",
            "extraction",
            "interpretation",
            "application",
            "exception",
            "definition",
            "multi_hop",
            "conflict",
            "completeness",
            "citation",
            "evaluation",
        }

    def test_correct_answer_has_no_failures(self):
        assert classify_answer_failure({"correct": True, "answer": "x"}) == []

    def test_retrieval(self):
        assert "retrieval" in classify_answer_failure(_rec(gold_available_in_pool=False))

    def test_context_assembly(self):
        cats = classify_answer_failure(_rec(gold_available_in_pool=True, gold_in_context=False))
        assert "context_assembly" in cats
        assert "retrieval" not in cats

    def test_extraction_needs_gold_in_context(self):
        cats = classify_answer_failure(_rec(gold_in_context=True, provision_correct=False))
        assert "extraction" in cats

    def test_interpretation_vs_application(self):
        assert "interpretation" in classify_answer_failure(_rec(provision_correct=True, legal_correct=False))
        assert "application" in classify_answer_failure(_rec(provision_correct=True, legal_correct=True))

    def test_exception_detector(self):
        cats = classify_answer_failure(
            _rec(context_text="Provided that sub-section (2) shall not apply.", answer="Section 31 applies.")
        )
        assert "exception" in cats

    def test_definition_detector(self):
        cats = classify_answer_failure(
            _rec(context_text='"Food" means any article used as food.', answer="Food business needs a licence.")
        )
        assert "definition" in cats

    def test_multi_hop_and_completeness(self):
        cats = classify_answer_failure(_rec(n_gold_units=3, n_gold_covered=1, completeness=False))
        assert "multi_hop" in cats
        assert "completeness" in cats

    def test_conflict(self):
        assert "conflict" in classify_answer_failure(_rec(has_conflicts=True))
        assert "conflict" in classify_answer_failure(_rec(temporal_conflict=True))

    def test_citation(self):
        assert "citation" in classify_answer_failure(_rec(citation_recall=0.2, citation_precision=0.9))
        assert "citation" in classify_answer_failure(_rec(answer="Some answer without markers."))

    def test_evaluation_mismatch(self):
        assert "evaluation" in classify_answer_failure(_rec(answer_jaccard=0.8))

    def test_manual_labels_kept(self):
        cats = classify_answer_failure({"correct": True, "manual_labels": ["exception", "bogus"]})
        assert cats == ["exception"]

    def test_fallback_needs_audit(self):
        assert classify_answer_failure({"correct": False}) == ["interpretation"]

    def test_unknown_verdict_fails_open(self):
        assert classify_answer_failure({}) == ["interpretation"]
        assert classify_answer_failure({"correct": "maybe"}) == ["interpretation"]
        assert classify_answer_failure({"correct": ""}) == ["interpretation"]

    def test_verdict_semantics(self):
        assert is_failure_verdict(True) is False
        assert is_failure_verdict(False) is True
        assert is_failure_verdict(1) is False
        assert is_failure_verdict(0) is True
        assert is_failure_verdict(0.7) is False
        assert is_failure_verdict(0.3) is True
        assert is_failure_verdict(None) is True
        assert is_failure_verdict("maybe") is True

    def test_markers_need_word_boundaries(self):
        assert "exception" not in classify_answer_failure(_rec(context_text="No exception here."))
        assert "definition" not in classify_answer_failure(_rec(context_text="This means the result is clear."))
        assert "definition" not in classify_answer_failure(_rec(context_text="The report includes three tables."))
        assert "definition" in classify_answer_failure(
            _rec(context_text='"Food" means any staple article.', answer="Licence needed.")
        )

    def test_zero_marker_citation_suppressed_upstream(self):
        assert classify_answer_failure(_rec(gold_available_in_pool=False)) == ["retrieval"]
        assert classify_answer_failure(_rec(gold_available_in_pool=True, gold_in_context=False)) == ["context_assembly"]
        cats = classify_answer_failure(_rec(gold_available_in_pool=False, citation_recall=0.2))
        assert "retrieval" in cats and "citation" in cats  # measured recall still reports

    def test_reasons_are_isolated(self):
        r1 = _rec(gold_available_in_pool=False)
        r2 = _rec(citation_recall=0.1)
        classify_answer_failure(r1)
        reasons = classify_with_reasons(r2)
        assert set(reasons) == set(classify_answer_failure(r2))
        assert "retrieval" not in reasons


class TestStep5Quantification:
    def test_exp_c_shape(self):
        payload = {
            "q1": {
                "n_gold_units": 1,
                "oracle_conditions": {"O3_full_support": {"status": "ok", "correct": False, "citation_recall": 0.1}},
            },
            "q2": {
                "n_gold_units": 1,
                "oracle_conditions": {"O3_full_support": {"status": "ok", "correct": True}},
            },
        }
        rows = iter_records(payload)
        assert len(rows) == 2
        result = aggregate(rows)
        assert result["n_questions"] == 2
        assert result["n_failures"] == 1
        assert result["distribution"]["citation"]["count"] == 1
        assert "q1" in result["per_question"]

    def test_json_round_trip(self, tmp_path):
        payload = {"q1": {"correct": False, "gold_available_in_pool": False}}
        path = tmp_path / "per_question.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = aggregate(iter_records(json.loads(path.read_text(encoding="utf-8"))))
        assert result["distribution"]["retrieval"]["count"] == 1

    def test_unknown_verdict_counted_with_category(self):
        result = aggregate(iter_records({"q1": {}}))
        assert result["n_failures"] == 1
        assert result["distribution"]["interpretation"]["count"] == 1
        assert result["per_question"] == {"q1": ["interpretation"]}


class TestAuditWorksheet:
    def _rows(self):
        return iter_records(
            {f"q{i}": {"correct": False, "gold_available_in_pool": False, "answer": f"Answer {i}."} for i in range(6)}
            | {"q6": {"correct": True, "answer": "Fine."}}
        )

    def test_sample_is_deterministic_and_bounded(self):
        from evaluation.quantify_answer_failures import sample_worksheet

        rows = self._rows()
        first = sample_worksheet(rows, n_total=4, seed=7)
        second = sample_worksheet(rows, n_total=4, seed=7)
        assert first == second
        assert len(first) == 4
        assert all("question_id" in e and "categories" in e and "reasons" in e for e in first)

    def test_sample_skips_correct_and_stratifies(self):
        from evaluation.quantify_answer_failures import render_worksheet, sample_worksheet

        rows = self._rows() + iter_records({"qx": {"correct": False, "citation_recall": 0.1, "answer": "Wrong [9]."}})
        entries = sample_worksheet(rows, n_total=40, seed=1)
        assert len(entries) == 7  # 6 retrieval + 1 citation-flavoured, correct excluded
        assert any("citation" in e["categories"] for e in entries)
        md = render_worksheet(entries)
        assert md.startswith("# Answer-failure audit worksheet")
        assert "q6" not in md

"""Tests for the provision-extraction eval / backfill / training harness.

All pure: synthetic payload records stand in for the corpus, and no Qdrant,
network, or optional ML dependency is required.
"""

from __future__ import annotations

from typing import Any

import scripts.backfill_provision_extraction as bfe
import scripts.train_provision_boundaries as tpb
from app.rag.provision_extractor import ProvisionRecord
from evaluation.provision_extraction_eval import (
    boundary_prf,
    classify_gold_misses,
    evaluate,
    gold_reference_keys,
    group_payload_documents,
    noise_stamp_rate,
    per_family_prf,
    predict_documents,
    predicted_keys,
)
from evaluation.provision_significance import (
    bootstrap_significance,
    bootstrap_significance_report,
)

FSS_ACT = "Food Safety and Standards Act, 2006"

DOC_ID = "fss_act_2006"

_SECTIONS = {
    "26": (
        "26. Responsibilities of the food business operator.— Every food business operator shall "
        "ensure that the articles of food satisfy the requirements of this Act."
    ),
    "31": "31. Licensing and registration.— No person shall commence any food business except under a licence.",
    "50": (
        "50. Penalty for selling food not of the nature or substance or quality demanded.— Any person "
        "who sells shall be liable to a penalty."
    ),
}


def _record(chunk_id: str, index: int, text: str, *, section_number: str | None = None, document_type: str = "act"):
    return {
        "id": chunk_id,
        "payload": {
            "document_id": DOC_ID,
            "chunk_index": index,
            "chunk_text": text,
            "act_name": FSS_ACT,
            "document_title": FSS_ACT,
            "document_type": document_type,
            "section_number": section_number,
            "sections_covered": [],
            "confidence": 0.9,
        },
    }


def _fss_records():
    return [
        _record("c0", 0, _SECTIONS["26"], section_number="26"),
        _record("c1", 1, _SECTIONS["31"], section_number="31"),
        _record("c2", 2, _SECTIONS["50"], section_number="50"),
    ]


def _gold_records():
    return {
        "fssai:s26": {"document_id": DOC_ID, "section": "26", "act": FSS_ACT},
        "fssai:s31": {"document_id": DOC_ID, "section": "31", "act": FSS_ACT},
        "fssai:s50": {"document_id": DOC_ID, "section": "50", "act": FSS_ACT},
        "fssai:s99": {"document_id": DOC_ID, "section": "99", "act": FSS_ACT},
    }


# --------------------------------------------------------------------------- #
# Grouping + prediction
# --------------------------------------------------------------------------- #


class TestGrouping:
    def test_groups_and_orders_chunks(self):
        shuffled = [_fss_records()[2], _fss_records()[0], _fss_records()[1]]
        groups = group_payload_documents(shuffled)
        assert list(groups) == [DOC_ID]
        assert [chunk["chunk_id"] for chunk in groups[DOC_ID].chunks] == ["c0", "c1", "c2"]
        assert groups[DOC_ID].act_name == FSS_ACT

    def test_predict_documents_emits_gold_keys(self):
        groups = group_payload_documents(_fss_records())
        predictions = predict_documents(groups)
        keys = predicted_keys(predictions[DOC_ID])
        assert keys == {("fssai", "26"), ("fssai", "31"), ("fssai", "50")}


# --------------------------------------------------------------------------- #
# Metric primitives
# --------------------------------------------------------------------------- #


class TestMetricPrimitives:
    def test_boundary_prf(self):
        result = boundary_prf({("fssai", "26"), ("fssai", "31")}, {("fssai", "26"), ("fssai", "50")})
        assert result["tp"] == 1
        assert result["fp"] == 1
        assert result["fn"] == 1
        assert result["precision"] == 0.5
        assert result["recall"] == 0.5
        assert result["f1"] == 0.5

    def test_per_family_prf(self):
        result = per_family_prf({("fssai", "26"), ("air_act", "15")}, {("fssai", "26")})
        assert set(result) == {"fssai", "air_act"}
        assert result["fssai"]["f1"] == 1.0
        assert result["air_act"]["precision"] == 0.0

    def test_gold_reference_keys_uses_registry_section(self):
        refs = gold_reference_keys(_gold_records())
        assert refs[DOC_ID] == {("fssai", "26"), ("fssai", "31"), ("fssai", "50"), ("fssai", "99")}


# --------------------------------------------------------------------------- #
# Full evaluation
# --------------------------------------------------------------------------- #


class TestEvaluate:
    def test_report_metrics(self):
        groups = group_payload_documents(_fss_records())
        report = evaluate(groups, _gold_records())
        micro = report["boundary"]["micro"]
        assert micro["tp"] == 3
        assert micro["fp"] == 0
        assert micro["fn"] == 1
        assert micro["precision"] == 1.0
        assert micro["recall"] == 0.75
        assert report["noise_stamp_rate"]["rate"] == 0.0
        assert report["gold_resolution"]["resolved"] == 3
        assert report["gold_resolution"]["misses"] == ["fssai:s99"]

    def test_noise_stamp_rate_flags_out_of_range(self):
        record = ProvisionRecord(provision_id="fssai:s999", family_id="fssai", section="999", text="x")
        rate = noise_stamp_rate({DOC_ID: [record]}, {DOC_ID: group_payload_documents(_fss_records())[DOC_ID]})
        assert rate["noise"] == 1
        assert rate["samples"][0]["reason"] == "out_of_range"


class TestGoldMissTriage:
    def test_buckets_separate_absent_text_from_bad_boundary(self):
        groups = group_payload_documents(_fss_records())
        predictions = predict_documents(groups)
        gold = {
            "fssai:s26": {"document_id": DOC_ID, "section": "26"},  # resolved
            "fssai:s99": {"document_id": DOC_ID, "section": "99"},  # bad boundary
            "fssai:food_regulations": {"document_id": DOC_ID, "section": None},  # instrument level
            "air_act:s15": {"document_id": "air_act_1981", "section": "15"},  # absent text
        }
        triage = classify_gold_misses(predictions, groups, gold)
        assert triage["resolved"] == 1
        assert triage["bad_boundary"]["count"] == 1
        assert triage["instrument_level"]["count"] == 1
        assert triage["document_absent"]["count"] == 1
        assert "air_act:s15" in triage["document_absent"]["provisions"]


# --------------------------------------------------------------------------- #
# Backfill planning
# --------------------------------------------------------------------------- #


class TestBackfillPlanning:
    def test_plan_maps_provisions_to_chunks(self):
        groups = group_payload_documents(_fss_records())
        plan = bfe.plan_for_groups(groups)
        assert plan["records"] == 3
        assert plan["points_planned"] == 3
        assert plan["validation_errors"] == []
        assert plan["updates"]["c0"]["provision_ids"] == ["fssai:s26"]
        assert plan["updates"]["c0"]["provision_spans"][0]["section"] == "26"

    def test_validate_record(self):
        good = ProvisionRecord(provision_id="fssai:s26", family_id="fssai", section="26", text="x")
        assert bfe.validate_record(good, FSS_ACT) == []
        bad = ProvisionRecord(provision_id="bad", family_id="fssai", section="999", text="x")
        problems = bfe.validate_record(bad, FSS_ACT)
        assert any("gold_grammar_roundtrip_failed" in problem for problem in problems)
        assert any("section_out_of_act_range" in problem for problem in problems)

    def test_apply_updates_batches_by_field_set(self):
        class _FakeClient:
            def __init__(self):
                self.calls = []

            def set_payload(self, collection_name, payload, points):
                self.calls.append((collection_name, payload, sorted(points)))

        class _FakeStore:
            def __init__(self, client):
                self._client = client

            def _require_client(self):
                return self._client

        client = _FakeClient()
        updates = {
            "c0": {"provision_ids": ["fssai:s26"], "provision_confidence": 0.9, "provision_modality": "obligation"},
            "c1": {"provision_ids": ["fssai:s26"], "provision_confidence": 0.9, "provision_modality": "obligation"},
        }
        written = bfe.apply_updates(_FakeStore(client), updates, "fssai_legal_768")
        assert written == 2
        assert len(client.calls) == 1  # identical field-sets share one call
        assert client.calls[0][2] == ["c0", "c1"]


# --------------------------------------------------------------------------- #
# Training labels
# --------------------------------------------------------------------------- #


class TestBootstrapSignificance:
    """Smoke tests for the paired-bootstrap adoption gate.

    The bootstrap is computed from two ``evaluate()`` reports (rules vs hybrid)
    over the SAME document set, so these tests build synthetic per-document
    metric dicts and pass them directly to ``bootstrap_significance``.
    """

    def _per_doc(self, recall: float, tp: int, fn: int) -> dict[str, Any]:
        """A single fake ``boundary_prf``-shaped per-document metric dict."""
        return {
            "precision": round(tp / max(tp + 0, 1), 6),
            "recall": recall,
            "f1": 0.0,
            "tp": tp,
            "fp": 0,
            "fn": fn,
        }

    def test_adopt_when_ci_excludes_zero_for_both_metrics(self):
        """When both metrics have a non-overlapping CI above zero, adopt."""
        rules_report = {
            "per_document": {
                "d0": self._per_doc(recall=0.3, tp=3, fn=7),
                "d1": self._per_doc(recall=0.5, tp=5, fn=5),
            },
        }
        hybrid_report = {
            "per_document": {
                "d0": self._per_doc(recall=0.9, tp=7, fn=3),
                "d1": self._per_doc(recall=0.9, tp=7, fn=3),
            },
        }
        # N=2 paired docs with a large per-doc gap: the percentile CI of
        # (hybrid - rules) mean difference should exclude 0.
        result = bootstrap_significance(
            rules_report,
            hybrid_report,
            ["d0", "d1"],
            iterations=50_000,
            seed=1,
        )
        assert result["adopt_hybrid"] is True
        assert all(r["significant"] for r in result["rows"])

    def test_no_adopt_when_ci_includes_zero(self):
        """When the per-doc difference is small, the CI includes zero -> no adopt."""
        rules_report = {
            "per_document": {
                "d0": self._per_doc(recall=0.8, tp=8, fn=2),
                "d1": self._per_doc(recall=0.8, tp=4, fn=1),
            },
        }
        hybrid_report = {
            "per_document": {
                "d0": self._per_doc(recall=0.81, tp=8, fn=2),
                "d1": self._per_doc(recall=0.82, tp=4, fn=1),
            },
        }
        result = bootstrap_significance(
            rules_report,
            hybrid_report,
            ["d0", "d1"],
            iterations=50_000,
            seed=1,
        )
        assert result["adopt_hybrid"] is False

    def test_bootstrap_significance_report_folds_rows_and_triage_delta(self):
        """The report wrapper returns a ``{"significance": ..., "gold_miss_triage_delta": ...}`` shape"""
        rules_report = {
            "per_document": {
                "d0": self._per_doc(recall=0.4, tp=4, fn=6),
            },
        }
        hybrid_report = {
            "per_document": {
                "d0": self._per_doc(recall=0.9, tp=9, fn=1),
            },
        }
        predictions_rules = []
        predictions_hybrid = []
        result = bootstrap_significance_report(
            rules_report,
            hybrid_report,
            ["d0"],
            predictions_rules=predictions_rules,
            predictions_hybrid=predictions_hybrid,
            groups={},
            gold_records={},
        )
        assert "significance" in result
        assert "rows" in result["significance"]
        assert result["significance"]["rows"]  # at least one row
        assert isinstance(result["gold_miss_triage_delta"], int)


class TestTrainingLabels:
    def test_silver_labels_from_chunk_section(self):
        records = [
            _record("c0", 0, _SECTIONS["26"], section_number="26"),
            _record("c1", 1, "88. Widget duties.— A person may act.", section_number=None),
        ]
        groups = group_payload_documents(records)
        rows = tpb.build_training_rows(groups)
        labels = {(row.document_id, row.label) for row in rows}
        assert len(rows) == 2
        assert any(row.label == 1 for row in rows)
        assert any(row.label == 0 for row in rows)
        assert labels == {(DOC_ID, 1), (DOC_ID, 0)}

    def test_excludes_regulation_documents(self):
        records = [_record("c0", 0, _SECTIONS["26"], section_number="26", document_type="regulation")]
        groups = group_payload_documents(records)
        assert tpb.build_training_rows(groups) == []

    def test_split_by_document_is_group_aware(self):
        rows = [
            tpb.TrainingRow(features={}, label=1, document_id="doc_a", source_pattern="engine_main"),
            tpb.TrainingRow(features={}, label=0, document_id="doc_a", source_pattern="engine_main"),
            tpb.TrainingRow(features={}, label=1, document_id="doc_b", source_pattern="engine_main"),
            tpb.TrainingRow(features={}, label=0, document_id="doc_c", source_pattern="engine_main"),
        ]
        train, test = tpb.split_by_document(rows, test_fraction=0.25, seed=1)
        assert {row.document_id for row in train}.isdisjoint({row.document_id for row in test})
        assert train and test

    def test_binary_metrics(self):
        metrics = tpb._binary_metrics([1, 0, 1, 0], [1, 0, 0, 1])
        assert metrics["accuracy"] == 0.5
        assert metrics["precision"] == 0.5
        assert metrics["recall"] == 0.5
        assert metrics["f1"] == 0.5

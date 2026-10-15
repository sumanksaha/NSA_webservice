"""Offline tests for the decision-pathway eval tooling (plan step 2).

Covers the pure metric/aggregation helpers of ``evaluation/tau_sweep.py`` and
``evaluation/ab_decision_selector.py`` plus the keep-score cache round-trip in
``evaluation/decision_scores.py``.  No network, no model calls: the cache is a
temp file and the aggregations run on synthetic rows.
"""

from __future__ import annotations

import pytest

from evaluation.ab_decision_selector import _agg, _paired
from evaluation.decision_scores import append_keep_score, decision_model, load_keep_scores
from evaluation.tau_sweep import _unit_rate


class TestDecisionModelAllowlist:
    """Only typesafe/jev-1.13 may score; the banned jev-router, the
    dropped mercury-decide:free, and deepseek etc. are refused."""

    def test_default_is_jev_113(self, monkeypatch):
        import evaluation.decision_scores as ds

        monkeypatch.delenv("RAG_DECISION_MODEL", raising=False)
        assert decision_model() == ds.DEFAULT_MODEL == "typesafe/jev-1.13"

    @pytest.mark.parametrize("model", ["typesafe/jev-1.13"])
    def test_sanctioned_models_pass(self, monkeypatch, model):
        monkeypatch.setenv("RAG_DECISION_MODEL", model)
        assert decision_model() == model

    @pytest.mark.parametrize(
        "model",
        [
            "typesafe/jev-router",  # banned 2026-10-15: routed to deepseek
            "inception/mercury-decide:free",  # dropped 2026-10-15 after the head-to-head
            "inception/mercury-decide",  # paid tier — never sanctioned
            "deepseek/deepseek-v4.1-flash",
            "google/gemini-3.8-flash",
            "meta/muse-spark-1.2",
        ],
    )
    def test_unsanctioned_models_refused(self, monkeypatch, model):
        monkeypatch.setenv("RAG_DECISION_MODEL", model)
        with pytest.raises(RuntimeError, match="sanctioned"):
            decision_model()


class TestUnitRate:
    def test_perfect_selection(self):
        assert _unit_rate(["a", "b"], {"a", "b"}) == (1.0, 1.0, 1.0)

    def test_partial_overlap(self):
        p, r, f1 = _unit_rate(["a", "c"], {"a", "b"})
        assert p == 0.5
        assert r == 0.5
        assert f1 == 0.5

    def test_empty_kept_with_achievable_gold_scores_zero(self):
        assert _unit_rate([], {"a"}) == (0.0, 0.0, 0.0)

    def test_empty_kept_and_no_gold_is_vacuous_perfect(self):
        assert _unit_rate([], set()) == (1.0, 1.0, 1.0)


def _row(bc: int, acc: float) -> dict:
    return {
        "m": {
            "binary_correct": bc,
            "answer_correctness": acc,
            "citation_recall": 0.5,
            "gold_reachable": 1.0,
            "groundedness_score": 0.9,
            "hallucination_detected": 0,
            "confidence": 0.7,
            "dangling_marker_rate": 0.0,
            "n_prompt_chunks": 6,
        }
    }


class TestAbAggregations:
    def test_agg_means_present_keys(self):
        rows = {"q1": _row(1, 0.8), "q2": _row(0, 0.4)}
        out = _agg(rows, ["q1", "q2"])
        assert out["binary_correct"] == 0.5
        assert out["answer_correctness"] == 0.6

    def test_agg_missing_metrics_degrade_to_zero_not_crash(self):
        rows = {"q1": {"m": {"binary_correct": 1}}}
        out = _agg(rows, ["q1"])
        assert out["binary_correct"] == 1.0
        assert out["citation_recall"] == 0.0  # absent key → no values → 0

    def test_paired_counts_and_net(self):
        base = {"q1": _row(1, 0.8), "q2": _row(0, 0.4)}
        arm = {"q1": _row(1, 0.8), "q2": _row(1, 0.4)}  # q2 improved
        out = _paired(base, arm, ["q1", "q2"])
        assert out["improved"] == 1
        assert out["regressed"] == 0
        assert out["net"] == 1
        assert out["mean_delta"] == 0.5


class TestKeepScoreCache:
    def test_last_record_per_question_wins(self, tmp_path, monkeypatch):
        import evaluation.decision_scores as ds

        monkeypatch.setattr(ds, "SCORE_CACHE", tmp_path / "scores.jsonl")
        append_keep_score({"question_id": "q1", "scores": {"c1": 0.1}, "error": None})
        append_keep_score({"question_id": "q1", "scores": {"c1": 0.9}, "error": None})
        append_keep_score({"question_id": "q2", "scores": {"c2": 0.5}, "error": None})

        cache = load_keep_scores()
        assert cache["q1"]["scores"]["c1"] == 0.9  # last wins
        assert cache["q2"]["scores"]["c2"] == 0.5

    def test_missing_cache_file_is_empty(self, tmp_path, monkeypatch):
        import evaluation.decision_scores as ds

        monkeypatch.setattr(ds, "SCORE_CACHE", tmp_path / "nope.jsonl")
        assert load_keep_scores() == {}

    def test_records_filter_by_model(self, tmp_path, monkeypatch):
        """The file holds every model's runs; one calibration sees only its own."""
        import evaluation.decision_scores as ds

        monkeypatch.setattr(ds, "SCORE_CACHE", tmp_path / "scores.jsonl")
        append_keep_score({"question_id": "q1", "model": "inception/mercury-decide:free", "scores": {"c1": 0.9}})
        append_keep_score({"question_id": "q1", "model": "typesafe/jev-1.13", "scores": {"c1": 0.2}})

        assert load_keep_scores("inception/mercury-decide:free")["q1"]["scores"]["c1"] == 0.9
        assert load_keep_scores("typesafe/jev-1.13")["q1"]["scores"]["c1"] == 0.2
        assert load_keep_scores("some/other-model") == {}
        assert "q1" in load_keep_scores()  # unfiltered sees everything

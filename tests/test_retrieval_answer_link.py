"""Tests for the retrieval->answer metric bridge and the training question pool.

Pure-function tests: no network, no LLM, no Qdrant. The module under test is
``evaluation/retrieval_answer_link`` (answer-level metrics joining gold depth
to soft/binary/human correctness) plus ``evaluation.build_train_pool``
(question-pool classification + deterministic oversampling).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.build_train_pool import apply_pool_weights, classify, qid_weight_map
from evaluation.retrieval_answer_link import (
    WINDOW_A_B_CAUSAL,
    _agg,
    bridge,
    point_biserial,
    spearman,
)


def _row(qid, verdict, bucket, action, depth, in_prompt, soft, binary):
    return {
        "qid": qid,
        "verdict": verdict,
        "bucket": bucket,
        "model_action": action,
        "gold_depth": depth,
        "gold_in_prompt": in_prompt,
        "soft": soft,
        "binary": binary,
        "abstain_credit": 0,
    }


ROWS = [
    _row("Q1", "model_wrong", "retrieval_rank", "provision_check", 30, False, 0.20, 0),
    _row("Q2", "model_wrong", "generation", "abstention_gate", 5, True, 0.50, 1),
    _row("Q3", "evaluator_miss", "retrieval_rank", "none", 40, False, 0.70, 1),
    _row("Q4", "model_wrong", "never_retrieved", "provision_check", None, False, 0.10, 0),
]


class TestCorrelations:
    def test_spearman_perfect(self):
        assert spearman([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
        assert spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == -1.0

    def test_spearman_degenerate(self):
        assert spearman([1.0, 1.0], [1.0, 2.0]) is None  # too short
        assert spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None  # no variance

    def test_point_biserial_sign(self):
        # binary separates low vs high continuous values -> strong negative r
        r = point_biserial([1, 1, 0, 0], [1.0, 2.0, 9.0, 10.0])
        assert r is not None and r < -0.9

    def test_point_biserial_single_class(self):
        assert point_biserial([1, 1, 1], [1.0, 2.0, 3.0]) is None


class TestAgg:
    def test_human_correct_rate(self):
        a = _agg(ROWS)
        assert a["n"] == 4
        assert a["human_correct_rate"] == 0.25  # only Q3 is not model_wrong
        assert a["binary_rate"] == 0.5

    def test_empty(self):
        assert _agg([])["human_correct_rate"] is None


class TestBridge:
    def test_conversion_views(self):
        b = bridge(ROWS)
        # in-prompt: only Q2 (binary 1, human wrong)
        assert b["binary_by_gold_in_prompt"]["in_prompt"]["binary_rate"] == 1.0
        assert b["binary_by_gold_in_prompt"]["in_prompt"]["human_correct_rate"] == 0.0
        # pool-only: Q1 (0) + Q3 (1)
        assert b["binary_by_gold_in_prompt"]["gold_in_pool_not_in_prompt"]["binary_rate"] == 0.5

    def test_ranker_upside_dual_band(self):
        b = bridge(ROWS)
        up = b["ranker_upside"]["R@10"]
        # promotable: model_wrong, depth>10, not in prompt -> only Q1
        assert up["promotable_model_wrong"] == 1
        # optimistic = 1 * conv(in_prompt)=1.0 / 4 ; causal = 0/7
        assert up["projected_binary_gain_optimistic"] == 0.25
        assert up["projected_binary_gain_causal"] == 0.0
        assert WINDOW_A_B_CAUSAL == (0, 7)

    def test_buckets_partition_model_wrong(self):
        b = bridge(ROWS)
        n = sum(v["n"] for v in b["by_bucket_model_wrong"].values())
        assert n == b["n_model_wrong_joined"] == 3
        assert b["by_bucket_model_wrong"]["retrieval_rank"]["n"] == 1


class TestPool:
    def _attr_rows(self):
        # 4 synthetic attribution rows mirroring the real classification rules
        return [
            {"qid": "P1", "verdict": "model_wrong", "bucket": "retrieval_rank", "model_action": "provision_check"},
            {"qid": "P2", "verdict": "model_wrong", "bucket": "retrieval_rank", "model_action": "abstention_gate"},
            {"qid": "G1", "verdict": "evaluator_miss", "bucket": "retrieval_rank", "model_action": "none"},
            {"qid": "U1", "verdict": "model_wrong", "bucket": "generation", "model_action": "needs_deeper_reasoning"},
        ]

    def test_classes_disjoint_and_complete(self):
        pool = classify(self._attr_rows())
        assert pool["priority_provision"] == ["P1"]
        assert pool["priority_promotable"] == ["P2"]
        assert pool["guard"] == ["G1"]
        assert pool["untrainable"] == ["U1"]
        all_q = [q for qs in pool.values() for q in qs]
        assert len(all_q) == len(set(all_q)) == 4

    def test_weight_map_and_oversampling(self):
        pool = {
            "weights": {"priority_provision": 3.0, "priority_promotable": 2.0, "guard": 1.0, "untrainable": 0.0},
            "qids": {
                "priority_provision": ["P1"],
                "priority_promotable": ["P2"],
                "guard": ["G1"],
                "untrainable": ["U1"],
            },
        }
        wm = qid_weight_map(pool)
        pairs = [{"question_id": q, "tier": 2} for q in ("P1", "P2", "G1", "U1")]
        out, info = apply_pool_weights(pairs, wm)
        # P1 x3 (2 extra), P2 x2 (1 extra), G1/U1 untouched (no drop)
        assert info == {"added_pairs": 3, "qids_boosted": 2}
        assert len(out) == 7
        assert sum(1 for p in out if p["question_id"] == "P1") == 3
        assert sum(1 for p in out if p["question_id"] == "U1") == 1  # weight 0 keeps one copy

"""``ranker_upside`` — the dual band and the promotable rule.

``tests/test_retrieval_answer_link.py`` pins the bridge on four synthetic rows
with exactly one promotable question, which is enough to fix the arithmetic
but not the *rules* that decide what counts as promotable or what the two
bands mean. These tests cover the rules.

The load-bearing property: the optimistic and causal bands must be the same
formula with different conversion rates, so they are directly comparable and
the gap between them is a real measurement rather than two unrelated numbers.
"""

from __future__ import annotations

import pytest

from evaluation.retrieval_answer_link import (
    RANK_CUTOFFS,
    WINDOW_A_B_CAUSAL,
    _agg,
    bridge,
    point_biserial,
    ranker_upside,
    spearman,
)


def row(
    qid,
    *,
    verdict="model_wrong",
    bucket="retrieval_rank",
    action="provision_check",
    depth=None,
    in_prompt=False,
    soft=0.3,
    binary=0,
):
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


class TestPromotableRule:
    def test_reachable_by_reordering_but_not_yet_shown(self):
        rows = [row("A", depth=30)]
        assert ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)["R@10"]["promotable_qids"] == ["A"]

    def test_gold_already_in_prompt_is_not_promotable(self):
        """Nothing to fix — the model already had it and still failed."""
        rows = [row("A", depth=3, in_prompt=True)]
        assert (
            ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)["R@10"]["promotable_model_wrong"] == 0
        )

    def test_never_retrieved_is_not_promotable(self):
        """No reordering reaches evidence that was not retrieved at all.

        This is the distinction that makes 'corpus gap' and 'ranking failure'
        separate populations instead of one bucket.
        """
        rows = [row("A", depth=None)]
        assert (
            ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)["R@10"]["promotable_model_wrong"] == 0
        )

    def test_evaluator_miss_is_never_promotable(self):
        """A grader error is not a training or fixing opportunity.

        Folding these in is the §5.3 artifact: 38 evaluator misses read as
        model error and would inflate the ranker's apparent upside.
        """
        rows = [row("A", verdict="evaluator_miss", depth=30)]
        assert (
            ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)["R@10"]["promotable_model_wrong"] == 0
        )

    def test_depth_sensitivity_across_cutoffs(self):
        """Gold at 8 / 25 / 50 is reachable at different cut-offs."""
        rows = [row("A", depth=8), row("B", depth=25), row("C", depth=50)]
        got = ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)
        assert got["R@5"]["promotable_model_wrong"] == 3
        assert got["R@10"]["promotable_model_wrong"] == 2
        assert got["R@20"]["promotable_model_wrong"] == 2

    def test_qids_are_reported_so_the_projection_is_auditable(self):
        rows = [row("A", depth=30), row("B", depth=40)]
        assert ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)["R@10"]["promotable_qids"] == [
            "A",
            "B",
        ]


class TestDualBand:
    def test_both_bands_share_one_formula(self):
        """Identical population share, different conversion rate."""
        rows = [row("A", depth=30), row("B", depth=30)]
        band = ranker_upside(rows, conversion_in_prompt=0.8, causal_conversion=0.1)["R@10"]
        assert band["population_share"] == 1.0
        assert band["projected_binary_gain_optimistic"] == pytest.approx(0.8)
        assert band["projected_binary_gain_causal"] == pytest.approx(0.1)

    def test_causal_band_uses_the_measured_window_ab(self):
        assert WINDOW_A_B_CAUSAL == (0, 7)
        rows = [row("A", depth=30)]
        band = ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)["R@10"]
        assert band["projected_binary_gain_causal"] == 0.0

    def test_optimistic_band_exceeds_the_causal_one_when_the_inputs_say_so(self):
        """The ordering is an assumption about the inputs, not of the formula.

        Both bands are `population_share * conversion`, and both conversions
        are caller-supplied floats — so nothing structurally stops them being
        passed the wrong way round. That would invert the module's conclusion,
        so it is recorded rather than silently produced.
        """
        rows = [row(f"Q{i}", depth=30) for i in range(5)]
        conv = [0.0, 0.1, 0.5, 0.9, 1.0]
        for c in conv:
            for causal in conv:
                band = ranker_upside(rows, conversion_in_prompt=c, causal_conversion=causal)["R@10"]
                if causal > c:
                    assert band["band_ordering"] == "inverted"
                else:
                    assert band["band_ordering"] == "as_expected"
                    assert band["projected_binary_gain_causal"] <= band["projected_binary_gain_optimistic"]

    def test_swapped_conversions_are_called_out_in_the_note(self):
        band = ranker_upside([row("A", depth=30)], conversion_in_prompt=0.05, causal_conversion=1.0)[
            "conversion_source"
        ]
        assert band["band_ordering"] == "inverted"
        assert "swapped" in band["note"]

    def test_projection_is_in_population_binary_rate_units(self):
        """So it is comparable to the measured binary rate, not a ratio."""
        rows = [row("A", depth=30), row("B", depth=None), row("C", depth=None), row("D", depth=None)]
        band = ranker_upside(rows, conversion_in_prompt=1.0, causal_conversion=0.0)["R@10"]
        assert band["population_share"] == 0.25
        assert band["projected_binary_gain_optimistic"] == 0.25

    def test_missing_conversion_is_none_not_zero(self):
        """No in-prompt population means the conversion is unknown, not 0."""
        rows = [row("A", depth=30)]
        band = ranker_upside(rows, conversion_in_prompt=None, causal_conversion=0.0)["R@10"]
        assert band["projected_binary_gain_optimistic"] is None
        assert band["projected_binary_gain_causal"] == 0.0

    def test_conversion_source_is_documented_in_the_payload(self):
        band = ranker_upside([row("A", depth=30)], conversion_in_prompt=1.0, causal_conversion=0.0)
        assert "conversion_source" in band
        assert "0/7" in band["conversion_source"]["causal_conversion"]

    def test_every_cutoff_is_reported(self):
        band = ranker_upside([row("A", depth=30)], conversion_in_prompt=1.0, causal_conversion=0.0)
        for k in RANK_CUTOFFS:
            assert f"R@{k}" in band


class TestEdgeCases:
    def test_empty_population_does_not_divide_by_zero(self):
        band = ranker_upside([], conversion_in_prompt=None, causal_conversion=0.0)
        assert band["R@10"]["population_share"] == 0.0
        assert band["R@10"]["projected_binary_gain_causal"] == 0.0

    def test_bridge_on_empty_rows(self):
        b = bridge([])
        assert b["aggregate"]["n"] == 0
        assert b["aggregate"]["human_correct_rate"] is None
        assert b["n_model_wrong_joined"] == 0

    def test_presence_groups_partition_the_population(self):
        rows = [
            row("A", depth=3, in_prompt=True),
            row("B", depth=30),
            row("C", depth=None),
        ]
        groups = bridge(rows)["binary_by_gold_in_prompt"]
        assert sum(g["n"] for g in groups.values()) == len(rows)

    def test_buckets_cover_only_model_failures(self):
        rows = [
            row("A", bucket="retrieval_rank"),
            row("B", verdict="evaluator_miss", bucket="retrieval_rank"),
            row("C", bucket="generation"),
        ]
        b = bridge(rows)
        assert b["n_model_wrong_joined"] == 2
        assert sum(g["n"] for g in b["by_bucket_model_wrong"].values()) == 2

    def test_human_correct_excludes_evaluator_misses(self):
        """An evaluator miss is not the model being right — it is a bad label."""
        rows = [
            row("A", verdict="model_wrong"),
            row("B", verdict="evaluator_miss"),
            row("C", verdict="reference_narrow"),
        ]
        agg = _agg(rows)
        assert agg["human_correct_rate"] == pytest.approx(2 / 3)
        assert agg["n_model_wrong"] == 1


class TestCorrelationEdges:
    def test_spearman_ties_use_average_ranks(self):
        assert spearman([1.0, 2.0, 2.0, 5.0], [1.0, 2.0, 2.0, 9.0]) == pytest.approx(1.0)

    def test_spearman_mismatched_lengths_is_none(self):
        assert spearman([1.0, 2.0, 3.0], [1.0, 2.0]) is None

    def test_point_biserial_uses_population_sd(self):
        """Sample sd would give -0.859 and silently weaken every correlation.

        The inputs are the complete observed population of questions, so the
        n-1 correction is the wrong estimator here.
        """
        assert point_biserial([1, 1, 0, 0], [1.0, 2.0, 9.0, 10.0]) < -0.9

    def test_point_biserial_constant_continuous_is_none(self):
        assert point_biserial([1, 0, 1], [5.0, 5.0, 5.0]) is None

    def test_point_biserial_mismatched_lengths_is_none(self):
        assert point_biserial([1, 0], [1.0]) is None

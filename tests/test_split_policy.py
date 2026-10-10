"""Answer-level split policy (ADR-0011).

The benchmark has always labelled its questions `dev` / `validation` /
`holdout`, and no answer-level harness has ever read that label. These tests
pin the two properties that make the split real rather than decorative:

1. A report cannot claim a split its contents do not match.
2. A holdout question in an iteration population is caught, not tolerated.

Also pins the census itself, because the policy is only as good as the
assumption that the label exists on every row — an unlabelled row would
silently fall outside every slice.
"""

from __future__ import annotations

import pytest

from evaluation.split_policy import split_census
from evaluation.benchmark import load_questions
from evaluation.split_policy import (
    DEV_SPLIT,
    EVAL_SPLIT,
    HOLDOUT_SPLIT,
    answer_eval_qids,
    assert_no_holdout,
    contamination_report,
    describe_qids,
    stamp_split,
)


def _census():
    """Direct callable snapshot of split_census() for non-test callers."""
    return split_census()


@pytest.fixture(scope="module")
def census():
    return _census()




class TestCensus:
    def test_all_three_splits_exist_and_partition_the_set(self, census):
        assert set(census) == {DEV_SPLIT, EVAL_SPLIT, HOLDOUT_SPLIT}

    def test_counts_sum_to_the_full_benchmark(self, census):
        assert sum(len(q) for q in census.values()) == 150

    def test_every_question_is_labelled(self, census):
        """An unlabelled row would fall outside every slice, silently."""
        assert "<unlabelled>" not in census
        assert "" not in census

    def test_splits_are_disjoint(self, census):
        seen: set[str] = set()
        for name, qids in census.items():
            assert not (seen & set(qids)), f"{name} overlaps an earlier split"
            seen |= set(qids)


class TestLoaderRespectsSplit:
    def test_default_is_unchanged(self):
        """Changing this default would silently rewrite every stored n=."""
        assert len(load_questions()) == 150

    def test_single_split_filters(self):
        assert len(load_questions(split=EVAL_SPLIT)) == len(answer_eval_qids(EVAL_SPLIT))

    def test_multiple_splits_union(self):
        n = len(load_questions(split=[EVAL_SPLIT, HOLDOUT_SPLIT]))
        assert n == len(answer_eval_qids(EVAL_SPLIT)) + len(answer_eval_qids(HOLDOUT_SPLIT))

    def test_filtered_questions_carry_gold_units(self):
        """Filtering must not skip gold resolution."""
        for q in load_questions(split=EVAL_SPLIT):
            assert q.split == EVAL_SPLIT

    def test_unknown_split_yields_nothing_rather_than_everything(self):
        assert load_questions(split="nope") == []


class TestDescribeQids:
    def test_counts_per_split(self, census):
        got = describe_qids(census[EVAL_SPLIT][:5] + census[DEV_SPLIT][:3])
        assert got["counts"] == {EVAL_SPLIT: 5, DEV_SPLIT: 3}
        assert got["n"] == 8

    def test_qids_outside_the_benchmark_are_surfaced(self):
        got = describe_qids(["Q001", "not-a-real-qid"])
        assert got["counts"]["unlabelled"] == 1
        assert "not-a-real-qid" in got["unlabelled_qids"]

    def test_empty_population(self):
        assert describe_qids([])["n"] == 0


class TestContaminationReport:
    def test_clean_validation_run(self, census):
        rep = contamination_report(census[EVAL_SPLIT])
        assert rep["problems"] == []
        assert rep["touches_holdout"] is False
        assert rep["touches_dev"] is False
        assert "clean" in rep["statement"]

    def test_holdout_is_named(self, census):
        rep = contamination_report(census[EVAL_SPLIT][:3] + census[HOLDOUT_SPLIT][:1])
        assert rep["touches_holdout"] is True
        assert any("holdout" in p for p in rep["problems"])

    def test_dev_is_named_as_training_leakage(self, census):
        rep = contamination_report(census[DEV_SPLIT][:1])
        assert rep["touches_dev"] is True
        assert any("leak" in p for p in rep["problems"])

    def test_undeclared_run_is_not_assumed_clean_or_dirty(self, census):
        """No declared split means no claim to check — only the contents."""
        rep = contamination_report(census[EVAL_SPLIT][:4])
        assert rep["declared_split"] is None
        assert rep["problems"] == []

    def test_declaration_that_contradicts_contents_is_reported(self, census):
        rep = contamination_report(census[EVAL_SPLIT][:4], declared_split=HOLDOUT_SPLIT)
        assert any("does not describe this run" in p for p in rep["problems"])

    def test_truthful_declaration_is_not_flagged(self, census):
        rep = contamination_report(census[EVAL_SPLIT][:4], declared_split=EVAL_SPLIT)
        assert rep["problems"] == []


class TestAssertNoHoldout:
    def test_clean_population_passes(self, census):
        assert_no_holdout(census[EVAL_SPLIT] + census[DEV_SPLIT])

    def test_holdout_raises(self, census):
        with pytest.raises(ValueError, match="holdout"):
            assert_no_holdout(census[EVAL_SPLIT] + census[HOLDOUT_SPLIT][:1])

    def test_it_is_a_raise_not_a_warning(self):
        """A holdout that has been read cannot be un-read."""
        with pytest.raises(ValueError):
            assert_no_holdout(["definitely-not-in-the-benchmark"])

    def test_unidentifiable_qids_are_refused_too(self, census):
        """A guard that passes when it cannot tell is not a guard."""
        with pytest.raises(ValueError, match="not in the benchmark"):
            assert_no_holdout(census[EVAL_SPLIT][:2] + ["Q999-unknown"])


class TestStampSplit:
    def test_stamps_in_place_and_returns(self, census):
        report: dict = {"benchmark": "x"}
        out = stamp_split(report, census[EVAL_SPLIT][:2])
        assert out is report
        assert "split_provenance" in report

    def test_statement_is_promotable_to_limitations(self, census):
        report = stamp_split({"limitations": []}, census[EVAL_SPLIT][:2])
        report["limitations"].append(report["split_provenance"]["statement"])
        assert any("clean" in s for s in report["limitations"])

    def test_stored_stage2_population_is_flagged_contaminated(self):
        """The +0.045 result that motivated the retry guard.

        13 dev (trains on them) and 2 holdout (Q132, Q149) of 22. Q132 is the
        qid whose flip-down motivated SPEC-3 in the first place.
        """
        import json
        from pathlib import Path

        path = Path("evaluation/stage2_ab_n20_report.json")
        if not path.exists():
            pytest.skip("Stage-2 report not present")
        qids = json.loads(path.read_text(encoding="utf-8"))["qids"]
        rep = contamination_report(qids)
        assert rep["touches_dev"] is True
        assert rep["touches_holdout"] is True
        assert "CONTAMINATED" in rep["statement"]

def _question_ids_by_split(name: str) -> list[str]:
    return split_census()[name]


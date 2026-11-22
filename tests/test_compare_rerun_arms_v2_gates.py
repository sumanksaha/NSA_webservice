"""Tests for the v2 gate set (evaluation/compare_rerun_arms.py).

Gate set v2 exists because ``soft`` cannot tell a content win from a length
artifact: ``soft`` is ``(jaccard + coverage) / 2`` and jaccard's denominator
grows with the answer, so an arm that merely writes more shows a soft gain
that is pure length.  The measured case: the W3 arm inflated median answer
length by 113-262% while moving binary by a net of 1-2, and cleared the v1
soft gate anyway.

So the gates under test here are:

* ``coverage`` delta -- the length-invariant content term;
* ``length`` guard -- median answer length may not inflate past
  ``LENGTH_MAX_INFLATION_PCT``;
* ``binary`` net -- must clear the same-config noise floor;
* ``abstention`` -- the *recorded* flag, not the rubric's lexicon match;
* the control cohort -- a regression guard, checked for harm and never
  promotion-eligible.

0 LLM calls.
"""

from __future__ import annotations

from typing import ClassVar

from evaluation.compare_rerun_arms import (
    COVERAGE_TIE,
    LENGTH_MAX_INFLATION_PCT,
    NOISE_FLOOR_FLIPS,
    SOFT_TIE,
    _gates,
    _metric_row,
)


class _FakeQuestion:
    def __init__(self, qid: str, *, insufficient_evidence: bool = False) -> None:
        self.question_id = qid
        self.question = "What does Section 55 provide?"
        self.gold_answer = "Imprisonment up to six months and a fine."
        self.gold_concepts = ["imprisonment up to six months"]
        self.gold_authorities = ["FSS Act s55"]
        self.acceptable_conclusion = "six months"
        self.insufficient_evidence = insufficient_evidence
        self.question_types = ["penalty"]


QUESTIONS = {
    "Q_A": _FakeQuestion("Q_A"),
    "Q_B": _FakeQuestion("Q_B"),
    "Q_C": _FakeQuestion("Q_C"),
    "Q_D": _FakeQuestion("Q_D"),
    "Q_E": _FakeQuestion("Q_E"),
    "Q_F": _FakeQuestion("Q_F"),
}
ACTIONS = {
    "Q_A": "provision_check",
    "Q_B": "abstention_gate",
    "Q_C": "control",
    "Q_D": "control",
    "Q_E": "control",
    "Q_F": "control",
}
ALL_QIDS = ("Q_A", "Q_B", "Q_C", "Q_D", "Q_E", "Q_F")

_ANSWER = "Under Section 55 of the FSS Act the penalty is imprisonment up to six months and a fine of five lakh rupees."


def _rec(qid: str, *, binary: int, soft: float, coverage: float, length: int, abstained: bool = False) -> dict:
    return {
        "qid": qid,
        "binary_correct": binary,
        "answer_correctness": soft,
        "answer_coverage": coverage,
        "answer_length": length,
        "abstained": abstained,
        "answer": _ANSWER,
    }


def _row(base: list[dict], arm: list[dict]) -> dict:
    return _metric_row(base, arm, QUESTIONS, ACTIONS)


class TestCoverageAndLengthAreReported:
    def test_coverage_jaccard_and_length_are_derived_per_arm(self):
        base = [_rec("Q_A", binary=0, soft=0.35, coverage=0.50, length=1000)]
        arm = [_rec("Q_A", binary=1, soft=0.50, coverage=0.70, length=1200)]
        row = _row(base, arm)
        assert row["coverage"]["base"] == 0.50
        assert row["coverage"]["arm"] == 0.70
        assert row["coverage"]["mean_paired_delta"] == 0.20
        # jaccard is recoverable from the frozen scorer, soft = (j + c) / 2
        assert row["jaccard"]["base"] == round(2 * 0.35 - 0.50, 4)
        assert row["jaccard"]["arm"] == round(2 * 0.50 - 0.70, 4)
        assert row["length"]["base_median"] == 1000
        assert row["length"]["arm_median"] == 1200
        assert row["length"]["delta_pct"] == 20.0

    def test_missing_coverage_field_defaults_to_zero_not_a_crash(self):
        base = [{"qid": "Q_A", "binary_correct": 0, "answer_correctness": 0.2, "answer": _ANSWER}]
        arm = [{"qid": "Q_A", "binary_correct": 1, "answer_correctness": 0.6, "answer": _ANSWER}]
        row = _row(base, arm)
        assert row["coverage"]["base"] == 0.0
        assert row["length"]["base_median"] == 0
        assert row["length"]["delta_pct"] is None


class TestGateSetV2:
    def _clean_row(self) -> dict:
        """A row that clears every gate: content up, length flat, binary up.

        Six qids so the binary net (6) clears the 4-flip noise floor -- a
        three-qid arm cannot clear it, which is the point of the floor.
        """
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=1, soft=0.42, coverage=0.62, length=1000) for q in ALL_QIDS]
        return _row(base, arm)

    def test_a_clean_arm_is_promotion_eligible(self):
        g = _gates(self._clean_row())
        assert g["soft_pass"] is True
        assert g["coverage_pass"] is True
        assert g["binary_pass"] is True
        assert g["length_pass"] is True
        assert g["abstention_pass"] is True
        assert g["promotion_eligible"] is True

    def test_a_soft_tie_fails_the_soft_gate(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=1, soft=0.31, coverage=0.62, length=1000) for q in ALL_QIDS]
        g = _gates(_row(base, arm))
        assert g["soft_pass"] is False
        assert g["promotion_eligible"] is False

    def test_coverage_below_the_tie_fails_even_with_a_soft_gain(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=1, soft=0.50, coverage=0.51, length=1000) for q in ALL_QIDS]
        g = _gates(_row(base, arm))
        assert g["soft_pass"] is True
        assert g["binary_pass"] is True
        assert g["length_pass"] is True
        assert g["coverage_pass"] is False
        assert g["promotion_eligible"] is False

    def test_length_inflation_fails_the_guard(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=1, soft=0.42, coverage=0.62, length=2000) for q in ALL_QIDS]
        g = _gates(_row(base, arm))
        assert g["soft_pass"] is True
        assert g["coverage_pass"] is True
        assert g["binary_pass"] is True
        assert g["length_pass"] is False
        assert g["promotion_eligible"] is False

    def test_length_shrinkage_passes_the_guard(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=2000) for q in ALL_QIDS]
        arm = [_rec(q, binary=1, soft=0.42, coverage=0.62, length=1000) for q in ALL_QIDS]
        g = _gates(_row(base, arm))
        assert g["length_pass"] is True

    def test_binary_inside_the_noise_floor_fails(self):
        # 1 gain, 1 loss -> net 0, inside the floor
        base = [
            _rec("Q_A", binary=0, soft=0.30, coverage=0.50, length=1000),
            _rec("Q_B", binary=1, soft=0.30, coverage=0.50, length=1000),
            _rec("Q_C", binary=0, soft=0.30, coverage=0.50, length=1000),
        ]
        arm = [
            _rec("Q_A", binary=1, soft=0.42, coverage=0.62, length=1000),
            _rec("Q_B", binary=0, soft=0.42, coverage=0.62, length=1000),
            _rec("Q_C", binary=0, soft=0.42, coverage=0.62, length=1000),
        ]
        g = _gates(_row(base, arm))
        assert g["binary_net"] == 0
        assert g["binary_pass"] is False
        assert g["promotion_eligible"] is False

    def test_a_rise_in_recorded_abstentions_fails(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=1, soft=0.42, coverage=0.62, length=1000, abstained=True) for q in ALL_QIDS]
        g = _gates(_row(base, arm))
        assert g["abstention_pass"] is False
        assert g["promotion_eligible"] is False


class TestControlCohortIsAGuardNotATarget:
    def test_a_soft_drop_inside_the_tie_is_allowed(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=0, soft=0.29, coverage=0.50, length=1000) for q in ALL_QIDS]
        g = _gates(_row(base, arm), regression_guard=True)
        assert g["soft_pass"] is True

    def test_a_soft_drop_beyond_the_tie_fails(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=0, soft=0.20, coverage=0.50, length=1000) for q in ALL_QIDS]
        g = _gates(_row(base, arm), regression_guard=True)
        assert g["soft_pass"] is False

    def test_a_control_is_never_promotion_eligible_even_if_it_gains(self):
        base = [_rec(q, binary=0, soft=0.30, coverage=0.50, length=1000) for q in ALL_QIDS]
        arm = [_rec(q, binary=1, soft=0.42, coverage=0.62, length=1000) for q in ALL_QIDS]
        g = _gates(_row(base, arm), regression_guard=True)
        assert g["promotion_eligible"] is False
        assert g["regression_guard"] is True


class TestGateConstants:
    def test_thresholds_match_the_preregistered_values(self):
        assert SOFT_TIE == 0.02
        assert COVERAGE_TIE == 0.02
        assert LENGTH_MAX_INFLATION_PCT == 10.0
        assert NOISE_FLOOR_FLIPS == 4


class TestNoiseBandGate:
    """The preregistered +0.02 soft tie sits inside the measured same-config
    spread, so a delta that clears the tie can still be a rerun.  The band
    carries the standard error of the MEAN delta (the yardstick for a mean),
    which is rescaled to the candidate's own n.
    """

    #: measured on the production path at n=25: soft SE 0.0092, coverage
    #: SE 0.0206, per-answer instability mean|d| 0.0378 / 0.0850
    BAND: ClassVar[dict] = {
        "soft": 0.0378,
        "coverage": 0.0850,
        "soft_se": 0.0092,
        "coverage_se": 0.0206,
        "binary_net": 0,
        "length_pct": 178.2,
        "n": 25,
    }

    def _row(self, *, soft: float, coverage: float, binary: int, n: int = 6) -> dict:
        questions = {f"Q{i}": _FakeQuestion(f"Q{i}") for i in range(n)}
        actions = {q: "control" for q in questions}
        base = [_rec(f"Q{i}", binary=0, soft=0.30, coverage=0.50, length=1000) for i in range(n)]
        arm = [_rec(f"Q{i}", binary=binary, soft=0.30 + soft, coverage=0.50 + coverage, length=1000) for i in range(n)]
        return _metric_row(base, arm, questions, actions)

    def test_a_delta_inside_the_band_fails_at_the_same_n(self):
        band = self.BAND | {"n": 6}
        g = _gates(self._row(soft=0.02, coverage=0.02, binary=1), noise_band=band)
        assert g["soft_pass"] is True, "it clears the preregistered tie"
        assert g["coverage_pass"] is True
        assert g["noise_pass"] is False, "but it is inside the measured band"
        assert g["promotion_eligible"] is False

    def test_the_threshold_is_the_reruns_standard_error_at_the_candidates_n(self):
        # The band n cancels: the threshold is sd / sqrt(n_candidate), so it
        # FALLS as the candidate population grows.  A band measured at n=25
        # must not reject a genuine +0.02 effect measured at n=150.
        band = self.BAND | {"n": 25}
        g25 = _gates(self._row(soft=0.02, coverage=0.02, binary=1, n=25), noise_band=band)
        g150 = _gates(self._row(soft=0.02, coverage=0.02, binary=1, n=150), noise_band=band)
        assert g25["noise_band"]["soft_threshold_at_candidate_n"] == 0.0092
        assert g150["noise_band"]["soft_threshold_at_candidate_n"] < 0.0092
        assert g150["noise_pass"] is True, "at n=150 the same delta clears the rescaled bar"

    def test_a_delta_outside_the_band_passes(self):
        band = self.BAND | {"n": 6}
        g = _gates(self._row(soft=0.09, coverage=0.12, binary=6), noise_band=band)
        assert g["noise_pass"] is True
        assert g["promotion_eligible"] is True

    def test_without_a_band_the_noise_check_is_not_applied(self):
        g = _gates(self._row(soft=0.02, coverage=0.02, binary=6))
        assert g["noise_pass"] is None
        assert g["promotion_eligible"] is True


def _row_of(base: list[dict], arm: list[dict]) -> dict:
    return _metric_row(base, arm, QUESTIONS, ACTIONS)

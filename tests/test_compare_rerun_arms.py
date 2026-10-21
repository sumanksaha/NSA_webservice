"""Tests for the paired A/B comparer (evaluation/compare_rerun_arms.py).

The gate decision reads this table, so the things it can get wrong matter:
discordant pairs (a net of 0 with +5/-5 is not a win), the noise floor flag,
soft ties, and the citation pairing rule (a qid instrumented in only one arm
must drop out rather than score as 0 for that arm).
"""

from __future__ import annotations

from evaluation.compare_rerun_arms import NOISE_FLOOR_FLIPS, SOFT_TIE, _metric_row


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
    "Q_B": _FakeQuestion("Q_B", insufficient_evidence=True),
    "Q_C": _FakeQuestion("Q_C"),
}
ACTIONS = {"Q_A": "provision_check", "Q_B": "abstention_gate", "Q_C": "control"}

_ABSTAIN = "The provided context does not contain the sought provision; it cannot be determined."
_ASSERT = (
    "Under Section 55 of the FSS Act, the penalty is imprisonment up to six months and a fine of five lakh rupees."
)


def _rec(qid: str, *, binary: int, soft: float, answer: str, citation=None, error=None) -> dict:
    rec = {
        "qid": qid,
        "binary_correct": binary,
        "answer_correctness": soft,
        "answer": answer,
        "error": error,
    }
    if citation is not None:
        rec["citation"] = citation
    return rec


def _pair():
    base = [
        _rec("Q_A", binary=0, soft=0.20, answer=_ASSERT, citation={"recall": 0.5, "gold_cited": 1, "has_citation": 1}),
        _rec("Q_B", binary=1, soft=0.70, answer=_ABSTAIN),
        _rec("Q_C", binary=0, soft=0.40, answer=_ASSERT, citation={"recall": 1.0, "gold_cited": 1, "has_citation": 1}),
    ]
    arm = [
        _rec("Q_A", binary=1, soft=0.70, answer=_ASSERT, citation={"recall": 1.0, "gold_cited": 2, "has_citation": 1}),
        _rec("Q_B", binary=0, soft=0.20, answer=_ASSERT),
        _rec("Q_C", binary=0, soft=0.40, answer=_ASSERT),  # no citation record -> pair drops out
    ]
    return base, arm


class TestBinaryDiscordantPairs:
    def test_gained_and_lost_are_counted_separately(self):
        row = _metric_row(*_pair(), QUESTIONS, ACTIONS)
        assert row["n"] == 3
        assert row["binary"]["base"] == 1
        assert row["binary"]["arm"] == 1  # 0->1 and 1->0 cancel in the totals...
        assert row["binary"]["gained"] == 1  # ...but both flips are visible
        assert row["binary"]["lost"] == 1
        assert row["binary"]["net"] == 0
        assert row["binary"]["inside_noise_floor"] is True

    def test_a_clear_win_is_flagged_outside_the_noise_floor(self):
        base = [
            _rec("Q_A", binary=0, soft=0.2, answer=_ASSERT),
            _rec("Q_B", binary=0, soft=0.2, answer=_ASSERT),
            _rec("Q_C", binary=0, soft=0.2, answer=_ASSERT),
        ]
        arm = [_rec(q, binary=1, soft=0.6, answer=_ASSERT) for q in ("Q_A", "Q_B", "Q_C")]
        row = _metric_row(base, arm, QUESTIONS, ACTIONS)
        assert row["binary"]["net"] == 3
        # 3 net flips is at/below the 4-flip noise floor measured between two
        # runs of the SAME configuration, so it still may not be called a win.
        assert row["binary"]["inside_noise_floor"] == (NOISE_FLOOR_FLIPS >= 3)


class TestSoftDelta:
    def test_ties_do_not_count_as_movement(self):
        row = _metric_row(*_pair(), QUESTIONS, ACTIONS)
        assert row["soft"]["improved"] == 1  # +0.50
        assert row["soft"]["worsened"] == 1  # -0.50
        # Q_C identical: neither, and a delta under SOFT_TIE never counts.
        assert row["soft"]["base"] == 0.4333
        assert row["soft"]["arm"] == 0.4333
        assert SOFT_TIE > 0


class TestCitationPairing:
    def test_qid_instrumented_in_only_one_arm_is_dropped_not_zeroed(self):
        row = _metric_row(*_pair(), QUESTIONS, ACTIONS)
        assert row["citation"]["n"] == 1  # Q_C has a record on the base side only
        assert row["citation"]["base"] == 0.5
        assert row["citation"]["arm"] == 1.0
        assert row["citation"]["gained_any_gold"] == 1

    def test_no_instrumentation_reports_none_not_zero(self):
        base, arm = _pair()
        for rec in base + arm:
            rec.pop("citation", None)
        assert _metric_row(base, arm, QUESTIONS, ACTIONS)["citation"] is None


class TestAbstentionHarmGuard:
    def test_lost_abstention_is_counted_on_the_arm(self):
        row = _metric_row(*_pair(), QUESTIONS, ACTIONS)
        a = row["abstention"]
        # Q_B is an insufficient_evidence target: the base abstains, the arm
        # asserts — a missed abstention, i.e. the exact regression the flag
        # must not ship.
        assert a["missed_abstention_base"] == 0
        assert a["missed_abstention_arm"] == 1
        assert a["false_abstention_base"] == 0
        assert a["false_abstention_arm"] == 0


class TestArmErrors:
    def test_llm_errors_are_counted(self):
        base, arm = _pair()
        arm[0]["error"] = "LLM request failed after 3 attempts"
        row = _metric_row(base, arm, QUESTIONS, ACTIONS)
        assert row["errors_arm"] == 1

    def test_empty_pairing_is_safe(self):
        assert _metric_row([], [], QUESTIONS, ACTIONS) == {"n": 0}

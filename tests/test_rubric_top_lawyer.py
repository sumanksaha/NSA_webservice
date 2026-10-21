"""Tests for the top-lawyer rubric (evaluation/rubric_top_lawyer.py).

Each dimension is pinned on a fixture that isolates its failure mode, using
the same scorer functions the cached-run harness applies — no LLM, no cached
artifacts needed.
"""

from __future__ import annotations

from evaluation.rubric_top_lawyer import _COMPLETENESS_CLAIM_RE, _STRUCTURE_LINE_RE, score_dimensions


def _question(**overrides):
    """A benchmark-like Question (duck-typed: only the scorer's fields)."""
    base = {
        "question_id": "Q001",
        "question": "What is the penalty for a licence violation under Section 55?",
        "gold_answer": "Imprisonment up to six months and a fine up to five lakh rupees, "
        "with a further daily fine of five thousand rupees for continuing default, "
        "escalating on repeat conviction.",
        "gold_concepts": ["imprisonment up to six months", "five lakh rupees", "daily fine"],
        "gold_authorities": ["FSS Act s55"],
        "acceptable_conclusion": "six months and five lakh",
        "insufficient_evidence": False,
        "question_types": ["penalty"],
    }
    base.update(overrides)
    return _FakeQuestion(**base)


class _FakeQuestion:  # minimal shape the scorers read
    def __init__(self, **kwargs):
        self.question_id = kwargs["question_id"]
        self.question = kwargs["question"]
        self.gold_answer = kwargs["gold_answer"]
        self.gold_concepts = kwargs["gold_concepts"]
        self.gold_authorities = kwargs["gold_authorities"]
        self.acceptable_conclusion = kwargs["acceptable_conclusion"]
        self.insufficient_evidence = kwargs["insufficient_evidence"]
        self.question_types = kwargs["question_types"]


class TestProvisionCheck:
    def test_named_authority_passes(self):
        d = score_dimensions(
            "Under Section 55 of the FSS Act, the licence must be renewed annually.",
            _question(),
            "provision_check",
        )
        assert d["provision_check"]["pass"] is True
        assert d["provision_check"]["authority"] is not None

    def test_unnamed_authority_fails(self):
        d = score_dimensions("The licence must be renewed every year.", _question(), "provision_check")
        assert d["provision_check"]["pass"] is False


class TestReasoningDepth:
    def test_full_penalty_structure_passes(self):
        answer = (
            "Under Section 55 of the FSS Act: imprisonment for a term which may extend to six "
            "months, with fine which may extend to Rs. 5,00,000 (five lakh rupees); a further "
            "continuing daily fine of Rs. 5,000 for each day of default; on repeat conviction, "
            "the penalty escalates to imprisonment up to two years."
        )
        d = score_dimensions(answer, _question(), "provision_check")
        assert d["reasoning_depth"]["pass"] is True
        assert d["reasoning_depth"]["penalty_structure"] == 1.0
        assert d["reasoning_depth"]["concepts_missing"] == 0

    def test_missing_penalty_tiers_fail(self):
        # Concepts present but only the base penalty — the audit's
        # "penalty incompleteness" mode: continuing daily fine and
        # escalation are absent, so the structure score is partial.
        answer = (
            "Under Section 55 of the FSS Act, a fine of five lakh rupees may be imposed "
            "with imprisonment which may extend to six months."
        )
        d = score_dimensions(answer, _question(), "provision_check")
        assert d["reasoning_depth"]["penalty_structure"] < 1.0

    def test_non_penalty_question_has_no_structure_component(self):
        d = score_dimensions(
            "Under Section 55 of the FSS Act, imprisonment up to six months, a fine of five "
            "lakh rupees and a daily fine for continuing default may be imposed.",
            _question(question_types=["general"]),
            "provision_check",
        )
        assert d["reasoning_depth"]["penalty_structure"] is None
        assert d["reasoning_depth"]["concepts_covered"] == 3


class TestAbstentionDiscipline:
    def test_qualified_refusal_passes_on_abstention_gate_target(self):
        answer = (
            "The provided context does not contain the sought provision; the answer cannot "
            "be determined because the operative text is not shown. What is unknown: whether "
            "the exemption in Section 55(2) applies to this case."
        )
        d = score_dimensions(answer, _question(), "abstention_gate")
        assert d["abstention_discipline"]["pass"] is True
        assert d["abstention_discipline"]["false_abstention"] is False

    def test_assertion_fails_on_abstention_gate_target(self):
        d = score_dimensions(
            "The penalty under Section 55 of the FSS Act is imprisonment up to six months.",
            _question(),
            "abstention_gate",
        )
        assert d["abstention_discipline"]["pass"] is False
        assert d["abstention_discipline"]["missed_abstention"] is True

    def test_false_abstention_on_answerable_question_fails(self):
        answer = "The context does not contain the answer to this question."
        d = score_dimensions(answer, _question(), "provision_check")
        assert d["abstention_discipline"]["pass"] is False
        assert d["abstention_discipline"]["false_abstention"] is True

    def test_answer_passes_on_answerable_question(self):
        d = score_dimensions(
            "Under Section 55 of the FSS Act, the penalty is imprisonment up to six months "
            "and a fine of five lakh rupees.",
            _question(),
            "provision_check",
        )
        assert d["abstention_discipline"]["pass"] is True


class TestPresentation:
    def test_memo_structure_scores(self):
        assert len({m.group(1).lower() for m in _STRUCTURE_LINE_RE.finditer("**Issue:** x\nRule: y")}) == 2

    def test_false_completeness_claim_penalised(self):
        answer = (
            "Issue: penalty. Rule: Section 55 of the FSS Act provides the penalty. "
            "Application: all parts of the question are fully covered by this answer. "
            "Conclusion: imprisonment up to six months."
        )
        d = score_dimensions(answer, _question(), "provision_check")
        assert d["presentation"]["false_completeness"] is True

    def test_completeness_claim_engine_requires_grounded_answer(self):
        assert _COMPLETENESS_CLAIM_RE.search("all parts of the question are covered")
        assert not _COMPLETENESS_CLAIM_RE.search("the rule is stated in full")

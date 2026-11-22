"""Tests for the soft-token gap analysis and the answer-shape counterfactual.

Both harnesses exist to make the prompt wording a measured choice rather than
a guess, so the things they can get wrong matter: the family partition must
not double-count a token, the section/figure detectors must not swallow
ordinary words, the Step split must survive a missing heading, and the
headroom must be the scorer's own arithmetic rather than an estimate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.answer_shape_counterfactual import (  # noqa: E402
    _aggregate,
    _variants,
    split_steps,
)
from evaluation.soft_token_gap import (  # noqa: E402
    FAMILY_ORDER,
    _families,
    _figure_tokens,
    _section_tokens,
    _tokens,
)


class _FakeQuestion:
    def __init__(
        self,
        qid: str,
        *,
        conclusion: str,
        concepts: list[str] | None = None,
        authorities: list[str] | None = None,
        insufficient_evidence: bool = False,
    ) -> None:
        self.question_id = qid
        self.question = "What does the Act provide?"
        self.acceptable_conclusion = conclusion
        self.gold_concepts = concepts or []
        self.gold_authorities = authorities or []
        self.insufficient_evidence = insufficient_evidence
        self.question_types = ["direct provision"]


QUESTIONS = {
    "Q_A": _FakeQuestion(
        "Q_A",
        conclusion=(
            "Sub-standard food (s60) and misbranded food (s61) attract a fine "
            "(s60 up to 5 lakh, s61 up to 3 lakh); misleading advertisement (s53) "
            "is a separate offence carrying a fine of 10 lakh rupees."
        ),
        concepts=["sub-standard food", "misbranded food", "misleading advertisement"],
        authorities=["Food Safety and Standards Authority of India"],
    ),
    "Q_B": _FakeQuestion(
        "Q_B",
        conclusion="The Designated Officer grants a State licence and the Central Licensing Authority grants a Central licence.",
        concepts=["licence issuance"],
        authorities=["Designated Officer"],
    ),
}
ACTIONS = {"Q_A": "provision_check", "Q_B": "control"}


class TestTokenDetection:
    def test_section_references_are_found(self):
        toks = _section_tokens("Sub-standard food (s60) attracts a fine under section 61")
        assert "s60" in toks
        assert "61" in toks  # bare number adjacent to "section"

    def test_a_bare_number_not_next_to_section_is_not_a_section_ref(self):
        toks = _section_tokens("a fine of 5 lakh and 3 lakh")
        assert "5" not in toks
        assert "3" not in toks

    def test_figure_tokens_cover_money_and_terms(self):
        toks = _figure_tokens("a fine of 5 lakh rupees and six months imprisonment")
        assert "5" in toks
        assert "lakh" in toks
        assert "rupees" in toks
        assert "months" in toks
        assert "fine" not in toks


class TestFamilyPartition:
    def test_missing_tokens_are_partitioned_without_overlap(self):
        q = QUESTIONS["Q_A"]
        answer = "The answer mentions sub-standard food and a fine."
        fams = _families(answer, q.acceptable_conclusion, q)
        all_tokens = set()
        for name in FAMILY_ORDER:
            assert not (all_tokens & fams[name]), f"{name} overlaps an earlier family"
            all_tokens |= fams[name]
        expected = _tokens(q.acceptable_conclusion) - _tokens(answer)
        assert all_tokens == expected

    def test_concepts_go_to_the_concept_family(self):
        q = QUESTIONS["Q_A"]
        # The answer covers "sub-standard food" and "misbranded food" but not
        # "misleading advertisement", so only the *uncovered* concept's tokens
        # are missing.
        answer = "The Act provides for sub-standard food and misbranded food."
        fams = _families(answer, q.acceptable_conclusion, q)
        assert "misleading" in fams["concept"]
        assert "advertisement" in fams["concept"]
        assert "misbranded" not in fams["concept"]
        assert "misbranded" not in fams["other"]

    def test_figures_go_to_the_figure_family(self):
        q = QUESTIONS["Q_A"]
        answer = (
            "Sub-standard food (s60) and misbranded food (s61) and misleading "
            "advertisement (s53) are separate offences under the Food Safety and "
            "Standards Authority of India."
        )
        fams = _families(answer, q.acceptable_conclusion, q)
        assert "lakh" in fams["figure"] or "5" in fams["figure"]
        assert "rupees" in fams["figure"]

    def test_a_fully_covered_answer_has_no_missing_tokens(self):
        q = QUESTIONS["Q_A"]
        fams = _families(q.acceptable_conclusion, q.acceptable_conclusion, q)
        assert sum(len(v) for v in fams.values()) == 0


class TestSplitSteps:
    """The split has to survive a missing ``Step 1`` heading.

    Measured on the first answer-shape smoke run: the model emitted the
    ``[n] <operative words>`` lines without the literal 'Step 1 - Quotes'
    heading, and only wrote 'Step 2 - Answer'.  A split that required both
    headings would have scored that answer as unsplittable and thrown the
    arm away.
    """

    def test_both_headings_split_into_quote_block_and_prose(self):
        answer = "Step 1 — Quotes\nquoted text [1].\nStep 2 — Answer\nprose text [1]."
        quote, prose = split_steps(answer)
        assert "quoted text" in quote
        assert "prose text" in prose
        assert "Step 1" not in quote

    def test_a_missing_step_one_heading_still_splits(self):
        answer = "[1] quoted text\n[2] more quoted text\n\nStep 2 — Answer\nprose text [1]."
        quote, prose = split_steps(answer)
        assert "[1] quoted text" in quote
        assert "prose text" in prose

    def test_an_answer_with_no_headings_does_not_split(self):
        assert split_steps("Just a flat legal answer with no headings.") is None

    def test_a_step_one_only_answer_does_not_split(self):
        # No Step 2 boundary, so the prose cannot be isolated.
        assert split_steps("Step 1 — Quotes\nonly quotes here [1].") is None

    def test_variants_accept_a_missing_step_one_heading(self):
        answer = "[1] six months\n\nStep 2 — Answer\nA fine may be imposed [1]."
        v = _variants(answer, "six months")
        assert v is not None
        assert v["n_carried"] == 2


class TestAggregate:
    def _row(self, *, soft_full=0.30, soft_prose=0.28, soft_target=0.40, cov=0.5) -> dict:
        def block(soft: float) -> dict[str, Any]:
            return {
                "answer_correctness": soft,
                "binary_correct": 0,
                "answer_coverage": cov,
                "answer_jaccard": soft - cov,
            }

        return {
            "scored": {
                "full": block(soft_full),
                "prose_only": block(soft_prose),
                "prose_plus_ref": block(soft_target),
            },
            "n_carried": 2,
            "markers_only_in_step1": 0,
            "markers_step1": 2,
            "markers_step2": 1,
            "tokens_full": 40,
            "tokens_prose": 20,
            "abstained": False,
            "error": False,
        }

    def test_it_reports_the_delta_against_the_full_answer(self):
        agg = _aggregate([self._row()])
        assert agg["n"] == 1
        assert agg["delta_prose_plus_ref"]["soft"] == 0.10
        assert agg["delta_prose_only"]["soft"] == -0.02
        # coverage is unchanged by the move -- the tokens relocate, they do not appear
        assert agg["prose_plus_ref"]["coverage"] == agg["full"]["coverage"]
        assert agg["mean_carried_reference_tokens"] == 2.0

    def test_empty_aggregate_is_safe(self):
        assert _aggregate([]) == {"n": 0}

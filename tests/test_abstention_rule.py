"""Tests for the abstention-credit rule (evaluator v2, 0 LLM calls).

Repairs two Step-0 findings (2026-09-26):
  * ``abstain_correct`` existed but never reached ``correct`` — answers that
    substantively match an abstention-shaped reference scored wrong;
  * the v1 lexicon misses common negations (``cannot be determined`` died on
    the trailing ``\\b`` after ``determine``; ``does not recognise`` absent).

Invariants under test:
  * credit fires ONLY on insufficient_evidence questions;
  * credit requires a negation-anchored match (a substantive answer that
    never negates gets nothing — Q098 pattern);
  * overlay-banned markers are stripped first (Q060/Q138);
  * v1-subset patterns still match (no regression vs the frozen lexicon);
  * short/empty answers keep the frozen scorer's abstain heuristic.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.abstention_rule import (
    abstain_credit,
    abstain_match,
    strip_banned,
)

IE_REF_STYLE_ANSWERS = [
    # Q042-style: recognition negation
    "No, the Prevention of Cruelty to Animals Rules, 2017 does not recognise a "
    "religious-ritual exception to the pre-stunning requirement as per Rule 63.",
    # v1 inflection bug: "cannot be determined" died on the trailing \\b
    "The exact annual licence fee for a State food licence cannot be determined "
    "from the provided evidence under the FSS Act and its fee rules.",
    # Q119-style: bounded-gap negation ("not a distance determinable")
    "The 500 m setback from a perennial river is not a distance determinable as "
    "a firm rule textually; the distance is fixed by notified coastal regulation.",
    # v1 subset: must keep matching (no regression)
    "The provided evidence contains no relevant information on the schedule of "
    "scrutiny fees payable under the section of the municipal act concerned.",
    # V4: existential absence ("there is no provision ...")
    "There is no provision in the Act that establishes a fee for this category.",
    "There are no rules that specify the threshold applicable here.",
    # V4: silence negation ("the act is silent on ...")
    "The Act is silent on the quantum of penalty for this conduct.",
    "The corpus remains silent as to whether a licence is required.",
    # V4: modal negation ("no provision can be established ...")
    "No section can be determined from the provided evidence alone.",
    "No threshold may be established from the materials on record.",
    # V4: possibility negation ("it is not possible to ...")
    "It is not possible to determine the amount from the context given.",
    "It is not feasible to establish the limit from the record.",
    # V4: source-scoped negation ("cannot be determined from the evidence ...")
    "The figure cannot be determined from the provided evidence here.",
    "The date cannot be established from the context supplied.",
    # V4: limitation negation ("does not itself establish ...")
    "The section does not itself prescribe a monetary cap on penalties.",
    "The provision does not per se set out the fee schedule.",
    # V4: purpose-bounded absence ("no provision for the purposes of ...")
    "There is no authority for the purposes of imposing this penalty.",
    "No clause exists for the purpose of fixing the threshold.",
    # V4: anywhere negation ("not specified anywhere")
    "The exact amount is not specified anywhere in the record.",
    "No penalty is stated anywhere in the corpus.",
    # V4: nowhere negation ("nowhere in the act ...")
    "Nowhere in the Act does it prescribe a specific fee figure.",
    # V4: failure negation ("the act fails to establish ...")
    "The Act fails to establish a fixed penalty amount for this offence.",
    "The corpus fails to set out the applicable registration fee.",
]

SUBSTANTIVE_ANSWERS = [
    # Q098-style: enumerates powers, never negates -> NO credit
    "Under Section 179(3) of the Companies Act, 2013, the key strategic powers "
    "reserved to the full Board of Directors are exercisable by means of "
    "resolutions passed at meetings of the Board as recorded in the minutes.",
    # ordinary substantive conclusion
    "The Food Safety Officer may serve an improvement notice under section 32 "
    "requiring the operator to remedy the contravence within thirty days.",
    # V4 negative: mentions sections/provisions but asserts positively, no negation anchor
    "Section 31 of the FSS Act requires a licence before operating a food business.",
    "The Act prescribes a penalty of up to one lakh rupees for this offence.",
    "The Food Safety Officer may serve an improvement notice under section 32.",
    "Rule 63 sets out the pre-stunning requirement for religious-ritual slaughter.",
    # V4 negative: negated word inside a substantive affirmative context should not match
    "The Act does specify the fee schedule under Section 51 and section 52.",
    "There is a clear provision establishing the threshold at fifty rupees.",
]


def test_credit_requires_insufficient_evidence_flag():
    ans = IE_REF_STYLE_ANSWERS[0]
    assert abstain_credit(ans, set(), insufficient_evidence=True) is True
    # same answer on a normal question: never credited
    assert abstain_credit(ans, set(), insufficient_evidence=False) is False


def test_all_ie_style_negations_get_credit():
    for ans in IE_REF_STYLE_ANSWERS:
        assert abstain_credit(ans, set(), insufficient_evidence=True) is True, ans


def test_substantive_answers_get_no_credit_even_on_ie_questions():
    for ans in SUBSTANTIVE_ANSWERS:
        assert abstain_credit(ans, set(), insufficient_evidence=True) is False, ans


def test_v1_inflection_bug_is_fixed():
    # v1's `cannot be (determine|...)` + trailing \b missed "determined"
    assert "cannot be determined" in "cannot be determined from the provided evidence"
    assert abstain_match("This answer says it cannot be determined anywhere here.") is True


def test_banned_markers_are_stripped_before_matching():
    ans = "The schedule of fees does not specify any amount for this category."
    assert abstain_match(ans, set()) is True
    assert abstain_match(ans, {"does not specify"}) is False
    # stripping is visible and case-insensitive
    assert "does not specify" not in strip_banned(ans.upper(), {"does not specify"})


def test_short_and_empty_keep_frozen_heuristic():
    assert abstain_match("", set()) is True
    assert abstain_match("unclear.", set()) is True  # < 20 chars


def test_no_credit_for_non_ie_even_with_short_or_empty():
    assert abstain_credit("", set(), insufficient_evidence=False) is False
    assert abstain_credit("unclear.", set(), insufficient_evidence=False) is False


def test_v4_phrasings_get_credit_on_ie_questions():
    v4 = [
        "There is no provision in the Act that establishes a fee for this category.",
        "The Act is silent on the quantum of penalty for this conduct.",
        "No section can be determined from the provided evidence alone.",
        "It is not possible to determine the amount from the context given.",
        "The figure cannot be determined from the provided evidence here.",
        "The section does not itself prescribe a monetary cap on penalties.",
        "There is no authority for the purposes of imposing this penalty.",
        "The exact amount is not specified anywhere in the record.",
        "Nowhere in the Act does it prescribe a specific fee figure.",
        "The Act fails to establish a fixed penalty amount for this offence.",
    ]
    for ans in v4:
        assert abstain_credit(ans, set(), insufficient_evidence=True) is True, ans


def test_v4_does_not_false_positive_on_substantive_answers():
    for ans in SUBSTANTIVE_ANSWERS:
        assert abstain_credit(ans, set(), insufficient_evidence=True) is False, ans
        assert abstain_match(ans, set()) is False, ans

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

from evaluation.abstention_rule import (  # noqa: E402
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
]

SUBSTANTIVE_ANSWERS = [
    # Q098-style: enumerates powers, never negates -> NO credit
    "Under Section 179(3) of the Companies Act, 2013, the key strategic powers "
    "reserved to the full Board of Directors are exercisable by means of "
    "resolutions passed at meetings of the Board as recorded in the minutes.",
    # ordinary substantive conclusion
    "The Food Safety Officer may serve an improvement notice under section 32 "
    "requiring the operator to remedy the contravence within thirty days.",
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

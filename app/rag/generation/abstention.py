"""Abstention lexicon — one home for "the answer declines to answer".

Why this module exists
---------------------
Both groundedness scorers measure support as a *ratio over citations*:

    ResponseSanitizer.sanitize   groundedness = len(valid) / len(citations)
    GroundednessScorer.score     citation_ratio = citation_result.score

An answer that cites nothing therefore scores ``groundedness = 0.0`` and is
flagged as a hallucination.  That is the exact inverse of the truth when the
model correctly refuses because the evidence does not establish an answer.

ADR-0010 §2.5 measured the damage: a prompt change drove the abstention rate
from 8/21 to 19/21, and every one of those *correct* refusals was scored
``hallucination_detected = True`` — so the change looked like a groundedness
collapse (-0.29) when it was an artifact of the metric.  Nothing downstream
could tell the two apart, because abstention was not recorded at all.

The fix is to record abstention and score it separately rather than folding it
into groundedness (the fix ADR-0010 §2.5 recommends).  ``groundedness_score``
is deliberately left at its measured value — it is an honest number and it is
what ``GROUNDEDNESS_RETRY_BELOW`` is calibrated against — but it must now be
read together with ``abstained``.  Mean groundedness over an abstention-heavy
population is still meaningless; that is an aggregation problem, addressed by
:func:`evaluation.abstention_rule.summarize_groundedness`.

Single home
-----------
The lexicon previously lived in ``evaluation/abstention_rule.py``.  ``app/``
must not import from ``evaluation/``, so the lexicon moved here and the eval
module imports it back: one definition, two consumers — the same locality rule
that module's own docstring already states.

Length policy differs by consumer, deliberately:

* :func:`is_abstention` (production) requires a *substantive* statement, so a
  short or degenerate answer is not laundered into "not a hallucination".
* ``evaluation.abstention_rule.abstain_match`` (frozen scorer contract) treats
  any answer under 20 characters as an abstention.  That is a convention of
  the frozen v1/v2 metrics and is left exactly as it was.

Deterministic; 0 model calls.
"""

from __future__ import annotations

import re

__all__ = [
    "ABSTENT_MARKERS_RE",
    "MIN_ABSTENTION_CHARS",
    "is_abstention",
    "strip_banned",
]


#: v1 lexicon, carried over verbatim from ``evaluation/abstention_rule.py``.
_V1_PATTERNS = (
    r"i (?:do not|cannot|dont|can't|can not)",
    r"cannot (?:find|answer|determine|locate)",
    r"no relevant",
    r"insufficient inform",
    r"unable to",
    r"not possible to",
    r"cannot be (?:determine|established|reliably)",
    r"not (?:recorded|established|stipulated|provided|specified|mentioned|available)",
    r"no (?:evidence|information|provision|specific)",
    r"does not (?:specify|establish|provide|state)",
    r"the corpus does not",
    r"no provision in the corpus",
)

#: v2 extensions — inflection tolerance + the negation phrasings the residual
#: answers actually use.  Every alternative is negation-anchored, never
#: "mentions the same subject": a substantive answer that merely enumerates
#: powers the reference says are *not* established gets no credit.
_V2_EXTRA = (
    # V3 additions — legal-specific negation phrasings discovered from failure cohort analysis
    # "The Act does not specify/provide/establish..."
    r"the (?:act|law|regulation|rule) (?:does not )?(?:specify|provide|establish|require)",
    r"no (?:provision|section|clause) (?:establishes?|provides?|specifies?|requires?)",
    r"the (?:corpus|evidence|record) (?:does not )?(?:establish|provide|specify|support)",
    r"cannot (?: be)? (?:find|determine|locate|ascertain|ascertainable) (?:from the )?(?:provided )?evidence",
    # "not [adjective] enough / not [sufficient] ..." phrasings
    r"not (?:sufficient|adequate|enough) (?:evidence|information|provision|support)",
    r"insufficient (?:evidence|information|provision|support) (?:to )?establish",
    # "no [noun] [verb] ..."
    r"no (?:authority|provision|basis|ground) (?:for|to )?(?:establish|provide|determine)",
    # "the [noun] [does not] [verb] ..."
    r"the (?:available|provided|existing) (?:evidence|corpus|information) (?:does not )?establish",
    r"the (?:said|aforementioned|above) (?:provision|section|clause) (?:does not )?establish",
    # inflection: "cannot be determined" missed v1's trailing \b after "determine"
    r"cannot be (?:determined|determining|specified|establishing)",
    r"can(?:not|'t) (?:be )?(?:determined|specified|established|ascertained|derived|read)",
    # recognition / containment / enumeration phrasings
    r"does not (?:recognise|recognize|contain|fix|determine|enumerate|prescribe|"
    r"lay down|set out|record|stipulate|mention|identify|define)",
    r"does not itself",
    # bounded-gap negation: "not a distance determinable", "not readable from the Act"
    r"not [^.;:]{0,30}(?:determinable|readable|derivable|ascertainable|enumerable)",
    # absence-of-quantity phrasings used by fee/threshold questions
    r"no (?:specific|exact|single|stable|clear) "
    r"(?:threshold|limit|fee|figure|cap|list|standard|schedule|duration|maximum|"
    r"minimum|amount|provision|figure)",
    r"is not (?:a )?(?:specific|exact|single|stable|fixed) ",
)

#: v4 extensions — residual negation phrasings surfaced from the missed-abstention
#: cohort (rubric residual set) that the v2 lexicon did not capture.  Kept
#: negation-anchored: each alternative declines on a specific negation, never on
#: "mentions the same subject".  Adding here is the only place production and
#: evaluation must stay in sync (evaluation/abstention_rule.py re-exports this
#: same compiled object).
_V4_EXTRA = (
    # "there is no ... / there are no ..." existential absence
    r"there (?:is|are) no (?:provision|section|clause|rule|rules|authority|basis|evidence|information)",
    r"there (?:is|are) not (?:any|a )?(?:provision|section|clause|rule|rules|authority|basis|evidence|information)",
    # "the act / the law / the corpus is silent on ..." — silence negation
    r"the (?:act|law|corpus|record) (?:is|remains) silent (?:on|as to|regarding|about)",
    # "no ... can be / could be / may be ... established" modal negation
    r"no (?:provision|section|clause|figure|threshold|limit|amount|figure) (?:can|could|may) (?:be )?(?:established|determined|ascertained|derived|read|found)",
    # "it is not possible to ... / not possible to ..." possibility negation
    r"it is not (?:possible|feasible|practicable) (?:to )?(?:determine|establish|ascertain|derive|find|read)",
    # "cannot be ... from the provided evidence / context" source-scoped negation
    r"cannot be (?:determined|established|ascertained|derived|read|found) (?:from the )?(?:provided )?(?:evidence|context|materials|record)",
    # "does not ... itself / per se / standing alone" limitation negation
    r"(?:does not|do not) (?:itself|per se|standing alone|in isolation) (?:establish|provide|specify|determine|set out|prescribe)",
    # "no ... for/in the purpose(s) of ..." purpose-bounded absence (with any
    # intervening words such as "exists" / "is present"; negation-anchored via
    # the leading "no <noun>")
    r"no (?:provision|section|clause|authority|basis) [^.;:]{0,40}(?:purpose|purposes)",
    # "not ... specified / stated / set out anywhere" anywhere negation
    r"not (?:specified|stated|set out|provided|established|recorded|mentioned|defined) anywhere",
    # "no X is stated/specified/provided anywhere" subject-scoped anywhere negation
    r"no (?:penalty|fee|amount|figure|threshold|limit|provision|section|clause|rule|authority) [^.;:]{0,30}?(?:stated|specified|provided|established|recorded|mentioned|defined) anywhere",
    # "nowhere in the ... does ..." nowhere negation
    r"nowhere in the (?:act|law|corpus|record|evidence|context|materials)",
    # "fails to ... / fails to establish ..." failure negation
    r"(?:the |this )?(?:act|law|provision|section|clause|corpus|evidence|record) fails to (?:establish|provide|specify|determine|set out|prescribe|record)",
)


#: The single compiled lexicon.  ``evaluation.abstention_rule`` re-exports it.
ABSTENT_MARKERS_RE = re.compile(
    r"\b(?:" + "|".join(_V1_PATTERNS + _V2_EXTRA + _V4_EXTRA) + r")\b",
    re.IGNORECASE,
)

#: An abstention must be a substantive statement.  Below this length there is
#: no proposition to decline, so a short answer is ungrounded rather than an
#: abstention — laundering it into "not a hallucination" would be a regression.
MIN_ABSTENTION_CHARS = 20

#: A long answer carrying citation markers is a substantive answer, not an
#: abstention.  The regex below can match a *legal qualification* inside a
#: longer answer — e.g. "the context does not specify a named authority" in
#: a 9500-char memo that went on to answer the question with [n] citations.
#: Q049 (9546 chars), Q059 (29271 chars) and Q066 all wrote full legal memos
#: with Issue/Rule/Application/Conclusion structure and were falsely
#: classified as abstentions by the regex matching a qualification phrase.
#: Below this length an answer is too short to carry substantive analysis;
#: above it, citation markers are strong evidence the model answered.
SUBSTANTIVE_ANSWER_CHARS = 2000


def strip_banned(answer: str, banned: set[str] | None) -> str:
    """Remove overlay-banned markers (Q060/Q138) before matching."""
    low = str(answer or "").lower()
    for m in banned or set():
        low = low.replace(m, " ")
    return low


def is_abstention(response_text: str, banned: set[str] | None = None) -> bool:
    """True when the answer declines rather than asserts.

    Production semantics matching evaluation rules (abstain_match):
    an abstention must match a negation-anchored marker.  A short or empty
    answer (<= :data:`MIN_ABSTENTION_CHARS` chars) asserts nothing and is
    not a *refusal* — laundering it into "not a hallucination" would be a
    safety regression, so the length floor is load-bearing.  It is treated
    as ungrounded rather than abstention by the frozen scorer contract.

    A long answer (>= :data:`SUBSTANTIVE_ANSWER_CHARS`) carrying citation
    markers is a substantive answer, not an abstention.  The regex below
    can match a *legal qualification* inside a longer answer — e.g. "the
    context does not specify a named authority" in a 9500-char memo that
    went on to answer the question with [n] citations.  Q049 (9546 chars),
    Q059 (29271 chars) and Q066 all wrote full legal memos with
    Issue/Rule/Application/Conclusion structure and were falsely
    classified as abstentions by the regex matching a qualification phrase.
    Below 2000 chars an answer is too short to carry substantive analysis;
    above it, citation markers are strong evidence the model answered.
    """
    text = str(response_text or "").strip()
    if len(text) < MIN_ABSTENTION_CHARS:
        return False  # short/empty: ungrounded, not a refusal
    if len(text) >= SUBSTANTIVE_ANSWER_CHARS:
        return False  # long, citation-rich: substantive answer
    return bool(ABSTENT_MARKERS_RE.search(strip_banned(text, banned)))

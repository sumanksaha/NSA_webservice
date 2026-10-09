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

#: The single compiled lexicon.  ``evaluation.abstention_rule`` re-exports it.
ABSTENT_MARKERS_RE = re.compile(
    r"\b(?:" + "|".join(_V1_PATTERNS + _V2_EXTRA) + r")\b",
    re.IGNORECASE,
)

#: An abstention must be a substantive statement.  Below this length there is
#: no proposition to decline, so a short answer is ungrounded rather than an
#: abstention — laundering it into "not a hallucination" would be a regression.
MIN_ABSTENTION_CHARS = 20


def strip_banned(answer: str, banned: set[str] | None) -> str:
    """Remove overlay-banned markers (Q060/Q138) before matching."""
    low = str(answer or "").lower()
    for m in banned or set():
        low = low.replace(m, " ")
    return low


def is_abstention(response_text: str, banned: set[str] | None = None) -> bool:
    """True when the answer declines rather than asserts.

    Production semantics, deliberately narrower than the frozen eval scorer:
    an abstention must be substantive (>= :data:`MIN_ABSTENTION_CHARS` chars)
    *and* match a negation-anchored marker.  An empty or 8-character answer is
    not an abstention — it is a degenerate answer, and it stays flagged.
    """
    text = str(response_text or "").strip()
    if len(text) < MIN_ABSTENTION_CHARS:
        return False
    return bool(ABSTENT_MARKERS_RE.search(strip_banned(text, banned)))

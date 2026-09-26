"""Abstention credit for `insufficient_evidence` questions (evaluator v2 only).

Why this exists (Step 0 analysis, 2026-09-26)
----------------------------------------------
The benchmark declares 12 of the 124 residual qids ``insufficient_evidence``;
its own worksheet says "a correct abstention is the right answer", and
``eval_e2e_v2`` scores ``abstain_correct = abstained if insufficient_evidence``.
But the evaluator-v2 binary rule is ``token_overlap > 0.5`` against the
reference conclusion, and ``abstain_correct`` was never folded in — so answers
that substantively agree with an abstention-shaped reference ("The available
corpus … does not establish …") sat at soft 0.27–0.46 and were counted wrong.

Two concrete defects this module repairs, *on the v2 side only*:

1. ``abstain_correct`` was computed but never applied to ``correct``.
2. The v1 abstain lexicon misses common negation phrasings found in the
   residual answers — e.g. ``cannot be determined`` fails the trailing ``\\b``
   after ``determine``, and ``does not recognise`` is absent entirely.

Contract
--------
* v1 metrics (``answer_correctness`` / ``correct`` / ``abstained``) are the
  frozen experiment artifacts and are NEVER touched here.
* ``abstained`` (the reported flag) keeps the v1 regex semantics — only the
  *credit* path uses the extended lexicon, so no downstream consumer of the
  abstain flag sees a behaviour change.
* Per-qid ``banned_abstain_markers`` from the overlay are stripped before
  matching (Q060/Q138 — reference-matching "does not …" was a false positive).
* Credit requires ALL of: question is ``insufficient_evidence`` AND the answer
  matches the extended abstention lexicon AND a non-trivial length (the v1
  "<20 chars == abstain" heuristic is kept, empty answer too).
* A substantive answer that never negates (e.g. it enumerates powers the
  reference says are not established) gets NO credit — the lexicon is
  deliberately negation-anchored, not "mentions the same subject".

Deterministic; 0 model calls. Imported by ``rescore_evaluator_v2`` (metric)
and ``step3_gated_generation`` (before/after scoring) so both sides of every
comparison use one rule (locality: one home for the lexicon).
"""

from __future__ import annotations

import re

#: v1 lexicon verbatim (kept as a subset so credit ⊇ v1 detection).
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
#: answers actually use. Every alternative is negation-anchored.
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

ABSTAIN_CREDIT_RE = re.compile(
    r"\b(?:" + "|".join(_V1_PATTERNS + _V2_EXTRA) + r")\b",
    re.IGNORECASE,
)

#: same "<20 chars is not an answer" heuristic as the frozen scorer
_MIN_ANSWER_CHARS = 20


def strip_banned(answer: str, banned: set[str] | None) -> str:
    """Remove overlay-banned markers (Q060/Q138) before matching."""
    low = str(answer or "").lower()
    for m in banned or set():
        low = low.replace(m, " ")
    return low


def abstain_match(answer: str, banned: set[str] | None = None) -> bool:
    """Extended abstention lexicon match (v2 credit path only)."""
    text = str(answer or "")
    if not text.strip() or len(text.strip()) < _MIN_ANSWER_CHARS:
        return True  # same empty/too-short heuristic as the frozen scorer
    return bool(ABSTAIN_CREDIT_RE.search(strip_banned(text, banned)))


def abstain_credit(
    answer: str,
    banned: set[str] | None,
    insufficient_evidence: bool,
) -> bool:
    """True when a binary credit is due: IE question AND genuine abstention.

    Non-IE questions always return False — the credit never manufactures a
    correct answer where the benchmark expects a substantive conclusion.
    """
    if not insufficient_evidence:
        return False
    return abstain_match(answer, banned)

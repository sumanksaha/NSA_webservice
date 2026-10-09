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

Lexicon location
----------------
The lexicon now lives in ``app.rag.generation.abstention`` and is imported
here, because ``app/`` must not import from ``evaluation/`` and both sides
must match the same negation patterns.  One definition, two consumers — the
locality rule this module already states.  ``ABSTAIN_CREDIT_RE`` is kept as
this module's public name and is the same object as
``app.rag.generation.abstention.ABSTENT_MARKERS_RE``.

The *length* policy intentionally differs between the two consumers and has
not been changed on either side: this frozen scorer treats any answer under
``_MIN_ANSWER_CHARS`` as an abstention, while
``app.rag.generation.abstention.is_abstention`` requires a substantive
statement so that a short degenerate answer is not laundered into
"not a hallucination".

Deterministic; 0 model calls. Imported by ``rescore_evaluator_v2`` (metric)
and ``step3_gated_generation`` (before/after scoring) so both sides of every
comparison use one rule (locality: one home for the lexicon).
"""

from __future__ import annotations

from app.rag.generation.abstention import ABSTENT_MARKERS_RE, strip_banned

__all__ = [
    "ABSTAIN_CREDIT_RE",
    "MIN_ANSWER_CHARS",
    "abstain_credit",
    "abstain_match",
    "strip_banned",
    "summarize_groundedness",
]

#: Re-exported from the shared home (see "Lexicon location" above).
ABSTAIN_CREDIT_RE = ABSTENT_MARKERS_RE

#: same "<20 chars is not an answer" heuristic as the frozen scorer
_MIN_ANSWER_CHARS = 20
MIN_ANSWER_CHARS = _MIN_ANSWER_CHARS


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


def summarize_groundedness(rows: dict[str, dict]) -> dict:
    """Split a groundedness aggregate by abstention.

    ADR-0010 §2.5: ``groundedness_score`` is a ratio over citations, so a
    correct refusal scores 0.0 without being ungrounded.  Averaging it across
    an abstention-heavy population measures the abstention *rate*, not answer
    quality — which is how a prompt change that lifted groundedness 0.98 →
    0.78 by abstaining on 44% of answerable questions read as a regression.

    A single mean is therefore not a reportable number.  This returns the
    abstention-aware split that is: the mean over answering questions, with
    the raw mean and the rate reported next to it so the movement is visible
    rather than hidden.

    Args:
        rows: ``{qid: {"answer": str, "groundedness_score": float}}``.

    Returns:
        ``groundedness_all`` / ``groundedness_answering`` / ``abstention_rate``
        / ``n`` / ``n_answering``.  ``groundedness_answering`` is ``None``
        when every answer abstained (no answering population to score).

    """
    grounds: list[float] = []
    answering: list[float] = []
    for row in (rows or {}).values():
        if not isinstance(row, dict):
            continue
        value = row.get("groundedness_score")
        if value is None:
            continue
        grounds.append(float(value))
        if not abstain_match(row.get("answer", "")):
            answering.append(float(value))

    n = len(grounds)
    return {
        "groundedness_all": (sum(grounds) / n) if n else None,
        "groundedness_answering": (sum(answering) / len(answering)) if answering else None,
        "abstention_rate": ((n - len(answering)) / n) if n else None,
        "n": n,
        "n_answering": len(answering),
    }

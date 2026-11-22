"""Tests for the per-question variant selector (app/rag/generation/variant_selector.py).

The selector decides what the user actually gets back, so the thing it can
get wrong matters.  The rule it used to apply was: *keep the candidates whose
citation-breadth proxy is within 0.02 of the baseline, then take the
shortest*.  That rule was measured against the repo's own arms and it does
not survive:

* the proxy rewards verbosity (a longer answer cites more chunks) while the
  tiebreak rewards brevity -- the two criteria pull in opposite directions;
* the 0.02 tolerance sits inside the measured same-config spread of the
  metric (soft mean|x| 0.035, length +-94pp between two runs of a
  byte-identical prompt -- ``evaluation/noise_floor.py``), so the tiebreak
  was decided by noise;
* truncation is not neutral.  Cutting an answer to 249 characters measured
  reference coverage collapsing from 0.5639 to 0.3348.

The rule is now **content first, brevity second**: keep the candidates that
retain the operative content, prefer the ones that answered, and take the
shortest only among content-equivalent candidates.

These tests pin that ordering.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag.generation.variant_selector import (  # noqa: E402
    CONTENT_EQUIVALENCE_BAND,
    VariantSelector,
    _is_abstention,
)
from app.rag.retrieval.result import RetrievedChunk  # noqa: E402


def _chunk(text: str, cid: str = "c1") -> RetrievedChunk:
    return RetrievedChunk(chunk_id=cid, text=text, score=1.0)


#: A source passage with the operative content a good answer must carry.
SOURCE = (
    "The Food Safety Officer may enter and inspect any place where food is "
    "manufactured, stored for sale, or exposed for sale, and take samples of "
    "such food for analysis. Any adulterant found in the possession of a "
    "manufacturer or distributor may be seized by the Food Safety Officer."
)

COMPLETE = (
    "Under Section 38(2) of the FSS Act, the Food Safety Officer may enter and "
    "inspect any place where food is manufactured, stored for sale, or exposed "
    "for sale, and take samples of such food for analysis [1]. Any adulterant "
    "found in the possession of a manufacturer or distributor may be seized by "
    "the Food Safety Officer [1]."
)

TRUNCATED = "Under Section 38(2) of the FSS Act, the Food Safety Officer may take samples for analysis [1]."

PADDED = COMPLETE + " " + "The Act may be read in context. " * 40

ABSTAIN = "The provided context does not contain the answer; it cannot be determined."


def _selector() -> VariantSelector:
    return VariantSelector(service=None)  # type: ignore[arg-type]


def _candidate(name: str, text: str, chunks: list[RetrievedChunk]) -> dict:
    sel = _selector()
    return {
        "name": name,
        "response": {"name": name},
        "text": text,
        "length": len(text),
        "abstained": _is_abstention(text),
        "soft_proxy": sel._soft_proxy(text, chunks),
        "content_proxy": sel._content_proxy(text, chunks),
    }


class TestContentProxy:
    def test_a_truncated_answer_retains_less_content(self):
        chunks = [_chunk(SOURCE)]
        sel = _selector()
        complete = sel._content_proxy(COMPLETE, chunks)
        truncated = sel._content_proxy(TRUNCATED, chunks)
        assert truncated < complete

    def test_padding_does_not_inflate_content_retention(self):
        # The proxy is normalised by the CONTEXT size, so an answer that adds
        # words without adding source content does not score higher.
        chunks = [_chunk(SOURCE)]
        sel = _selector()
        assert sel._content_proxy(PADDED, chunks) == pytest.approx(sel._content_proxy(COMPLETE, chunks))

    def test_an_empty_answer_retains_nothing(self):
        chunks = [_chunk(SOURCE)]
        sel = _selector()
        assert sel._content_proxy("", chunks) == 0.0
        assert sel._content_proxy(ABSTAIN, chunks) < sel._content_proxy(COMPLETE, chunks)

    def test_the_proxy_is_invariant_to_answer_length(self):
        # Two answers with the same content score the same, however long.
        chunks = [_chunk(SOURCE)]
        sel = _selector()
        assert sel._content_proxy(COMPLETE, chunks) == pytest.approx(
            sel._content_proxy(COMPLETE + " extra words here", chunks)
        )


class TestPickBest:
    def test_content_beats_brevity(self):
        # The complete answer is much longer; the truncated one is shorter.
        # Content wins.
        chunks = [_chunk(SOURCE)]
        results = [
            _candidate("baseline", COMPLETE, chunks),
            _candidate("tight_guardrail", TRUNCATED, chunks),
        ]
        chosen = _selector()._pick_best(results)
        assert chosen["name"] == "baseline"

    def test_brevity_breaks_ties_between_content_equivalent_candidates(self):
        chunks = [_chunk(SOURCE)]
        results = [
            _candidate("baseline", COMPLETE + " some additional context words", chunks),
            _candidate("tight_guardrail", COMPLETE, chunks),
        ]
        chosen = _selector()._pick_best(results)
        assert chosen["name"] == "tight_guardrail"

    def test_an_answer_beats_a_refusal_at_equal_content(self):
        chunks = [_chunk(SOURCE)]
        results = [
            _candidate("baseline", COMPLETE, chunks),
            _candidate("refuser", ABSTAIN, chunks),
        ]
        chosen = _selector()._pick_best(results)
        assert chosen["name"] == "baseline"

    def test_a_variant_that_retains_more_content_beats_the_baseline(self):
        # The eligibility band is anchored on the BEST candidate, not on the
        # baseline: a variant that genuinely retains more must not be
        # rejected for beating the baseline.
        chunks = [_chunk(SOURCE)]
        results = [
            _candidate("baseline", TRUNCATED, chunks),
            _candidate("full", COMPLETE, chunks),
        ]
        chosen = _selector()._pick_best(results)
        assert chosen["name"] == "full"

    def test_the_old_rule_would_have_picked_the_wrong_answer(self):
        # Regression guard for the flaw: the previous rule took the shortest
        # candidate whose citation-breadth proxy was within 0.02 of the
        # baseline.  The truncated answer cites the same single chunk, so it
        # passed that gate and won on length -- and lost a third of the
        # reference content.
        chunks = [_chunk(SOURCE)]
        baseline = _candidate("baseline", COMPLETE, chunks)
        truncated = _candidate("tight_guardrail", TRUNCATED, chunks)
        old_eligible = truncated["soft_proxy"] >= baseline["soft_proxy"] - 0.02
        assert old_eligible, "the old gate would have admitted the truncated answer"
        assert min([baseline, truncated], key=lambda r: r["length"])["name"] == "tight_guardrail"

        chosen = _selector()._pick_best([baseline, truncated])
        assert chosen["name"] == "baseline", "the new rule must reject the truncated answer"

    def test_the_band_is_tighter_than_the_measured_truncation_loss(self):
        # The measured loss from truncating to 249 characters was 0.21 in
        # reference coverage; the band must be far tighter than that, or the
        # selector would admit genuinely truncated answers.
        assert CONTENT_EQUIVALENCE_BAND < 0.10

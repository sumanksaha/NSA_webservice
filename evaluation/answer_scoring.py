"""Shared answer scoring: soft overlap + binary correctness (P0-3).

Extracted so the eval harness (``eval_e2e_v2``) and the P0-1 A/B
(``ab_p0_1_packing``) score answers with *identical* logic without either
importing the other's heavy module.

The binary rule is ``soft > threshold OR genuine abstention on an
insufficient-evidence question``.  The abstention arm exists because a
substantively correct refusal ("the corpus does not establish ...") scores
~0.3 on token overlap and was being marked wrong; the human audit attributed
38% of failures to the evaluator rather than the model.
"""

from __future__ import annotations

import re
from typing import Any

from evaluation.abstention_rule import abstain_credit

#: Frozen soft-overlap threshold — the historical v1 rule. Must not drift, so
#: the dual scorecard always compares against the number the 9-12% baseline used.
BINARY_CORRECTNESS_THRESHOLD = 0.5

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def score_answer(
    answer: str,
    expected: str,
    insufficient_evidence: bool,
    banned_markers: set[str] | None = None,
) -> dict[str, Any]:
    """Soft + binary correctness for one answer/reference pair."""
    ans_toks = _tokens(answer)
    exp_toks = _tokens(expected)
    if ans_toks and exp_toks:
        jaccard = len(ans_toks & exp_toks) / len(ans_toks | exp_toks)
        coverage = len(exp_toks & ans_toks) / len(exp_toks)
    else:
        jaccard = 0.0
        coverage = 0.0

    soft = (jaccard + coverage) / 2
    overlap_ok = soft > BINARY_CORRECTNESS_THRESHOLD
    abstention_credited = abstain_credit(answer or "", banned_markers, insufficient_evidence)

    return {
        "answer_correctness": round(soft, 4),
        "binary_correct": int(bool(overlap_ok or abstention_credited)),
        "binary_correct_overlap_only": int(overlap_ok),
        "binary_correct_abstention_credit": int(abstention_credited),
        "answer_jaccard": round(jaccard, 4),
        "answer_coverage": round(coverage, 4),
    }

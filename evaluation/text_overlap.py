"""Dependency-light text overlap helpers.

Extracted from experiment_b_topk_eval so modules that only need token overlap do
not pull matplotlib/torch/sentence-transformers at import time.
"""

from __future__ import annotations

import re

_TOK_RE = re.compile(r"[a-z0-9]+")


def _toks(s: str) -> set[str]:
    return set(_TOK_RE.findall(s.lower())) if s else set()


def token_overlap(answer: str, expected: str) -> dict[str, float]:
    a, e = _toks(answer), _toks(expected or "")
    if not e:
        return {"coverage": 0.0, "jaccard": 0.0, "correctness": 0.0}
    inter = len(a & e)
    cov = inter / len(e)
    jac = inter / len(a | e) if (a | e) else 0.0
    return {"coverage": cov, "jaccard": jac, "correctness": (jac + cov) / 2.0}

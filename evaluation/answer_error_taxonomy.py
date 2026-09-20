"""S28 step 4 — answer-error taxonomy for legal RAG answer failures.

Roadmap reference: ``Legal_RAG_Answer_Correctness_Improvement_Roadmap.md``
§22 (Error Taxonomy) and §28 steps 4–5 (Immediate Action Plan).

This module is deliberately **retrieval-taxonomy-free**: the existing
``evaluation/failure_taxonomy.py`` classifies *ranking* failures (gold in
pool but ranked below top-K, categories A–L).  What §22 needs instead is an
*answer* taxonomy — why a final answer is wrong even when evidence was
available (Exp B/C show R@100 ≈ 88% but correctness ≈ 36–38%).

Every classifier here is a deterministic heuristic pre-sort for the
human audit (§28 step 3), not a legal judgment.  A question marked
incorrect should end with one or more categories below; the human audit
then confirms or corrects via ``manual_labels``.
"""

from __future__ import annotations

import re
from typing import Any

#: Canonical §22 categories in table order.  Each entry carries the
#: diagnostic question and the intervention the roadmap prescribes.
CATEGORIES: dict[str, dict[str, str]] = {
    "retrieval": {
        "label": "Retrieval",
        "diagnostic": "Gold evidence unavailable in the retrieval pool?",
        "intervention": "Retrieval / CE reranker",
    },
    "context_assembly": {
        "label": "Context assembly",
        "diagnostic": "Evidence available in pool but omitted from LLM context?",
        "intervention": "Evidence selector",
    },
    "extraction": {
        "label": "Extraction",
        "diagnostic": "Rule present in context but not recognized in answer?",
        "intervention": "Reasoning representation",
    },
    "interpretation": {
        "label": "Interpretation",
        "diagnostic": "Rule recognized but misunderstood?",
        "intervention": "Legal reasoner",
    },
    "application": {
        "label": "Application",
        "diagnostic": "Correct rule applied incorrectly to the facts?",
        "intervention": "Fact-condition mapping",
    },
    "exception": {
        "label": "Exception",
        "diagnostic": "Proviso / exclusion / exception missed?",
        "intervention": "Exception detector",
    },
    "definition": {
        "label": "Definition",
        "diagnostic": "Defined term mishandled?",
        "intervention": "Definition resolver",
    },
    "multi_hop": {
        "label": "Multi-hop",
        "diagnostic": "Multiple provisions not combined?",
        "intervention": "Decomposition + graph",
    },
    "conflict": {
        "label": "Conflict",
        "diagnostic": "Competing provisions / temporal versions mishandled?",
        "intervention": "Hierarchy resolver",
    },
    "completeness": {
        "label": "Completeness",
        "diagnostic": "Answer incomplete (required provisions uncited)?",
        "intervention": "Auditor",
    },
    "citation": {
        "label": "Citation",
        "diagnostic": "Wrong / unsupported citation?",
        "intervention": "Citation verifier",
    },
    "evaluation": {
        "label": "Evaluation",
        "diagnostic": "Metric mismatch — legally acceptable but marked wrong?",
        "intervention": "Benchmark audit",
    },
}

#: Stable output order (matches the §22 table).
CATEGORY_ORDER: list[str] = list(CATEGORIES)

#: Word-boundary regexes — bare substrings over-fire on ordinary English
#: ("exception" contains "except"; "This means ..." is not a definition).
_EXCEPTION_PATTERNS = (
    r"\bexcept\b",
    r"\bnotwithstanding\b",
    r"\bprovided that\b",
    r"\bprovided further\b",
    r"\bsubject to\b",
    r"\bunless\b",
    r"\bsave as\b",
    r"\bproviso\b",
    r"\bdoes not apply\b",
    r"\bshall not apply\b",
)

#: A statutory definition needs a quoted term + "means" ("Food" means ...);
#: bare "means"/"includes" fires on ordinary prose.
_DEFINITION_PATTERNS = (
    r""""[^"]{1,80}"\s+means\b""",
    r"""'[^']{1,80}'\s+means\b""",
    r"\bdefinition\b",
    r"\bfor the purposes of\b",
    r"\bshall have the meaning\b",
)


def _has_marker(text: str, patterns: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in patterns)


_TRUE_STRINGS = {"true", "1", "yes", "y"}
_FALSE_STRINGS = {"false", "0", "no", "n"}


def _as_bool(value: Any) -> bool | None:
    """Strict tri-state parse: unrecognized strings → None (unknown), never False."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUE_STRINGS:
            return True
        if low in _FALSE_STRINGS:
            return False
        return None
    return None


def _verdict_known(value: Any) -> bool:
    """Whether a correctness verdict is an explicit pass/fail (vs missing/garbage)."""
    if value is None:
        return False
    if isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS | _FALSE_STRINGS
    return False


def is_failure_verdict(value: Any) -> bool:
    """Shared failure semantics (taxonomy + quantifier stay consistent).

    Fail-open: an unknown verdict (missing field, unparsable string) counts
    as a needs-audit failure rather than silently passing.  Fractional
    scores fail below 0.5.
    """
    if isinstance(value, float) and not value.is_integer():
        return value < 0.5
    return _as_bool(value) is not True


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _classify_impl(record: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """Core classifier returning ``(ordered_categories, reasons)``.

    Re-entrant and thread-safe — no module-global state (both public
    wrappers call this).  Multi-label by design; see module docstring.
    """
    manual = record.get("manual_labels") or []
    kept = [m for m in manual if m in CATEGORIES]

    verdict = record.get("correct", record.get("answer_correctness"))
    if not is_failure_verdict(verdict):
        return kept, {m: "human label" for m in kept}

    found: list[str] = []
    reasons: dict[str, str] = {}

    def _add(cat: str, reason: str) -> None:
        if cat not in found:
            found.append(cat)
            reasons[cat] = reason

    pool = _as_bool(record.get("gold_available_in_pool"))
    in_ctx = _as_bool(record.get("gold_in_context"))
    if pool is False:
        _add("retrieval", "gold evidence not in retrieval pool")
    elif pool is True and in_ctx is False:
        _add("context_assembly", "gold in pool but omitted from LLM context")

    provision_correct = _as_bool(record.get("provision_correct"))
    legal_correct = _as_bool(record.get("legal_correct"))
    if in_ctx is True and provision_correct is False:
        _add("extraction", "gold in context but cited provisions miss gold")
    if provision_correct is True and legal_correct is False:
        _add("interpretation", "right provision cited but conclusion mismatched")
    if provision_correct is True and legal_correct is True:
        _add("application", "right provision cited yet answer marked wrong — fact application suspect")

    answer = str(record.get("answer") or "")
    context_text = str(record.get("context_text") or "")
    ctx_exc = record.get("context_has_exception")
    ans_exc = record.get("answer_has_exception")
    if ctx_exc is None and context_text:
        ctx_exc = _has_marker(context_text, _EXCEPTION_PATTERNS)
    if ans_exc is None and answer:
        ans_exc = _has_marker(answer, _EXCEPTION_PATTERNS)
    if _as_bool(ctx_exc) is True and _as_bool(ans_exc) is False:
        _add("exception", "context states an exception/proviso the answer ignores")

    ctx_def = record.get("context_has_definition")
    ans_def = record.get("answer_has_definition")
    if ctx_def is None and context_text:
        ctx_def = _has_marker(context_text, _DEFINITION_PATTERNS)
    if ans_def is None and answer:
        ans_def = _has_marker(answer, _DEFINITION_PATTERNS)
    if _as_bool(ctx_def) is True and _as_bool(ans_def) is False:
        _add("definition", "context defines a term the answer never uses")

    n_units = _num(record.get("n_gold_units"))
    n_covered = _num(record.get("n_gold_covered"))
    if n_units is not None and n_units > 1:
        if n_covered is None or n_covered < n_units:
            _add("multi_hop", f"{n_covered} of {n_units} gold provisions covered")
    if _as_bool(record.get("has_conflicts")) or _as_bool(record.get("temporal_conflict")):
        _add("conflict", "evidence conflicts / temporal-version clash flagged")
    if _as_bool(record.get("completeness")) is False:
        _add("completeness", "grader completeness signal false")

    recall = _num(record.get("citation_recall"))
    precision = _num(record.get("citation_precision"))
    n_markers = _num(record.get("n_citation_markers"))
    if n_markers is None and answer.strip():
        n_markers = float(len(re.findall(r"\[\d+\]", answer)))
    if (recall is not None and recall < 0.5) or (precision is not None and precision < 0.5):
        _add("citation", f"citation recall={recall} precision={precision}")
    elif n_markers == 0 and answer.strip():
        # Suppress when the evidence never reached the context — the missing
        # citations are explained upstream (retrieval/context_assembly), so
        # reporting `citation` as well would inflate that bucket by construction.
        if "retrieval" not in found and "context_assembly" not in found:
            _add("citation", "non-empty answer with zero citation markers")

    jaccard = _num(record.get("answer_jaccard"))
    concl = _num(record.get("conclusion_overlap"))
    best_overlap = max([v for v in (jaccard, concl) if v is not None], default=None)
    if best_overlap is not None and best_overlap >= 0.6:
        _add("evaluation", f"high lexical overlap ({best_overlap}) yet marked incorrect")

    if not found and not kept:
        if not _verdict_known(verdict):
            _add("interpretation", "no correctness verdict recorded — needs human audit")
        else:
            _add("interpretation", "incorrect with no discriminating signals — needs human audit")

    ordered = [c for c in CATEGORY_ORDER if c in found]
    for m in kept:
        if m not in ordered:
            ordered.append(m)
            reasons.setdefault(m, "human label")
    return ordered, reasons


def classify_answer_failure(record: dict[str, Any]) -> list[str]:
    """Return §22 categories for one failed answer record.

    ``record`` fields are all optional; missing signals simply disable
    that detector (never invent evidence).  Recognized keys:

    ```text
    correct / answer_correctness (bool or 0/1)
    manual_labels (human audit override — always kept if valid)
    gold_available_in_pool, gold_in_context
    provision_correct, legal_correct, completeness
    citation_recall, citation_precision, n_citation_markers
    groundedness
    n_gold_units, n_gold_covered
    context_has_exception, answer_has_exception
    context_has_definition, answer_has_definition
    has_conflicts, temporal_conflict
    answer_jaccard, conclusion_overlap
    answer, context_text (raw strings for marker fallback)
    ```

    Unknown verdicts (missing/unparsable ``correct``) fail open to
    ``["interpretation"]`` as needs-audit placeholders — see
    :func:`is_failure_verdict`.
    """
    cats, _ = _classify_impl(record)
    return cats


def classify_with_reasons(record: dict[str, Any]) -> dict[str, str]:
    """Classify and return ``{category: reason}`` (human-audit worksheet)."""
    cats, reasons = _classify_impl(record)
    return {c: reasons.get(c, "") for c in cats}


def describe_categories() -> list[dict[str, str]]:
    """Return the §22 table as ``[{category, label, diagnostic, intervention}]``."""
    return [
        {
            "category": cat,
            "label": meta["label"],
            "diagnostic": meta["diagnostic"],
            "intervention": meta["intervention"],
        }
        for cat, meta in CATEGORIES.items()
    ]


__all__ = [
    "CATEGORIES",
    "CATEGORY_ORDER",
    "classify_answer_failure",
    "classify_with_reasons",
    "describe_categories",
    "is_failure_verdict",
]

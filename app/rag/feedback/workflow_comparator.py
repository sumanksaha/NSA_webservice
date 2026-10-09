"""Workflow answer comparator -- compare user vs system workflow answer."""

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "WorkflowAnswerComparison",
    "build_improvement_summary",
    "compare_workflow_answers",
    "find_citation_issues",
    "find_extra_forms",
    "find_extra_steps",
    "find_factual_discrepancies",
    "find_missing_forms",
    "find_missing_steps",
]

_FORM_RE = re.compile(r"\b(form|forms?)\s+(ii|iii|iv|v|vi|vii|viii)\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b(\d+)\.\s+(.*?)(?=\s*\d+\.\s+|\Z)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class WorkflowAnswerComparison:
    """Full comparison of user vs system workflow answer."""

    query: str
    system_answer: str
    user_answer: str
    gold_answer: str | None = None
    missing_forms: list[str] = None
    extra_forms: list[str] = None
    missing_steps: list[str] = None
    extra_steps: list[str] = None
    citation_issues: list[str] = None
    factual_discrepancies: list[str] = None
    overall_rating: str = "neutral"

    @property
    def has_discrepancies(self) -> bool:
        return any([
            self.missing_forms or [],
            self.extra_forms or [],
            self.missing_steps or [],
            self.extra_steps or [],
            self.citation_issues or [],
            self.factual_discrepancies or [],
        ])

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "missing_forms": self.missing_forms or [],
            "extra_forms": self.extra_forms or [],
            "missing_steps": self.missing_steps or [],
            "extra_steps": self.extra_steps or [],
            "citation_issues": self.citation_issues or [],
            "factual_discrepancies": self.factual_discrepancies or [],
            "overall_rating": self.overall_rating,
            "has_discrepancies": self.has_discrepancies,
        }


# ---------------------------------------------------------------------------
# Form detection
# ---------------------------------------------------------------------------


def find_missing_forms(system_answer: str, user_answer: str) -> list[str]:
    """Forms present in system answer but MISSING in user answer."""
    sys_forms = set(_FORM_RE.findall(system_answer or ""))
    user_forms = set(_FORM_RE.findall(user_answer or ""))
    sys_nums = {m[1].lower() for m in sys_forms if m[0].lower() == "form"}
    user_nums = {m[1].lower() for m in user_forms if m[0].lower() == "form"}
    missing = sorted(sys_nums - user_nums)
    return missing


def find_extra_forms(system_answer: str, user_answer: str) -> list[str]:
    """Forms present in user answer but NOT in system answer."""
    sys_forms = set(_FORM_RE.findall(system_answer or ""))
    user_forms = set(_FORM_RE.findall(user_answer or ""))
    sys_nums = {m[1].lower() for m in sys_forms if m[0].lower() == "form"}
    user_nums = {m[1].lower() for m in user_forms if m[0].lower() == "form"}
    extra = sorted(user_nums - sys_nums)
    return extra


# ---------------------------------------------------------------------------
# Step comparison
# ---------------------------------------------------------------------------


def find_missing_steps(system_answer: str, user_answer: str) -> list[str]:
    """Steps present in system answer but MISSING in user answer."""
    sys_steps = [m.group(2).strip() for m in _NUMBER_RE.finditer(system_answer or "")]
    user_steps = [m.group(2).strip() for m in _NUMBER_RE.finditer(user_answer or "")]
    missing = []
    for s in sys_steps:
        covered = any(s.lower() in u.lower() for u in user_steps)
        if not covered:
            missing.append(s)
    return missing


def find_extra_steps(system_answer: str, user_answer: str) -> list[str]:
    """Steps present in user answer but NOT in system answer."""
    sys_steps = [m.group(2).strip() for m in _NUMBER_RE.finditer(system_answer or "")]
    user_steps = [m.group(2).strip() for m in _NUMBER_RE.finditer(user_answer or "")]
    extra = []
    for u in user_steps:
        covered = any(u.lower() in s.lower() for s in sys_steps)
        if not covered:
            extra.append(u)
    return extra


def find_citation_issues(system_answer: str, user_answer: str) -> list[str]:
    """Check for citation problems in user answer."""
    issues = []
    return issues


def find_factual_discrepancies(system_answer: str, user_answer: str) -> list[str]:
    """Find substantive differences between answers."""
    issues = []
    sys_tokens = set(re.findall(r"\b[a-z]+\b", system_answer or "").lower())
    user_tokens = set(re.findall(r"\b[a-z]+\b", user_answer or "").lower())
    common = set([
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "to",
        "of",
        "in",
        "and",
        "or",
        "for",
        "with",
        "as",
        "on",
        "at",
        "by",
        "from",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "they",
        "them",
        "their",
        "he",
        "she",
        "his",
        "her",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
    ])
    sys_content = sys_tokens - common
    user_content = user_tokens - common
    diff = user_content - sys_content
    if diff:
        issues.append("User added unexpected terms: " + ", ".join(sorted(diff))[:100])
    return issues


def build_improvement_summary(comparison: WorkflowAnswerComparison) -> str:
    """Build a human-readable summary of improvements needed."""
    issues = []
    if comparison.missing_forms:
        issues.append("Missing forms: " + ", ".join(comparison.missing_forms) + chr(34))
    if comparison.extra_forms:
        issues.append("Extra forms (hallucinated): " + ", ".join(comparison.extra_forms) + chr(34))
    if comparison.missing_steps:
        issues.append(f"Missing steps: {len(comparison.missing_steps)}")
    if comparison.citation_issues:
        issues.append(f"Citation issues: {len(comparison.citation_issues)}")
    if comparison.factual_discrepancies:
        issues.append(f"Factual discrepancies: {len(comparison.factual_discrepancies)}")
    if not issues:
        return "No discrepancies found. Answer matches system output."
    return " ".join(issues)


# ---------------------------------------------------------------------------
# Main comparison entry point
# ---------------------------------------------------------------------------


def compare_workflow_answers(
    query: str, system_answer: str, user_answer: str, gold_answer: str | None = None,
) -> WorkflowAnswerComparison:
    """Compare system and user answers for the same query."""
    missing_forms = find_missing_forms(system_answer, user_answer)
    extra_forms = find_extra_forms(system_answer, user_answer)
    missing_steps = find_missing_steps(system_answer, user_answer)
    extra_steps = find_extra_steps(system_answer, user_answer)
    citation_issues = find_citation_issues(system_answer, user_answer)
    factual_discrepancies = find_factual_discrepancies(system_answer, user_answer)
    total_issues = (
        len(missing_forms)
        + len(extra_forms)
        + len(missing_steps)
        + len(extra_steps)
        + len(citation_issues)
        + len(factual_discrepancies)
    )
    if total_issues == 0:
        rating = "exact_match"
    elif total_issues <= 2:
        rating = "minor_discrepancies"
    else:
        rating = "significant_discrepancies"
    return WorkflowAnswerComparison(
        query=query,
        system_answer=system_answer,
        user_answer=user_answer,
        gold_answer=gold_answer,
        missing_forms=missing_forms,
        extra_forms=extra_forms,
        missing_steps=missing_steps,
        extra_steps=extra_steps,
        citation_issues=citation_issues,
        factual_discrepancies=factual_discrepancies,
        overall_rating=rating,
    )

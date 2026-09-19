"""Violation risk stratification (pure, no app / LLM / network).

Critical = direct food-safety hazard; major = statutory non-compliance;
minor = carried only when the caller says so (no field infers minor).
Matching is case-insensitive so RAG/inspection item keys (``water_report``)
and checklist fields (``Water_report``) resolve identically.
"""

from __future__ import annotations

from typing import Any

#: Checklist fields that are direct food-safety hazards (normalized lowercase).
CRITICAL_FIELDS = frozenset({
    "pest_report",
    "water_report",
    "refrigerator_clean",
    "expired_item",
})

_SEVERITIES = ("critical", "major", "minor")


def severity_of(field: str | None) -> str:
    """Infer severity for a checklist/item field (explicit value wins elsewhere)."""
    if (field or "").strip().lower() in CRITICAL_FIELDS:
        return "critical"
    return "major"


def stratify(shortcomings: list[dict[str, Any]]) -> dict[str, int]:
    """Count shortcomings per severity (explicit ``severity`` wins over inference)."""
    counts = {"critical": 0, "major": 0, "minor": 0}
    for shortcoming in shortcomings or []:
        if not isinstance(shortcoming, dict):
            continue  # type: ignore[unreachable]
        explicit = str(shortcoming.get("severity") or "").strip().lower()
        level = explicit if explicit in _SEVERITIES else severity_of(shortcoming.get("item"))
        counts[level] += 1
    return counts

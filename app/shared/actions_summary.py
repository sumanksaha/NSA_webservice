"""Unified view of corrective actions combining violations + FBO issues.

This module provides the core logic for the "well-formatted actions to be done"
request: union violations (from derive_violations) + FBO issues,
- deduplicate by violation field (e.g., "unclean premise" maps to "clean_premise" action)
- add generic recommendations for unique items
- present in a clean, inspectable JSON for downstream consumers (improvement notices, CAPA plans)

Usage example::

    from app.shared.actions_summary import build_unified_actions
    from app.shared.context_derivers import derive_violations

    violations = derive_violations(checklist)
    fbo_issues = get_fbo_issues_for_fbo(fbo_id)
    unified = build_unified_actions(violations, fbo_issues)
"""

from __future__ import annotations

from typing import Any

from app.shared.context_derivers import REMEDIATION_ACTIONS

# Canonical field-to-action mapping (reuse from context_derivers.REMEDIATION_ACTIONS)
# Keys are checklist fields; values are action directives.
_FIELD_TO_ACTION: dict[str, str] = REMEDIATION_ACTIONS


def _infer_field_from_text(text: str | None) -> str:
    """Convert a violation text title to the canonical field name for lookup.

    Short, deterministic mapping: lowercased phrase → field.
    This supports FBO issues that may not be checklist fields.
    """
    if not text:
        return "general"
    normalized = text.lower().strip()
    # Simple heuristic: clean_premise topics
    if any(word in normalized for word in ["clean", "premise", "hygienic"]):
        return "clean_premise"
    if any(word in normalized for word in ["refrigerator", "fridge"]):
        return "refrigerator_clean"
    if any(word in normalized for word in ["attire", "protective", "headgear"]):
        return "proper_attire"
    if any(word in normalized for word in ["cover", "uncovered", "food"]):
        return "proper_covered_utensil"
    if any(word in normalized for word in ["date", "tag", "traceability"]):
        return "date_tag"
    if any(word in normalized for word in ["veg", "non veg", "vegetarian", "separation"]):
        return "veg_nonveg_separation"
    if any(word in normalized for word in ["segregation", "cross contamination"]):
        return "food_segregation"
    if any(word in normalized for word in ["license", "fssai", "display"]):
        return "license_display"
    if any(word in normalized for word in ["pest", "pests"]):
        return "Pest_report"
    if any(word in normalized for word in ["water", "drinking"]):
        return "Water_report"
    if any(word in normalized for word in ["color", "artificial"]):
        return "artificial_colour"
    if any(word in normalized for word in ["expired", "expiry"]):
        return "Expired_item"
    return "general"


def _deduplicate_by_field(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one item per field; prefer earlier in the list."""
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item.get("field") or _infer_field_from_text(item.get("title"))
        if key not in seen:
            seen[key] = item
    return list(seen.values())


def build_unified_actions(
    violations: list[dict[str, Any]] | None = None,
    fbo_issues: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Produce a unified list of corrective actions (violations + FBO issues).

    Args:
        violations: Output of ``derive_violations`` (checklist origin).
        fbo_issues: Optional list of FBO issue dicts; each dict may have:
            - title: problem title
            - observation: free-form description (optional)
            - source_type: "inspection" or "sample" (optional)
            - state: "open", "closed", etc.
            - detail_json: structured extra info (optional)

    Returns:
        List of dicts with unified fields:
        {
            "title": str,          # user-friendly title
            "field": str,          # canonical lookup key
            "action": str | None,  # derived from REMEDIATION_ACTIONS (if any)
            "source": "checklist" | "fbo_issue",  # origin hint
            "metadata": {...},     # any extra fields for downstream consumers
        }
    """
    violations = violations or []
    fbo_issues = fbo_issues or []

    # Convert violations to unified format
    unified: list[dict[str, Any]] = []
    for v in violations:
        field = v.get("field", "general")
        title = v.get("title", "")
        observation = v.get("observation", "")
        unified.append({
            "title": title,
            "field": field,
            "action": _FIELD_TO_ACTION.get(field),
            "source": "checklist",
            "metadata": {"observation": observation},
        })

    # Convert FBO issues, infer field, add recommendations for unknown fields
    for issue in fbo_issues:
        title = issue.get("title", "")
        field = _infer_field_from_text(title)
        unified.append({
            "title": title,
            "field": field,
            "action": _FIELD_TO_ACTION.get(field),
            "source": "fbo_issue",
            "metadata": {k: v for k, v in issue.items() if k not in {"title"}},
        })

    # Deduplicate by field
    deduped = _deduplicate_by_field(unified)

    # Add generic recommendation for items without a direct action mapping
    for item in deduped:
        if not item.get("action"):
            item["action"] = f"Take immediate corrective action to address: {item['title']}."
            # Mark as needing manual review so we don't rely on auto-generated text
            item["needs_manual_review"] = True

    # Sort: checklist items first (preserve their order), then FBO-sourced items
    deduped.sort(key=lambda x: (x["source"] == "fbo_issue",))
    return deduped


__all__ = ["build_unified_actions"]

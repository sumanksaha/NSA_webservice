"""FBO audit context builders (pure, no app / LLM / network).

Turns :func:`derive_violations` output (``title`` / ``observation`` /
``field``) into the ``FBOAuditContext`` shape from
``docs/FBO_AUDITOR_AGENT_BLUEPRINT.md`` §3.1 — the single input contract
for :class:`FBOAuditorAgent`.
"""

from __future__ import annotations

from typing import Any

from app.auditor.severity import severity_of


def build_shortcomings(violations: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Map violation dicts to shortcomings with inferred severity."""
    shortcomings: list[dict[str, str]] = []
    for violation in violations or []:
        if not isinstance(violation, dict):
            continue  # type: ignore[unreachable]
        field = str(violation.get("field") or "general")
        shortcomings.append({
            "item": field,
            "observation": str(violation.get("observation") or violation.get("title") or ""),
            "severity": severity_of(field),
        })
    return shortcomings


def build_fbo_context(
    fbo_name: str | None = None,
    business_type: str | None = None,
    scale: str | None = None,
    compliance_deadline_days: int = 15,
    violations: list[dict[str, Any]] | None = None,
    fbo_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the ``FBOAuditContext`` input contract for the auditor agent."""
    return {
        "fbo_id": fbo_id or "",
        "fbo_name": fbo_name or "",
        "business_type": business_type or "General Food Establishment",
        "scale": scale or "Standard FBO",
        "compliance_deadline_days": compliance_deadline_days,
        "shortcomings": build_shortcomings(violations or []),
    }

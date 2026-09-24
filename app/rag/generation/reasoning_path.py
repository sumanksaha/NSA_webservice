"""Shared reasoning-path policy (arch review candidates 1+2).

One deep module behind the structured-reasoning revision loop, shared by
the graph node (``reasoning.py``), the audit router (``graph.py``
``route_after_audit``), and the Experiment D harness.  Owns:

- the revision policy: ``audit_failed`` / ``should_revise`` (the
  ``max_revisions`` contract lives here, not by agreement across three
  modules);
- the canonical defect-note format and correction-context assembly
  (previously two formats: the node used ``type: correction``, the
  harness ``- type: explanation => correction``);
- the structured user-content assembly shared by ``generate_node`` and
  the harness (``reasoning_user_content``; the evidence block is
  included only when an explicit context is passed, since the
  generation pipeline adds retrieved context separately).

Deterministic, no LLM.  Accepts ``AuditResult`` models or their
``model_dump()`` dicts anywhere an audit is taken.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.rag.agent.nodes.auditor import AuditResult, AuditResultDict

    #: Anything the revision policy accepts as an audit verdict.
    #: Type-only import (auditor lives under ``agent.nodes``; a runtime
    #: import here would cycle through the nodes package ``__init__``).
    AuditLike = AuditResultDict | AuditResult | None
else:
    AuditLike = Any

__all__ = [
    "DEFAULT_MAX_REVISIONS",
    "audit_failed",
    "defect_notes",
    "reasoning_user_content",
    "revision_context",
    "should_revise",
]

#: Single home of the revision budget.  Production ``initial_state`` and the
#: Experiment D harness both use 1 (one controlled correction pass); the old
#: should_revise/route fallback of 2 drifted from that contract.
DEFAULT_MAX_REVISIONS = 1


def _status(audit: AuditLike) -> str:
    if audit is None:
        return ""
    if isinstance(audit, Mapping):
        return str(audit.get("status") or "")
    return str(getattr(audit, "status", "") or "")


def audit_failed(audit: AuditLike) -> bool:
    """Whether ``audit`` is a FAIL verdict (missing audit never fails)."""
    return _status(audit) == "FAIL"


def should_revise(
    audit: AuditLike,
    revision_count: int,
    max_revisions: int | None = None,
) -> bool:
    """Revision policy: FAIL with budget left → revise, else generate.

    Single home of the ``max_revisions`` contract shared by the graph node,
    the audit router, and the Experiment D harness.  Default cap is
    :data:`DEFAULT_MAX_REVISIONS` (1) so a missing/invalid override cannot
    silently grant a second pass.
    """
    try:
        count = int(revision_count)
    except (TypeError, ValueError):
        count = 0
    if max_revisions is None:
        cap = DEFAULT_MAX_REVISIONS
    else:
        try:
            cap = int(max_revisions)
        except (TypeError, ValueError):
            cap = DEFAULT_MAX_REVISIONS
    return audit_failed(audit) and count < cap


def _defect_field(defect: Any, name: str) -> str:
    if isinstance(defect, Mapping):
        return str(defect.get(name) or "")
    return str(getattr(defect, name, "") or "")


def defect_notes(defects: Any) -> str:
    """Canonical per-defect notes, one ``- type: explanation => correction`` line each."""
    lines = []
    for defect in defects or []:
        lines.append(
            f"- {_defect_field(defect, 'defect_type')}: {_defect_field(defect, 'explanation')}"
            f" => {_defect_field(defect, 'required_correction')}"
        )
    return "\n".join(lines)


def revision_context(context: str, defects: Any) -> str:
    """Append the ``Correction required`` block; empty defects → unchanged."""
    notes = defect_notes(defects)
    if not notes:
        return context
    return f"{context}\n\nCorrection required:\n{notes}"


def reasoning_user_content(question: str, argument_json: str, context: str | None = None) -> str:
    """Assemble the structured user content for the final answer call.

    ``context`` is included as an explicit evidence block only when
    passed — the generation pipeline adds retrieved context separately,
    while the Experiment D harness passes it explicitly.
    """
    parts = [
        f"Question: {question}\n\nStructured reasoning:\n{argument_json}",
    ]
    if context is not None:
        parts.append(f"Evidence context:\n{context}")
    parts.append(
        "Final answer (cite sources with [n] markers; "
        "qualify conclusions the reasoning marks unknown):"
    )
    return "\n\n".join(parts)

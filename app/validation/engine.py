"""Validation orchestrator for the Phase 12 Legal Validation Engine.

:class:`ValidationEngine` resolves a case via :class:`CaseResolver`, gathers
the plain-dict ``case_data`` payload (assembled by
:class:`app.validation.data_assembler.CaseDataAssembler`), runs every
registered rule, and aggregates the findings into a composite readiness score:

    score = clamp(100 - 15 * error_count - 5 * warning_count, 0, 100)

Data gathering (the only ORM-touching surface) has been extracted into
:class:`~app.validation.data_assembler.CaseDataAssembler` so that rules
stay pure and tests can inject a pre-built ``case_data`` dict without a DB.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.shared.case_resolver import CaseResolver
from app.validation.data_assembler import CaseDataAssembler
from app.validation.rules import ERROR, INFO, RULES, WARNING, ValidationResult

logger = logging.getLogger(__name__)

#: Score grades — keyed by the lower bound of the range.
_GRADES: list[tuple[int, str]] = [
    (90, "Ready"),
    (75, "Needs attention"),
    (50, "At risk"),
    (0, "Not ready"),
]

_ERROR_PENALTY = 15
_WARNING_PENALTY = 5


class ValidationEngine:
    """Run all registered validation rules against a case and score it."""

    def __init__(self, rules: list[type] | None = None) -> None:
        #: Instantiated rules, in registry order (deterministic output).
        self._rules = [rule_cls() for rule_cls in (rules or RULES)]
        #: Assembles the plain-dict case_data payload (ORM + rendering).
        self._assembler = CaseDataAssembler()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def validate_case(self, case_id: int, case_type: str | None = None) -> dict:
        """Validate a case and return the structured result payload.

        Args:
            case_id: Primary key of the case file or adjudication.
            case_type: ``"case_file"`` or ``"adjudication"``.  When ``None``
                the resolver tries CaseFile first, then Adjudication.

        Returns:
            ``{"error": "Case not found"}`` when the case does not exist, or
            ``{score, grade, errors, warnings, suggestions, info, rules_run,
            case_id, adjudication_id, case_type, case_number}``.
        """
        resolved = CaseResolver().resolve(case_id, kind=case_type or None)
        if resolved is None:
            return {"error": "Case not found"}

        case_data = self._assembler.assemble(resolved)
        return self.validate_case_data(case_data, resolved)

    def validate_case_data(self, case_data: dict, resolved: Any | None = None) -> dict:
        """Validate a pre-built ``case_data`` dict (no DB required).

        Lets tests exercise the full rule suite with synthetic data.  When
        ``resolved`` is provided its metadata augments the returned payload;
        otherwise minimal defaults are used.
        """
        findings: list[ValidationResult] = []
        for rule in self._rules:
            try:
                findings.extend(rule.evaluate(case_data))
            except Exception as exc:  # a rule failure must never crash the report
                logger.error(
                    "Validation rule %s failed: %s",
                    rule.rule_id,
                    exc,
                )

        if resolved is None:
            from app.shared.case_resolver import ResolvedCase  # lazy

            resolved = ResolvedCase(
                case_id=case_data.get("case_id"),
                adjudication_id=case_data.get("adjudication_id"),
                case_type=case_data.get("case_type", "case_file"),
                case_number=case_data.get("case_number", ""),
                record=None,
            )
        return self._summarize(resolved, findings)

    # ------------------------------------------------------------------ #
    # Aggregation
    # ------------------------------------------------------------------ #

    def _summarize(self, resolved, findings: list[ValidationResult]) -> dict:
        """Group findings by severity, compute the score, and grade it."""
        errors = [r.to_dict() for r in findings if r.severity == ERROR]
        warnings = [r.to_dict() for r in findings if r.severity == WARNING]
        info = [r.to_dict() for r in findings if r.severity == INFO]

        score = max(
            0,
            min(
                100,
                100 - _ERROR_PENALTY * len(errors) - _WARNING_PENALTY * len(warnings),
            ),
        )
        grade = next(label for threshold, label in _GRADES if score >= threshold)

        suggestions: list[str] = []
        for finding in findings:
            if finding.suggestion and finding.suggestion not in suggestions:
                suggestions.append(finding.suggestion)
        if not suggestions:
            suggestions = [r["message"] for r in info]

        return {
            "score": score,
            "grade": grade,
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
            "info": info,
            "rules_run": len(self._rules),
            "case_id": resolved.case_id,
            "adjudication_id": resolved.adjudication_id,
            "case_type": resolved.case_type,
            "case_number": resolved.case_number,
            "generated_at": datetime.now(UTC).isoformat(),
        }


__all__ = ["ValidationEngine"]

"""Validation orchestrator for the Phase 12 Legal Validation Engine.

:class:`ValidationEngine` resolves a case via :class:`CaseResolver`, gathers
the plain-dict ``case_data`` payload consumed by the rules (see
``app/validation/rules.py``), runs every registered rule, and aggregates the
findings into a composite readiness score:

    score = clamp(100 - 15 * error_count - 5 * warning_count, 0, 100)

The engine is the only module that touches the ORM — rules stay pure.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.extensions import db
from app.shared.case_resolver import CaseResolver
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


def _iso(value) -> str | None:
    """Serialize a datetime to ISO-8601, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class ValidationEngine:
    """Run all registered validation rules against a case and score it."""

    def __init__(self, rules: list[type] | None = None) -> None:
        #: Instantiated rules, in registry order (deterministic output).
        self._rules = [rule_cls() for rule_cls in (rules or RULES)]

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

        case_data = self._build_case_data(resolved)

        findings: list[ValidationResult] = []
        for rule in self._rules:
            try:
                findings.extend(rule.evaluate(case_data))
            except Exception as exc:  # a rule failure must never crash the report
                logger.error("Validation rule %s failed for %s %s: %s",
                             rule.rule_id, resolved.case_type, resolved.case_id, exc)

        return self._summarize(resolved, findings)

    # ------------------------------------------------------------------ #
    # Data gathering (the only ORM-touching surface)
    # ------------------------------------------------------------------ #

    def _build_case_data(self, resolved) -> dict:
        """Assemble the plain-dict payload consumed by every rule."""
        from app.models import Annexure, Evidence, Sample

        record = resolved.record
        if resolved.case_type == "case_file":
            from app.case_file_generator.routes import case_file_to_dict

            fields = case_file_to_dict(record)
        else:
            from app.adjudication.routes import adjudication_to_dict

            fields = adjudication_to_dict(record)

        link_kwargs = (
            {"case_id": resolved.case_id}
            if resolved.case_type == "case_file"
            else {"adjudication_id": resolved.adjudication_id}
        )

        annexures = [
            self._serialize_annexure(a)
            for a in Annexure.query.filter_by(**link_kwargs)
            .order_by(Annexure.uploaded_at.asc())
            .all()
        ]
        evidence = [
            self._serialize_evidence(e)
            for e in Evidence.query.filter_by(**link_kwargs)
            .order_by(Evidence.uploaded_at.asc())
            .all()
        ]

        sample = None
        if resolved.case_type == "case_file" and record.sample_id is not None:
            sample_record = db.session.get(Sample, record.sample_id)
            if sample_record is not None:
                sample = {
                    "id": sample_record.id,
                    "sample_code": sample_record.sample_code,
                    "sample_name": sample_record.sample_name,
                    "sample_type": sample_record.sample_type,
                    "collection_date": _iso(sample_record.collection_date),
                    "submission_date": _iso(sample_record.submission_date),
                }

        document_html, document_html_permission = self._render_documents(resolved)

        suggested_sections = {"sections": [], "reasoning": {}}
        if resolved.case_type == "adjudication":
            from app.utils.suggester import suggest_sections

            suggested_sections = suggest_sections(fields)

        return {
            "case_id": resolved.case_id,
            "adjudication_id": resolved.adjudication_id,
            "case_type": resolved.case_type,
            "case_number": resolved.case_number,
            "fields": fields,
            "annexures": annexures,
            "evidence": evidence,
            "sample": sample,
            "document_html": document_html,
            "document_html_permission": document_html_permission,
            "suggested_sections": suggested_sections,
        }

    def _render_documents(self, resolved) -> tuple[str, str]:
        """Render petition + permission HTML for a case (best-effort).

        Rendering failures (missing files, template errors) are non-fatal:
        the signature/completeness rules see empty HTML and degrade to an
        INFO note instead of crashing the report.
        """
        try:
            if resolved.case_type == "case_file":
                from app.document_viewer.renderer import render_case_file_document

                petition = render_case_file_document(resolved.case_id, "petition")
                permission = render_case_file_document(resolved.case_id, "permission")
            else:
                from app.document_viewer.renderer import render_adjudication_document

                petition = render_adjudication_document(resolved.adjudication_id, "petition")
                permission = render_adjudication_document(resolved.adjudication_id, "permission")
            return str(petition or ""), str(permission or "")
        except Exception as exc:
            logger.warning(
                "Validation: document rendering failed for %s %s: %s",
                resolved.case_type,
                resolved.case_id,
                exc,
            )
            return "", ""

    @staticmethod
    def _serialize_annexure(a) -> dict:
        return {
            "id": a.id,
            "caption": a.caption,
            "date": _iso(a.date),
            "file_hash": a.file_hash,
            "filename": a.filename,
            "file_size": a.file_size,
            "mime_type": a.mime_type,
            "page_count": a.page_count,
            "ocr_text": a.ocr_text,
            "tags": a.tags,
            "annexure_letter": a.annexure_letter,
            "case_id": a.case_id,
            "adjudication_id": a.adjudication_id,
            "uploaded_at": _iso(a.uploaded_at),
        }

    @staticmethod
    def _serialize_evidence(e) -> dict:
        return {
            "id": e.id,
            "evidence_type": e.evidence_type,
            "caption": e.caption,
            "filename": e.filename,
            "file_hash": e.file_hash,
            "file_size": e.file_size,
            "mime_type": e.mime_type,
            "verification_status": e.verification_status,
            "case_id": e.case_id,
            "adjudication_id": e.adjudication_id,
            "uploaded_at": _iso(e.uploaded_at),
        }

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

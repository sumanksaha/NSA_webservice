"""Case data assembler for the Phase 12 Legal Validation Engine.

Extracted from ValidationEngine so _build_case_data becomes a public,
testable unit. Tests can construct case_data directly without a DB.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.extensions import db

logger = logging.getLogger(__name__)
CASE_DATA_KEYS = frozenset({
    "case_id",
    "adjudication_id",
    "case_type",
    "case_number",
    "fields",
    "annexures",
    "evidence",
    "sample",
    "document_html",
    "document_html_permission",
    "suggested_sections",
})


def _iso(value):
    """Serialize a datetime to ISO-8601, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class CaseDataAssembler:
    """Assemble the plain-dict payload consumed by every validation rule."""

    def assemble(self, resolved: Any) -> dict:
        """Build the full case_data dict from a ResolvedCase."""
        from app.models import Annexure, Evidence

        record = resolved.record
        if resolved.case_type == "case_file":
            from app.case_file_generator.routes import case_file_to_dict

            fields = case_file_to_dict(record)
        else:
            from app.adjudication.routes import adjudication_to_dict

            fields = adjudication_to_dict(record)

        if resolved.case_type == "case_file":
            link_kwargs = {"case_id": resolved.case_id}
        else:
            link_kwargs = {"adjudication_id": resolved.adjudication_id}

        annexures = [
            self.serialize_annexure(a)
            for a in Annexure.query.filter_by(**link_kwargs).order_by(Annexure.uploaded_at.asc()).all()
        ]
        evidence = [
            self.serialize_evidence(e)
            for e in Evidence.query.filter_by(**link_kwargs).order_by(Evidence.uploaded_at.asc()).all()
        ]

        sample = self._serialize_sample(record, resolved.case_type)
        doc_html, doc_html_perm = self.render_documents(resolved)

        suggested_sections: dict = {"sections": [], "reasoning": {}}
        if resolved.case_type == "adjudication":
            from app.plugins.registry import PluginRegistry

            rule_provider = PluginRegistry.get_instance().get_active("rules")
            suggested_sections = rule_provider.suggest_sections(fields)

        return {
            "case_id": resolved.case_id,
            "adjudication_id": resolved.adjudication_id,
            "case_type": resolved.case_type,
            "case_number": resolved.case_number,
            "fields": fields,
            "annexures": annexures,
            "evidence": evidence,
            "sample": sample,
            "document_html": doc_html,
            "document_html_permission": doc_html_perm,
            "suggested_sections": suggested_sections,
        }

    def from_dict(self, data: dict) -> dict:
        """Pass-through for testability -- returns data unchanged."""
        return data

    # --- Serialization helpers ---

    def _serialize_sample(self, record, case_type):
        if case_type != "case_file":
            return None
        sample_id = getattr(record, "sample_id", None)
        if sample_id is None:
            return None
        from app.models import Sample

        sample_record = db.session.get(Sample, sample_id)
        if sample_record is None:
            return None
        return {
            "id": sample_record.id,
            "sample_code": sample_record.sample_code,
            "sample_name": sample_record.sample_name,
            "sample_type": sample_record.sample_type,
            "collection_date": _iso(sample_record.collection_date),
            "submission_date": _iso(sample_record.submission_date),
        }

    @staticmethod
    def render_documents(resolved):
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
                "Validation: rendering failed for %s %s: %s",
                resolved.case_type,
                resolved.case_id,
                exc,
            )
            return "", ""

    @staticmethod
    def serialize_annexure(a):
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
    def serialize_evidence(e):
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


__all__ = ["CASE_DATA_KEYS", "CaseDataAssembler"]

"""Adoc→DOCX rendering bound to the case_file_generator template dir.

Thin wrapper over :mod:`app.shared.adoc_renderer` (ADR-001 pipeline), the
same pattern the non-sample adjudication blueprint uses.
"""

from __future__ import annotations

from pathlib import Path

from app.shared.adoc_renderer import render_adoc_to_docx

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "case_file_generator"

# .adoc templates keyed by doc_type (source of truth per ADR-001)
ADOC_TEMPLATES = {
    "petition": "petition.adoc",
    "permission": "permission_letter.adoc",
}


def render_docx(doc_type: str, context: dict) -> bytes:
    """Render a case-file .adoc template to DOCX bytes.

    ``doc_type`` is ``petition`` or ``permission``.
    """
    template_name = ADOC_TEMPLATES[doc_type]
    return render_adoc_to_docx(TEMPLATE_DIR, template_name, context)

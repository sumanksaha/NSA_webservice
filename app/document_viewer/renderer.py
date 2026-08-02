"""Shared document rendering helpers for the editor.

These functions extract the "fetch case data -> build context -> render template
to HTML string" logic that is currently duplicated inline in
``generate_case_file_route()``, ``generate_case_file_pdf()`` (case_file_generator),
and ``generate_all()`` / ``regenerate_adjudication_documents()`` (adjudication).
"""

from datetime import datetime

from flask import render_template

from app.shared.case_keys import (
    DERIVED_APPLICABLE_SECTIONS,
    DERIVED_CASE_TRACK,
    DERIVED_SAME_ENTITY,
    DERIVED_SECTIONS_DISPLAY,
    DERIVED_VIOLATIONS,
    SECTION_55,
    SECTION_56,
    SECTION_58,
    SECTION_63,
    SECTION_64,
    SHARED_COMPLAINT_LODGED,
    SHARED_NON_LICENSE,
    SHARED_PRE_AUTHORIZATION,
)
from app.shared.context_derivers import (
    derive_applicable_sections_from_adjudication,
    derive_case_track,
    derive_sections_display,
    derive_violations,
)
from app.utils.pdf_utils import embed_photos_as_base64


def render_case_file_document(case_id: int, doc_type: str) -> str:
    """Render a CaseFile document (petition or permission letter) to HTML.

    Reuses the existing ``case_file_to_dict()`` and ``process_form_data()``
    functions from ``case_file_generator/routes.py``.
    """
    from app.case_file_generator.routes import case_file_to_dict, process_form_data
    from app.models import CaseFile

    case_file = CaseFile.query.get_or_404(case_id)
    form_data = case_file_to_dict(case_file)
    case_data = process_form_data(form_data)

    if doc_type == "petition":
        template = "case_file_generator/petition.html"
    else:
        template = "case_file_generator/permission_letter.html"

    return str(render_template(template, **case_data))


def build_adjudication_context(form_data: dict) -> dict:
    """Build the render context dict for adjudication documents.

    Extracted from the inline logic in ``adjudication/routes.py``
    ``generate_all()`` (lines ~571-651) and ``regenerate_adjudication_documents()``
    (lines ~313-392). Both functions can call this helper to avoid duplication.
    """
    # Get section checkboxes
    section_55 = form_data.get(SECTION_55, "no")
    section_56 = form_data.get(SECTION_56, "no")
    section_58 = form_data.get(SECTION_58, "no")
    section_63 = form_data.get(SECTION_63, "no")
    section_64 = form_data.get(SECTION_64, "no")

    # Get case flags
    non_license = form_data.get(SHARED_NON_LICENSE, "no")
    pre_authorization = form_data.get(SHARED_PRE_AUTHORIZATION, "no")
    complaint_lodged = form_data.get(SHARED_COMPLAINT_LODGED, "no")

    # Derive applicable sections
    applicable_sections = derive_applicable_sections_from_adjudication(
        section_55=section_55,
        section_56=section_56,
        section_58=section_58,
        section_63=section_63,
        section_64=section_64,
    )

    # Render context
    context = form_data.copy()
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    # Add canonical derived context fields
    context[DERIVED_APPLICABLE_SECTIONS] = applicable_sections
    context[DERIVED_SECTIONS_DISPLAY] = derive_sections_display(applicable_sections)
    context[DERIVED_CASE_TRACK] = derive_case_track(
        non_license=non_license,
        pre_authorization=pre_authorization,
        complaint_lodged=complaint_lodged,
        is_sample=False,
    )
    context[DERIVED_VIOLATIONS] = derive_violations(form_data)
    context[DERIVED_SAME_ENTITY] = False  # Adjudication doesn't use same_entity

    # Backward compatible violations field
    context["violations"] = context[DERIVED_VIOLATIONS]

    return context


def render_adjudication_document(case_id: int, doc_type: str) -> str:
    """Render an Adjudication document (petition or permission letter) to HTML.

    Reuses the existing ``adjudication_to_dict()`` from ``adjudication/routes.py``
    and the extracted ``build_adjudication_context()`` helper.
    """
    from app.adjudication.routes import adjudication_to_dict
    from app.models import Adjudication, PhotoEvidence

    adj = Adjudication.query.get_or_404(case_id)
    form_data = adjudication_to_dict(adj)

    context = build_adjudication_context(form_data)

    # Photo Evidence Integration -- all photos for this case
    all_photos = PhotoEvidence.query.filter_by(case_id=adj.id).order_by(PhotoEvidence.captured_at.asc()).all()

    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]

    context["adjudication"] = {
        "photos": verified_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in verified_photos]),
    }

    if doc_type == "petition":
        template = "adjudication/template_nonsample_petition.html"
    else:
        template = "adjudication/Legal_NonsampleAdjudication_Template.html"

    return str(render_template(template, **context))

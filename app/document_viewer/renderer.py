"""Shared document rendering helpers for the editor.

These functions extract the "fetch case data -> build context -> render template
to HTML string" logic that is currently duplicated inline in
``generate_case_file_route()``, ``generate_case_file_pdf()`` (case_file_generator),
and ``generate_all()`` / ``regenerate_adjudication_documents()`` (adjudication).
"""

from datetime import datetime

from flask import render_template

from app.utils.pdf_utils import post_process_pdf_html


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

    html = str(render_template(template, **case_data))
    # Phase 6: cross-reference pass (list renumbering + annexure enclosures).
    return post_process_pdf_html(html, case_id=case_id)


def build_adjudication_context(form_data: dict) -> dict:
    """Build the render context dict for adjudication documents.

    Delegates to the canonical builder in ``adjudication/routes.py`` (same
    section reads, derive calls, and date normalisation) and adds the
    render-time ``compilation_date`` the viewer stamps on documents.
    """
    from app.adjudication.routes import _prepare_adjudication_context

    context = _prepare_adjudication_context(form_data)
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")
    return context


def render_adjudication_document(case_id: int, doc_type: str) -> str:
    """Render an Adjudication document (petition or permission letter) to HTML.

    Reuses the existing ``adjudication_to_dict()`` from ``adjudication/routes.py``
    and the extracted ``build_adjudication_context()`` helper.
    """
    from app.adjudication.routes import adjudication_to_dict
    from app.models import Adjudication

    adj = Adjudication.query.get_or_404(case_id)
    form_data = adjudication_to_dict(adj)

    context = build_adjudication_context(form_data)

    # Photo Evidence Integration -- verified photos for this adjudication,
    # selected and embedded via the photo seam.
    from app.shared.photo_selection import select_for_document

    selection = select_for_document(adjudication_id=adj.id)
    context["adjudication"] = {
        "photos": selection.photos,
        "photo_embeds": selection.embeds,
    }

    if doc_type == "petition":
        template = "adjudication/template_nonsample_petition.html"
    else:
        template = "adjudication/Legal_NonsampleAdjudication_Template.html"

    html = str(render_template(template, **context))
    # Phase 6: cross-reference pass (list renumbering + annexure enclosures).
    return post_process_pdf_html(html, adjudication_id=case_id)

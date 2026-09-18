"""Editor / cross-reference / TOC report rendering (split from DocumentCaseManager).

Each function takes the model, ``case_type``/``bp_name`` strings, and any
model-specific callbacks explicitly — no manager instance required.
"""

from __future__ import annotations

from typing import Any

from flask import render_template, url_for

from app.shared import document_lookup as lookup


def _render_document(case_type: str, case_id: int, doc_type: str) -> str:
    """Render a document via the appropriate renderer function."""
    from app.document_viewer.renderer import (
        render_adjudication_document,
        render_case_file_document,
    )

    render_fn = render_case_file_document if case_type == "case_file" else render_adjudication_document
    return render_fn(case_id, doc_type)


def _generate_xref_report(case_type: str, annotated_html: str, case_id: int) -> dict:
    """Cross-reference report data for rendered HTML."""
    from app.cross_reference import generate_xref_report_data

    return generate_xref_report_data(annotated_html, **lookup.case_kwarg(case_type, case_id))


def render_editor(model: type, case_type: str, bp_name: str, case_id: int) -> str:
    """Render the Quill editor page pre-filled with a case's documents."""
    case = lookup.get_case(model, case_id)
    if case is None:
        return ""
    from app.document_viewer.renderer import (
        render_adjudication_document,
        render_case_file_document,
    )

    render_fn = render_case_file_document if case_type == "case_file" else render_adjudication_document
    return render_template(
        "document_viewer/editor.html",
        case_number=case.case_number,
        case_id=case.id,
        case_type=case_type,
        petition_html=render_fn(case_id, "petition"),
        permission_html=render_fn(case_id, "permission"),
        report_url=url_for(f"{bp_name}.xref_report", case_id=case_id),
        toc_url=url_for(f"{bp_name}.toc_report", case_id=case_id),
    )


def xref_report(model: type, case_type: str, bp_name: str, case_id: int, doc_type: str = "petition") -> str:
    """Render the cross-reference report for a case."""
    case = lookup.get_case(model, case_id)
    if case is None:
        return ""
    annotated_html = _render_document(case_type, case_id, doc_type)
    report = _generate_xref_report(case_type, annotated_html, case_id)
    return render_template(
        "xref_report.html",
        case_number=lookup.get_case_number(case),
        fbo_name=lookup.get_fbo_name(case_type, case),
        food_safety_officer=lookup.get_fso(case_type, case),
        doc_type=doc_type,
        report=report,
        annotated_html=annotated_html,
        report_url=url_for(f"{bp_name}.xref_report", case_id=case_id),
        renumber_url=url_for(f"{bp_name}.renumber_annexures", case_id=case_id),
    )


def toc_report(model: type, case_type: str, bp_name: str, case_id: int, doc_type: str = "petition") -> str:
    """Render the table-of-contents report for a case."""
    case = lookup.get_case(model, case_id)
    if case is None:
        return ""
    annotated_html = _render_document(case_type, case_id, doc_type)
    from app.toc_generator import generate_toc_data
    from app.toc_generator.engine import TocGeneratorEngine

    toc_data = generate_toc_data(annotated_html)
    toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc(annotated_html))
    return render_template(
        "toc_report.html",
        case_number=lookup.get_case_number(case),
        fbo_name=lookup.get_fbo_name(case_type, case),
        food_safety_officer=lookup.get_fso(case_type, case),
        doc_type=doc_type,
        toc_data=toc_data,
        toc_html=toc_html,
        annotated_html=annotated_html,
        toc_url=url_for(f"{bp_name}.toc_report", case_id=case_id),
    )


def renumber_annexures(case_type: str, case_id: int) -> dict:
    """Renumber annexure letters in upload order."""
    from app.cross_reference.engine import CrossReferenceEngine

    updates = CrossReferenceEngine().renumber_annexures(**lookup.case_kwarg(case_type, case_id))
    return {"status": "ok", "updates": updates, "count": len(updates)}


def render_document_for_report(case_type: str, case_id: int, doc_type: str) -> str:
    """Render + annotate a document for the xref/TOC report views."""
    return _render_document(case_type, case_id, doc_type)


def xref_report_data(case_type: str, annotated_html: str, case_id: int) -> dict:
    """Xref report data for already-rendered HTML (route views)."""
    return _generate_xref_report(case_type, annotated_html, case_id)


def toc_report_data(annotated_html: str) -> tuple[Any, str]:
    """``(toc_data, toc_html)`` for already-rendered HTML (route views)."""
    from app.toc_generator import generate_toc_data
    from app.toc_generator.engine import TocGeneratorEngine

    return generate_toc_data(annotated_html), TocGeneratorEngine().build_toc_html(
        TocGeneratorEngine().extract_toc(annotated_html)
    )

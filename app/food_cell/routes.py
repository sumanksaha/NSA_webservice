"""Food Cell routes — DO Intimation download / HTML view / regenerate.

Also serves Improvement Notice documents (u/s 32 of the FSS Act), which are
always keyed to an *Inspection* (never a Sample). The first render/download
freezes the inspection record via ``Inspection.notice_issued_at``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from flask import (
    abort,
    jsonify,
    render_template,
    send_file,
)
from flask_login import login_required

from app.extensions import db
from app.food_cell import food_cell_bp
from app.food_cell.renderer import DODocumentRenderer
from app.food_cell.services import generate_and_forward_do_intimation
from app.models.billing import Sample
from app.models.food_cell import DoIntimation
from app.models.inspection import Inspection
from app.shared.context_derivers import derive_actions, derive_violations


@food_cell_bp.route("/do-intimation/<int:sample_id>/pdf")
@login_required
def download_do_intimation_pdf(sample_id: int):
    """Download the DO intimation PDF for *sample_id*."""
    intimation = DoIntimation.query.filter_by(sample_id=sample_id).first()
    if intimation is None or intimation.pdf_url is None:
        abort(404, description="DO intimation PDF not found. Generate it first.")
    pdf_path = intimation.pdf_url
    if not os.path.isfile(pdf_path):
        abort(404, description="DO intimation PDF file not found on disk.")
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"DO_Intimation_{sample_id}.pdf",
        mimetype="application/pdf",
    )


@food_cell_bp.route("/do-intimation/<int:sample_id>/html")
@login_required
def view_do_intimation_html(sample_id: int):
    """View the DO intimation HTML inline in the browser."""
    intimation = DoIntimation.query.filter_by(sample_id=sample_id).first()
    if intimation is None or intimation.html_path is None:
        abort(404, description="DO intimation HTML not found. Generate it first.")
    html_path = intimation.html_path
    if not os.path.isfile(html_path):
        abort(404, description="DO intimation HTML file not found on disk.")
    with open(html_path, encoding="utf-8") as fh:
        html_content = fh.read()
    sample = db.session.get(Sample, sample_id)
    return render_template(
        "food_cell/do_intimation_inline.html",
        html_content=html_content,
        sample=sample,
        intimation=intimation,
    )


@food_cell_bp.route("/do-intimation/<int:sample_id>/regenerate", methods=["POST"])
@login_required
def regenerate_do_intimation(sample_id: int):
    """Force-regenerate the DO intimation for *sample_id* (manual re-render)."""
    sample = db.session.get(Sample, sample_id)
    if sample is None:
        abort(404, description="Sample not found.")
    intimation = generate_and_forward_do_intimation(sample_id, sample=sample, force=True)
    if intimation is None:
        return jsonify({"error": "Failed to generate DO intimation"}), 500
    return jsonify({
        "intimation_id": intimation.id,
        "do_reference_no": intimation.do_reference_no,
        "status": intimation.status,
        "pdf_url": intimation.pdf_url,
        "sync_status": intimation.sync_status,
        "food_cell_forwarded": (intimation.food_cell_forwarded.isoformat() if intimation.food_cell_forwarded else None),
    }), 200


@food_cell_bp.route("/do-intimation/<int:sample_id>/status")
@login_required
def do_intimation_status(sample_id: int):
    """Return JSON status of the DO intimation for *sample_id*."""
    intimation = DoIntimation.query.filter_by(sample_id=sample_id).first()
    if intimation is None:
        return jsonify({"exists": False}), 200
    return (
        jsonify({
            "exists": True,
            "intimation_id": intimation.id,
            "do_reference_no": intimation.do_reference_no,
            "status": intimation.status,
            "food_cell_forwarded": (
                intimation.food_cell_forwarded.isoformat() if intimation.food_cell_forwarded else None
            ),
            "sync_status": intimation.sync_status,
            "has_html": bool(intimation.html_path),
            "has_pdf": bool(intimation.pdf_url),
        }),
        200,
    )


# ------------------------------------------------------------------------- #
# Improvement Notice routes (u/s 32 FSS Act) — inspection-keyed
# ------------------------------------------------------------------------- #


def _inspection_violations(inspection: Inspection) -> list[dict[str, str]]:
    """Derive violations from the inspection's stored checklist."""
    try:
        checklist = json.loads(inspection.checklist_json) if inspection.checklist_json else {}
    except (ValueError, TypeError):
        checklist = {}
    return derive_violations(checklist)


# ponytail: stateless class, one instance per request is fine; share a module
# singleton only if profiling ever says so.
_notice_renderer = DODocumentRenderer()


def _freeze_inspection(inspection: Inspection) -> None:
    """Stamp first-issue time; non-null ``notice_issued_at`` freezes edits."""
    if inspection.notice_issued_at is None:
        inspection.notice_issued_at = datetime.now(UTC)
        db.session.commit()


@food_cell_bp.route("/improvement-notice/inspection/<int:inspection_id>/html")
@login_required
def view_improvement_notice_html(inspection_id: int):
    """View the Improvement Notice HTML for an *inspection*."""
    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        abort(404, description="Inspection not found.")
    violations = _inspection_violations(inspection)
    if not violations:
        return jsonify({"error": "No violations recorded for this inspection; no notice to issue."}), 400

    actions = derive_actions(violations)
    deadline = inspection.compliance_deadline.strftime("%d/%m/%Y") if inspection.compliance_deadline else None
    html = _notice_renderer.render_improvement_notice_html(
        inspection, violations=violations, actions=actions, compliance_deadline=deadline
    )
    _freeze_inspection(inspection)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@food_cell_bp.route("/improvement-notice/inspection/<int:inspection_id>/pdf")
@login_required
def download_improvement_notice_pdf(inspection_id: int):
    """Download the Improvement Notice PDF for an *inspection*."""
    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        abort(404, description="Inspection not found.")
    violations = _inspection_violations(inspection)
    if not violations:
        return jsonify({"error": "No violations recorded for this inspection; no notice to issue."}), 400

    actions = derive_actions(violations)
    deadline = inspection.compliance_deadline.strftime("%d/%m/%Y") if inspection.compliance_deadline else None
    html = _notice_renderer.render_improvement_notice_html(
        inspection, violations=violations, actions=actions, compliance_deadline=deadline
    )
    pdf_path = _notice_renderer.render_improvement_notice_pdf(html, inspection)
    _freeze_inspection(inspection)
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"Improvement_Notice_{inspection_id}.pdf",
        mimetype="application/pdf",
    )

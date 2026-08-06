"""Food Cell routes — DO Intimation download / HTML view / regenerate."""

from __future__ import annotations

import os

from flask import (
    abort,
    current_app,
    jsonify,
    render_template,
    send_file,
)
from flask_login import login_required

from app.extensions import db
from app.food_cell import food_cell_bp
from app.food_cell.services import generate_and_forward_do_intimation
from app.models.billing import Sample
from app.models.food_cell import DoIntimation


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
    with open(html_path, "r", encoding="utf-8") as fh:
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
    return jsonify(
        {
            "intimation_id": intimation.id,
            "do_reference_no": intimation.do_reference_no,
            "status": intimation.status,
            "pdf_url": intimation.pdf_url,
            "sync_status": intimation.sync_status,
            "food_cell_forwarded": (
                intimation.food_cell_forwarded.isoformat()
                if intimation.food_cell_forwarded
                else None
            ),
        }
    ), 200


@food_cell_bp.route("/do-intimation/<int:sample_id>/status")
@login_required
def do_intimation_status(sample_id: int):
    """Return JSON status of the DO intimation for *sample_id*."""
    intimation = DoIntimation.query.filter_by(sample_id=sample_id).first()
    if intimation is None:
        return jsonify({"exists": False}), 200
    return (
        jsonify(
            {
                "exists": True,
                "intimation_id": intimation.id,
                "do_reference_no": intimation.do_reference_no,
                "status": intimation.status,
                "food_cell_forwarded": (
                    intimation.food_cell_forwarded.isoformat()
                    if intimation.food_cell_forwarded
                    else None
                ),
                "sync_status": intimation.sync_status,
                "has_html": bool(intimation.html_path),
                "has_pdf": bool(intimation.pdf_url),
            }
        ),
        200,
    )

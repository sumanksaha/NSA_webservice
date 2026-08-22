"""Autopopulation routes (Phase C)."""

from flask import jsonify

from app.autopopulation import autopopulation_bp
from app.autopopulation.service import draft_fbo_issue_for_sample, prefill


@autopopulation_bp.route("/prefill/<int:sample_id>")
def prefill_for_sample(sample_id: int):
    """Per-consumer prefill bundles built from the verified OCR record.

    Consumers (case-file / adjudication / bill forms) fetch this once and
    populate their fields without manual re-entry.
    """
    result = prefill(sample_id)
    if result is None:
        return jsonify({"error": f"Sample {sample_id} not found"}), 404
    return jsonify(result)


@autopopulation_bp.route("/draft-fbo-issue/<int:sample_id>", methods=["POST"])
def draft_issue(sample_id: int):
    """Auto-draft an FBO issue for a non-conforming lab report (idempotent)."""

    if sample_exists(sample_id) is False:
        return jsonify({"error": f"Sample {sample_id} not found"}), 404

    issue = draft_fbo_issue_for_sample(sample_id)
    if issue is None:
        return jsonify({
            "status": "conforming",
            "message": "All lab parameters match their standards — no issue drafted.",
        })
    return jsonify({
        "status": "drafted" if _was_created(issue, sample_id) else "existing",
        "issue_id": issue.id,
        "state": issue.state,
        "source_type": issue.source_type,
        "fbo_name": issue.fbo_name,
    })


def sample_exists(sample_id: int) -> bool | None:
    from app.extensions import db
    from app.models import Sample

    return db.session.get(Sample, sample_id) is not None


def _was_created(issue, sample_id: int) -> bool:
    import json

    try:
        detail = json.loads(issue.detail_json or "{}")
    except (TypeError, ValueError):
        return False
    # A freshly drafted issue has exactly this detail shape; an existing one
    # is indistinguishable — report "drafted" when the id was just allocated.
    return detail.get("sample_id") == sample_id

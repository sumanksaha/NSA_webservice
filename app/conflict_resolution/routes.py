"""Conflict-resolution queue routes (Phase B)."""

from __future__ import annotations

import json

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.conflict_resolution import conflict_resolution_bp
from app.ocr_extraction.service import open_conflicts, resolve_conflict


def _parse_values(raw: str) -> list[dict]:
    try:
        return json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []


@conflict_resolution_bp.route("/")
def queue():
    """Unresolved conflicts with their competing values."""
    conflicts = []
    for c in open_conflicts():
        conflicts.append({
            "id": c.id,
            "field_name": c.field_name,
            "ocr_document_id": c.ocr_document_id,
            "sample_id": c.sample_id,
            "created_at": c.created_at,
            "values": _parse_values(c.values_json),
        })
    return render_template("conflict_resolution/queue.html", conflicts=conflicts)


@conflict_resolution_bp.route("/<int:conflict_id>/resolve", methods=["POST"])
def resolve(conflict_id: int):
    """Pick the authoritative value for a conflict.

    Accepts ``resolved_value`` as form field or JSON; the chosen value is
    applied through the review workflow (OCRCorrection + extracted_json update).
    """
    resolved_value = request.form.get("resolved_value") or (request.get_json(silent=True) or {}).get("resolved_value", "")
    if not resolved_value:
        return jsonify({"error": "resolved_value is required"}), 400

    try:
        result = resolve_conflict(conflict_id, str(resolved_value))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    if request.is_json:
        return jsonify({
            "status": "resolved",
            "applied_count": result.applied_count,
            "conflicts_opened": result.conflicts_opened,
        })
    flash(f"Conflict #{conflict_id} resolved to “{resolved_value}”.")
    return redirect(url_for("conflict_resolution.queue"))

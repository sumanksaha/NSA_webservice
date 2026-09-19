"""HTTP + inspection-lifecycle routes for the FBO auditor agent.

* ``POST /auditor/plan`` — JSON API: FBOAuditContext → CAPA plan.
* ``POST /inspection/<id>/auditor-plan`` — trigger from a recorded
  inspection (violations via :func:`derive_violations`); persists the plan
  on ``Inspection.auditor_plan_json``. Lives on ``inspection_bp`` with the
  rest of the inspection lifecycle.
* ``POST /inspection/<id>/verify-closure`` — FSO asserts the auditor
  dossier verified → "Corrective Measures Implemented" terminal state.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.auditor import auditor_bp
from app.extensions import db
from app.inspection import inspection_bp
from app.models import Inspection
from app.shared.config import cfg
from app.shared.context_derivers import derive_violations

logger = logging.getLogger(__name__)


def _auditor_enabled() -> bool:
    """Whether the auditor agent may spend LLM budget."""
    return bool(cfg.auditor_enabled)


def _run_plan(fbo_context: dict[str, Any]) -> dict[str, Any]:
    """Ground + synthesize a CAPA plan (best-effort RAG, fail-closed LLM)."""
    from app.auditor import search_adapter
    from app.auditor.service import FBOAuditorAgent

    shortcomings = fbo_context.get("shortcomings", [])
    queries = [str(s.get("item", "")) for s in shortcomings if isinstance(s, dict) and s.get("item")]
    evidence = search_adapter.search_regulations(" ".join(queries), top_k=3) if queries else []
    return FBOAuditorAgent().generate_remediation_plan(
        fbo_context=fbo_context,
        regulatory_evidence=evidence,
    )


@auditor_bp.route("/plan", methods=["POST"])
def generate_audit_plan():
    """Generate a CAPA remediation plan from shortcomings (JSON API)."""
    if not _auditor_enabled():
        return jsonify({"error": "Auditor AI is disabled."}), 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    if not data.get("shortcomings"):
        return jsonify({"error": "Missing shortcomings list."}), 400
    try:
        plan = _run_plan(data)
    except RuntimeError as exc:
        logger.warning("auditor plan degraded: %s", exc)
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        logger.error("auditor plan failed: %s", exc)
        return jsonify({"error": f"Auditor plan failed: {exc}"}), 500
    return jsonify({"status": "success", "plan": plan}), 200


@inspection_bp.route("/<int:inspection_id>/auditor-plan", methods=["GET"])
def view_auditor_plan(inspection_id: int):
    """Render the persisted CAPA plan for an inspection (404 when none yet)."""
    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        return jsonify({"error": f"Inspection {inspection_id} not found."}), 404
    if not inspection.auditor_plan_json:
        return jsonify({"error": "No auditor plan generated yet for this inspection."}), 404
    try:
        plan = json.loads(inspection.auditor_plan_json)
    except (ValueError, TypeError):
        return jsonify({"error": "Stored auditor plan is corrupt."}), 500
    return render_template(
        "inspection/auditor_plan_view.html",
        inspection=inspection,
        plan=plan,
    )


@inspection_bp.route("/<int:inspection_id>/auditor-plan", methods=["POST"])
def generate_auditor_plan(inspection_id: int):
    """Trigger CAPA generation from a recorded inspection's checklist."""
    from app.auditor.context import build_fbo_context

    if not _auditor_enabled():
        return jsonify({"error": "Auditor AI is disabled."}), 503
    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        return jsonify({"error": f"Inspection {inspection_id} not found."}), 404
    try:
        checklist = json.loads(inspection.checklist_json) if inspection.checklist_json else {}
    except (ValueError, TypeError):
        checklist = {}
    violations = derive_violations(checklist if isinstance(checklist, dict) else {})
    if not violations:
        return jsonify({"status": "no_violations", "plan": None}), 200
    fbo_context = build_fbo_context(
        fbo_name=inspection.fbo_name,
        business_type=inspection.concerned_food or "General Food Establishment",
        violations=violations,
    )
    try:
        plan = _run_plan(fbo_context)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        logger.error("auditor trigger failed: %s", exc)
        return jsonify({"error": f"Auditor plan failed: {exc}"}), 500
    inspection.auditor_plan_json = json.dumps(plan)
    db.session.commit()
    return jsonify({"status": "success", "plan": plan}), 200


@inspection_bp.route("/<int:inspection_id>/verify-closure", methods=["POST"])
def verify_closure(inspection_id: int):
    """FSO asserts the auditor dossier verified → Corrective Measures Implemented."""
    from flask_login import current_user

    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        return jsonify({"error": f"Inspection {inspection_id} not found."}), 404
    actor = getattr(current_user, "username", None) or getattr(current_user, "fso_name", None) or "fso"
    inspection.is_dismissed = True
    inspection.dismissed_by = actor
    inspection.dismissed_at = datetime.now(UTC)
    inspection.dossier_verified = True
    db.session.commit()
    flash(
        f"Inspection {inspection.inspection_code} closed: Corrective Measures Implemented (auditor dossier verified).",
        "success",
    )
    return redirect(url_for("inspection.open_issues"))

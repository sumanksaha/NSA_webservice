"""Timeline routes (plan.md Phase 13).

Serves the interactive timeline page, the JSON payload consumed by the
frontend, and a manual refresh endpoint.  Case IDs are disambiguated via
:class:`CaseResolver` so the same endpoints work for CaseFile and
Adjudication records (``?kind=case_file|adjudication``).
"""

from __future__ import annotations

from flask import abort, jsonify, render_template, request, url_for

from app.shared.case_resolver import CaseResolver
from app.timeline import timeline_bp
from app.timeline.engine import TimelineEngine

engine = TimelineEngine()


def _resolve(case_id_or_adjudication_id: int):
    """Resolve the path ID to a case, honouring the optional ``?kind=`` param."""
    return CaseResolver().resolve(
        case_id_or_adjudication_id,
        kind=request.args.get("kind"),
    )


@timeline_bp.route("/case/<int:case_id_or_adjudication_id>")
def view(case_id_or_adjudication_id: int):
    """Render the interactive timeline + Gantt page for a case."""
    resolved = _resolve(case_id_or_adjudication_id)
    if resolved is None:
        abort(404)

    return render_template(
        "timeline/index.html",
        case_number=resolved.case_number,
        case_id=resolved.case_id,
        adjudication_id=resolved.adjudication_id,
        case_type=resolved.case_type,
        api_url=url_for(
            "timeline.api",
            case_id_or_adjudication_id=case_id_or_adjudication_id,
        ),
        refresh_url=url_for(
            "timeline.refresh",
            case_id_or_adjudication_id=case_id_or_adjudication_id,
        ),
    )


@timeline_bp.route("/api/case/<int:case_id_or_adjudication_id>")
def api(case_id_or_adjudication_id: int):
    """Return the timeline JSON payload (events + warnings) for a case.

    For case_file cases the events are (re)persisted to ``timeline_event``
    on every call, keeping the table in sync with the record dates.
    """
    resolved = _resolve(case_id_or_adjudication_id)
    if resolved is None:
        return jsonify({"error": "Case not found"}), 404

    payload = engine.build_payload(resolved)
    if "error" in payload:
        return jsonify(payload), 404
    return jsonify(payload)


@timeline_bp.route("/api/case/<int:case_id_or_adjudication_id>/refresh", methods=["POST"])
def refresh(case_id_or_adjudication_id: int):
    """Regenerate + persist the timeline for a case (case_file only)."""
    resolved = _resolve(case_id_or_adjudication_id)
    if resolved is None:
        return jsonify({"error": "Case not found"}), 404

    count = engine.refresh(resolved)
    return jsonify(
        {
            "status": "ok",
            "case_type": resolved.case_type,
            "persisted": count,
            "message": (
                f"Timeline regenerated ({count} events persisted)."
                if resolved.case_type == "case_file"
                else "Timeline regenerated (adjudication timelines are computed, not stored)."
            ),
        }
    )

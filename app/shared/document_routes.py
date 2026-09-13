"""Common CRUD + document route registration (split from DocumentCaseManager).

:func:`register_document_routes` wires the shared endpoints onto a
blueprint. It depends only on the model, ``case_type``/``bp_name`` strings,
and the lookup/report helpers — never on the manager class.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, url_for

from app.shared import document_lookup as lookup
from app.shared import document_reports as reports


def register_document_routes(
    bp: Blueprint,
    model: type,
    case_type: str,
    bp_name: str,
    template_dir: str,
    model_to_dict_fn,
) -> None:
    """Register the common CRUD + document routes on *bp*."""

    @bp.route("/")
    def index():
        # Recent cases only — the index page is the landing view for both
        # blueprints, so keep the 'Case Timelines' panel cheap instead of
        # scanning the whole table on every load.
        from flask_login import current_user

        from app.shared.rbac import scoped_officer_name

        query = model.query
        scope = scoped_officer_name(current_user)
        if scope is not None:
            query = query.filter(lookup.officer_column(model, case_type) == scope)
        recent_cases = query.order_by(model.created_at.desc()).limit(50).all()
        return render_template(
            f"{template_dir}/index.html",
            cases=[lookup.case_summary(case_type, c) for c in recent_cases],
            case_type=case_type,
        )

    @bp.route("/cases", methods=["GET"])
    def list_cases():
        from flask_login import current_user

        from app.shared.rbac import scoped_officer_name

        query = model.query
        scope = scoped_officer_name(current_user)
        if scope is not None:
            query = query.filter(lookup.officer_column(model, case_type) == scope)
        cases = query.order_by(model.created_at.desc()).all()
        return jsonify([lookup.case_summary(case_type, c) for c in cases])

    @bp.route("/case/<int:case_id>", methods=["GET"])
    def get_case(case_id):
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        return jsonify(model_to_dict_fn(case))

    @bp.route("/case/by_number/<case_number>", methods=["GET"])
    def get_case_by_number(case_number):
        case = lookup.get_case_by_number(model, case_number)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with number {case_number} not found"}), 404
        return jsonify(model_to_dict_fn(case))

    @bp.route("/<int:case_id>/editor", methods=["GET"])
    def edit_case(case_id):
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        return reports.render_editor(model, case_type, bp_name, case_id)

    @bp.route("/<int:case_id>/xref_report", methods=["GET"])
    def xref_report(case_id):
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        doc_type = request.args.get("doc_type", "petition")
        annotated_html = reports.render_document_for_report(case_type, case_id, doc_type)
        report = reports.xref_report_data(case_type, annotated_html, case_id)
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

    @bp.route("/<int:case_id>/toc_report", methods=["GET"])
    def toc_report(case_id):
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        doc_type = request.args.get("doc_type", "petition")
        annotated_html = reports.render_document_for_report(case_type, case_id, doc_type)
        toc_data, toc_html = reports.toc_report_data(annotated_html)
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

    @bp.route("/<int:case_id>/renumber_annexures", methods=["POST"])
    def renumber_annexures(case_id):
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        result = reports.renumber_annexures(case_type, case_id)
        return jsonify({"status": "ok", "updates": result["updates"], "count": result["count"]})



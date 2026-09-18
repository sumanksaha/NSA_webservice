"""Common CRUD + document route registration (split from DocumentCaseManager).

:func:`register_document_routes` wires the shared endpoints onto a
blueprint. It depends only on the model, ``case_type``/``bp_name`` strings,
and the lookup/report helpers — never on the manager class.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, render_template, request, url_for
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.services.audit_context import audit_logger
from app.shared import document_lookup as lookup
from app.shared import document_reports as reports


def _audit_entity(case_type: str) -> str:
    return "adjudication_order" if case_type == "adjudication" else "case_file"


def _audit_action(case_type: str, verb: str) -> str:
    prefix = "ADJUDICATION_ORDER" if case_type == "adjudication" else "CASE_FILE"
    return f"{prefix}_{verb}"


def _sheet_cell(value):
    """Normalise a model value for a Sheets row cell."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _sync_record_to_sheets(case_type: str, sheets_module: str | None, model_to_dict_fn, case) -> None:
    """Best-effort Sheets resync of an edited/archived record (warn-only)."""
    if not sheets_module:
        return
    try:
        from app.services.sheets_sync import SHEET_COLUMNS
        from app.services.sync_orchestrator import sync_row
    except ImportError:
        return
    try:
        record = model_to_dict_fn(case)
        cols = SHEET_COLUMNS.get(sheets_module, [])
        row_dict = {c: _sheet_cell(record.get(c)) for c in cols if c in record}
        sync_row(sheets_module, row_dict, entity_id=case.id)
    except Exception as exc:  # Sheets outages must not lose the local edit
        current_app.logger.warning(f"{case_type} {case.id}: Sheets resync failed: {exc}")


def register_document_routes(
    bp: Blueprint,
    model: type,
    case_type: str,
    bp_name: str,
    template_dir: str,
    model_to_dict_fn,
    *,
    validate_form_fn=None,
    apply_update_fn=None,
    form_dict_fn=None,
    sheets_module: str | None = None,
) -> None:
    """Register the common CRUD + document routes on *bp*."""

    @bp.route("/")
    def index():
        # Recent cases only — the index page is the landing view for both
        # blueprints, so keep the 'Case Timelines' panel cheap instead of
        # scanning the whole table on every load. Archived cases are hidden
        # unless ?include_archived=1.
        from flask_login import current_user

        from app.shared.rbac import scoped_officer_name

        include_archived = request.args.get("include_archived") == "1"
        query = model.query
        scope = scoped_officer_name(current_user)
        if scope is not None:
            query = query.filter(lookup.officer_column(model, case_type) == scope)
        query = lookup.apply_archive_filter(query, model, include_archived)
        recent_cases = query.order_by(model.created_at.desc()).limit(50).all()
        return render_template(
            f"{template_dir}/index.html",
            cases=[lookup.case_summary(case_type, c) for c in recent_cases],
            case_type=case_type,
            show_archived=include_archived,
        )

    @bp.route("/cases", methods=["GET"])
    def list_cases():
        from flask_login import current_user

        from app.shared.rbac import scoped_officer_name

        include_archived = request.args.get("include_archived") == "1"
        query = model.query
        scope = scoped_officer_name(current_user)
        if scope is not None:
            query = query.filter(lookup.officer_column(model, case_type) == scope)
        query = lookup.apply_archive_filter(query, model, include_archived)
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

    # ------------------------------------------------------------------ #
    # Field edit (prefilled form) + archive / unarchive (soft-delete)
    # ------------------------------------------------------------------ #

    @bp.route("/case/<int:case_id>/edit", methods=["GET"])
    def edit_form(case_id):
        """Render the prefilled edit form for a case."""
        if form_dict_fn is None:
            return jsonify({"error": "Editing is not supported for this case type"}), 404
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        return render_template(
            f"{template_dir}/edit.html",
            form=form_dict_fn(case),
            case_id=case.id,
            case_number=lookup.get_case_number(case),
            is_archived=bool(getattr(case, "is_archived", False)),
            case_type=case_type,
        )

    @bp.route("/case/<int:case_id>", methods=["PUT"])
    def update_case(case_id):
        """Update a case's entered data (full-form payload; case_number immutable)."""
        if validate_form_fn is None or apply_update_fn is None:
            return jsonify({"error": "Editing is not supported for this case type"}), 404
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        if bool(getattr(case, "is_archived", False)):
            return jsonify({"error": "Case is archived. Unarchive it before editing."}), 409

        data = request.form.to_dict() if request.form else request.get_json(silent=True) or {}

        # case_number is the immutable identity — never updated.
        if "case_number" in data and (data.get("case_number") or "").strip() != case.case_number:
            return jsonify({"error": "case_number cannot be changed."}), 400

        # Phase 18 RBAC: a scoped officer always owns what they touch.
        from flask_login import current_user

        from app.shared.rbac import scoped_officer_name

        scope = scoped_officer_name(current_user)
        if scope:
            data["food_safety_officer_name"] = scope

        errors = validate_form_fn(data)
        if errors:
            return jsonify({"error": "Please correct the highlighted fields below.", "errors": errors}), 400

        apply_update_fn(case, data)
        # Mark Supabase-dirty so the next push() upserts the new state
        # (is_archived included automatically via _model_to_payload).
        case.synced_at = None
        try:
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
            return jsonify({
                "error": "Conflict: this case was modified by another user. Please reload and try again."
            }), 409
        except Exception as exc:
            db.session.rollback()
            return jsonify({"error": f"Failed to update case: {exc!s}"}), 500

        _sync_record_to_sheets(case_type, sheets_module, model_to_dict_fn, case)
        try:
            audit_logger(_audit_entity(case_type)).log(str(case_id), _audit_action(case_type, "UPDATED"))
        except Exception:
            current_app.logger.warning(f"{case_type} {case_id}: audit log write failed", exc_info=True)
        return jsonify(model_to_dict_fn(case)), 200

    @bp.route("/case/<int:case_id>/archive", methods=["POST"])
    def archive_case(case_id):
        """Archive a case: hides it from UI lists, keeps all data (incl. Supabase)."""
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        if bool(getattr(case, "is_archived", False)):
            return jsonify({"message": "Case is already archived.", "is_archived": True}), 200
        case.is_archived = True
        case.archived_at = datetime.now(UTC)
        case.synced_at = None
        try:
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
            return jsonify({
                "error": "Conflict: this case was modified by another user. Please reload and try again."
            }), 409
        except Exception as exc:
            db.session.rollback()
            return jsonify({"error": f"Failed to archive case: {exc!s}"}), 500

        _sync_record_to_sheets(case_type, sheets_module, model_to_dict_fn, case)
        try:
            audit_logger(_audit_entity(case_type)).log(str(case_id), _audit_action(case_type, "ARCHIVED"))
        except Exception:
            current_app.logger.warning(f"{case_type} {case_id}: audit log write failed", exc_info=True)
        return jsonify({"message": "Case archived.", "is_archived": True}), 200

    @bp.route("/case/<int:case_id>/unarchive", methods=["POST"])
    def unarchive_case(case_id):
        """Restore an archived case back to the UI lists."""
        case = lookup.get_case(model, case_id)
        if case is None or not lookup.visible_to_current_user(model, case_type, case):
            return jsonify({"error": f"Case with id {case_id} not found"}), 404
        if not bool(getattr(case, "is_archived", False)):
            return jsonify({"message": "Case is not archived.", "is_archived": False}), 200
        case.is_archived = False
        case.archived_at = None
        case.synced_at = None
        try:
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
            return jsonify({
                "error": "Conflict: this case was modified by another user. Please reload and try again."
            }), 409
        except Exception as exc:
            db.session.rollback()
            return jsonify({"error": f"Failed to unarchive case: {exc!s}"}), 500

        _sync_record_to_sheets(case_type, sheets_module, model_to_dict_fn, case)
        try:
            audit_logger(_audit_entity(case_type)).log(str(case_id), _audit_action(case_type, "UNARCHIVED"))
        except Exception:
            current_app.logger.warning(f"{case_type} {case_id}: audit log write failed", exc_info=True)
        return jsonify({"message": "Case restored.", "is_archived": False}), 200



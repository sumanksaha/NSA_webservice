"""Document case management — shared abstraction for CaseFile and Adjudication.

Consolidates the near-duplicate route logic that previously lived in
``app/case_file_generator/routes.py`` (697 lines) and
``app/adjudication/routes.py`` (820 lines).

Each concrete module instantiates :class:`DocumentCaseManager` with
model-specific callbacks (``model_to_dict_fn``, ``process_form_fn``,
``prepare_context_fn``) and registers the common routes.  Module-specific
endpoints (``lookup_sample``, ``suggest_sections``, etc.) remain in the
thin route files alongside the manager instance.

Canonical interface:

    .. code-block:: python

        class DocumentCaseManager:
            def register_routes(self, bp): ...
            def get_case(self, case_id) -> model | None
            def get_case_by_number(self, case_number) -> model | None
            def list_cases(self) -> list[dict]
            def render_editor(self, case_id) -> str
            def xref_report(self, case_id, doc_type) -> str
            def toc_report(self, case_id, doc_type) -> str
            def renumber_annexures(self, case_id) -> dict
            def regenerate(self, case_id, **kwargs) -> PDFResult
            def generate_case(self, form_data) -> (dict, int)
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import or_
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import Evidence
from app.services.audit import log_audit
from app.services.sheets_sync import sync_to_sheets
from app.utils.pdf_utils import embed_photos_as_base64, generate_pdf_from_html, post_process_pdf_html
from app.utils.qstash_client import make_dedup_key, publish_task

logger = logging.getLogger(__name__)

# Type aliases for injected callbacks
ModelToDictFn = Callable[[Any], dict]
ProcessFormFn = Callable[[dict], Any]  # model instance
PrepareContextFn = Callable[[dict], dict]
ValidateFormFn = Callable[[dict], dict[str, str]]


class PDFResult:
    """Result of a document generation / regeneration operation."""

    def __init__(self, pdf_bytes: bytes | None, error: str | None = None) -> None:
        self.pdf_bytes = pdf_bytes
        self.error = error

    @property
    def success(self) -> bool:
        return self.pdf_bytes is not None


class DocumentCaseManager:
    """Parameterized manager for CaseFile / Adjudication CRUD + document generation.

    Args:
        model: SQLAlchemy model class (``CaseFile`` or ``Adjudication``).
        template_dir: Blueprint template folder name
            (``"case_file_generator"`` or ``"adjudication"``).
        bp_name: Flask endpoint-name prefix (``"case_file_generator"`` or
            ``"adjudication"``).
        case_type: ``"case_file"`` or ``"adjudication"`` — drives the
            ``case_id`` / ``adjudication_id`` keyword passed to PDF
            post-processing.
        model_to_dict_fn: Converts a model instance to a dict for JSON.
        process_form_fn: Converts validated form data to a model instance.
        prepare_context_fn: Converts form data dict to a template-render context.
        validate_form_fn: Validates form data, returning ``{field: error}``.
        templates: Dict mapping logical names to template paths.
    """

    def __init__(
        self,
        model: type,
        template_dir: str,
        bp_name: str,
        case_type: str,
        model_to_dict_fn: ModelToDictFn,
        process_form_fn: ProcessFormFn,
        validate_form_fn: ValidateFormFn | None = None,
        prepare_context_fn: PrepareContextFn | None = None,
        templates: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.template_dir = template_dir
        self.bp_name = bp_name
        self.case_type = case_type
        self.model_to_dict_fn = model_to_dict_fn
        self.process_form_fn = process_form_fn
        self.validate_form_fn = validate_form_fn
        self.prepare_context_fn = prepare_context_fn or (lambda ctx: ctx)
        self.templates = templates or {}

    # ------------------------------------------------------------------ #
    # Route registration
    # ------------------------------------------------------------------ #

    def register_routes(self, bp: Blueprint) -> None:
        """Register the common CRUD + document routes on *bp*."""

        @bp.route("/")
        def index():
            return render_template(f"{self.template_dir}/index.html")

        @bp.route("/cases", methods=["GET"])
        def list_cases():
            cases = self.model.query.order_by(self.model.created_at.desc()).all()
            return jsonify([self._case_summary(c) for c in cases])

        @bp.route("/case/<int:case_id>", methods=["GET"])
        def get_case(case_id):
            case = self.get_case(case_id)
            if case is None:
                return jsonify({"error": f"Case with id {case_id} not found"}), 404
            return jsonify(self.model_to_dict_fn(case))

        @bp.route("/case/by_number/<case_number>", methods=["GET"])
        def get_case_by_number(case_number):
            case = self.get_case_by_number(case_number)
            if case is None:
                return jsonify({"error": f"Case with number {case_number} not found"}), 404
            return jsonify(self.model_to_dict_fn(case))

        @bp.route("/<int:case_id>/editor", methods=["GET"])
        def edit_case(case_id):
            case = self.get_case(case_id)
            if case is None:
                return jsonify({"error": f"Case with id {case_id} not found"}), 404
            return self.render_editor(case_id)

        @bp.route("/<int:case_id>/xref_report", methods=["GET"])
        def xref_report(case_id):
            case = self.get_case(case_id)
            if case is None:
                return jsonify({"error": f"Case with id {case_id} not found"}), 404
            doc_type = request.args.get("doc_type", "petition")
            annotated_html = self._render_document(case_id, doc_type)
            report = self._generate_xref_report(annotated_html, case_id)
            return render_template(
                "xref_report.html",
                case_number=self._get_case_number(case),
                fbo_name=self._get_fbo_name(case),
                food_safety_officer=self._get_fso(case),
                doc_type=doc_type,
                report=report,
                annotated_html=annotated_html,
                report_url=url_for(f"{self.bp_name}.xref_report", case_id=case_id),
                renumber_url=url_for(f"{self.bp_name}.renumber_annexures", case_id=case_id),
            )

        @bp.route("/<int:case_id>/toc_report", methods=["GET"])
        def toc_report(case_id):
            case = self.get_case(case_id)
            if case is None:
                return jsonify({"error": f"Case with id {case_id} not found"}), 404
            doc_type = request.args.get("doc_type", "petition")
            annotated_html = self._render_document(case_id, doc_type)
            from app.toc_generator import generate_toc_data
            from app.toc_generator.engine import TocGeneratorEngine

            toc_data = generate_toc_data(annotated_html)
            toc_html = TocGeneratorEngine().build_toc_html(
                TocGeneratorEngine().extract_toc(annotated_html)
            )
            return render_template(
                "toc_report.html",
                case_number=self._get_case_number(case),
                fbo_name=self._get_fbo_name(case),
                food_safety_officer=self._get_fso(case),
                doc_type=doc_type,
                toc_data=toc_data,
                toc_html=toc_html,
                annotated_html=annotated_html,
                toc_url=url_for(f"{self.bp_name}.toc_report", case_id=case_id),
            )

        @bp.route("/<int:case_id>/renumber_annexures", methods=["POST"])
        def renumber_annexures(case_id):
            from app.cross_reference.engine import CrossReferenceEngine

            kwargs = self._case_kwarg(case_id)
            updates = CrossReferenceEngine().renumber_annexures(**kwargs)
            return jsonify({"status": "ok", "updates": updates, "count": len(updates)})

    # ------------------------------------------------------------------ #
    # Lookup helpers
    # ------------------------------------------------------------------ #

    def get_case(self, case_id: int) -> Any | None:
        """Retrieve a case by primary key."""
        return db.session.get(self.model, case_id)

    def get_case_by_number(self, case_number: str) -> Any | None:
        """Retrieve a case by case number."""
        return self.model.query.filter_by(case_number=case_number).first()

    def list_cases(self) -> list[dict]:
        """Return all cases as summary dicts."""
        cases = self.model.query.order_by(self.model.created_at.desc()).all()
        return [self._case_summary(c) for c in cases]

    # ------------------------------------------------------------------ #
    # Document generation / regeneration
    # ------------------------------------------------------------------ #

    def render_editor(self, case_id: int) -> str:
        """Render the Quill editor page pre-filled with a case's documents."""
        case = self.get_case(case_id)
        if case is None:
            return ""
        from app.document_viewer.renderer import (
            render_adjudication_document,
            render_case_file_document,
        )

        render_fn = (
            render_case_file_document
            if self.case_type == "case_file"
            else render_adjudication_document
        )
        return render_template(
            "document_viewer/editor.html",
            case_number=case.case_number,
            case_id=case.id,
            case_type=self.case_type,
            petition_html=render_fn(case_id, "petition"),
            permission_html=render_fn(case_id, "permission"),
            report_url=url_for(f"{self.bp_name}.xref_report", case_id=case_id),
            toc_url=url_for(f"{self.bp_name}.toc_report", case_id=case_id),
        )

    def xref_report(self, case_id: int, doc_type: str = "petition") -> str:
        """Render the cross-reference report for a case."""
        case = self.get_case(case_id)
        if case is None:
            return ""
        annotated_html = self._render_document(case_id, doc_type)
        report = self._generate_xref_report(annotated_html, case_id)
        return render_template(
            "xref_report.html",
            case_number=self._get_case_number(case),
            fbo_name=self._get_fbo_name(case),
            food_safety_officer=self._get_fso(case),
            doc_type=doc_type,
            report=report,
            annotated_html=annotated_html,
            report_url=url_for(f"{self.bp_name}.xref_report", case_id=case_id),
            renumber_url=url_for(f"{self.bp_name}.renumber_annexures", case_id=case_id),
        )

    def toc_report(self, case_id: int, doc_type: str = "petition") -> str:
        """Render the table-of-contents report for a case."""
        case = self.get_case(case_id)
        if case is None:
            return ""
        annotated_html = self._render_document(case_id, doc_type)
        from app.toc_generator import generate_toc_data
        from app.toc_generator.engine import TocGeneratorEngine

        toc_data = generate_toc_data(annotated_html)
        toc_html = TocGeneratorEngine().build_toc_html(
            TocGeneratorEngine().extract_toc(annotated_html)
        )
        return render_template(
            "toc_report.html",
            case_number=self._get_case_number(case),
            fbo_name=self._get_fbo_name(case),
            food_safety_officer=self._get_fso(case),
            doc_type=doc_type,
            toc_data=toc_data,
            toc_html=toc_html,
            annotated_html=annotated_html,
            toc_url=url_for(f"{self.bp_name}.toc_report", case_id=case_id),
        )

    def renumber_annexures(self, case_id: int) -> dict:
        """Renumber annexure letters in upload order."""
        from app.cross_reference.engine import CrossReferenceEngine

        kwargs = self._case_kwarg(case_id)
        updates = CrossReferenceEngine().renumber_annexures(**kwargs)
        return {"status": "ok", "updates": updates, "count": len(updates)}

    def _render_document(self, case_id: int, doc_type: str) -> str:
        """Render a document via the appropriate renderer function."""
        from app.document_viewer.renderer import (
            render_adjudication_document,
            render_case_file_document,
        )

        render_fn = (
            render_case_file_document
            if self.case_type == "case_file"
            else render_adjudication_document
        )
        return render_fn(case_id, doc_type)

    def _generate_xref_report(self, annotated_html: str, case_id: int) -> dict:
        from app.cross_reference import generate_xref_report_data

        kwarg = self._case_kwarg(case_id)
        return generate_xref_report_data(annotated_html, **kwarg)

    def _case_kwarg(self, case_id: int) -> dict:
        """Return the case-type-specific kwarg for CrossReference/Toc API."""
        if self.case_type == "case_file":
            return {"case_id": case_id, "adjudication_id": None}
        return {"adjudication_id": case_id, "case_id": None}

    # ------------------------------------------------------------------ #
    # Regeneration (shared skeleton, model-specific context prep)
    # ------------------------------------------------------------------ #

    def regenerate(
        self, case_id: int, context_overrides: dict | None = None
    ) -> Any:
        """Regenerate documents from an existing case.

        Delegates context preparation to ``prepare_context_fn`` (injected
        at construction). Returns a Flask ``send_file`` response (ZIP in
        memory) or a JSON error response.

        For CaseFile, the ``templates`` dict should map
        ``"petition"`` and ``"permission"`` to template paths and
        ``case_data`` should be pre-built by ``process_form_fn``.
        """
        case = self.get_case(case_id)
        if case is None:
            return jsonify({"error": f"Case with id {case_id} not found"}), 404

        form_data = self.model_to_dict_fn(case)
        case_data = self.process_form_fn(form_data) if self.process_form_fn else form_data

        context = self.prepare_context_fn(case_data) if self.prepare_context_fn else case_data
        if context_overrides:
            context.update(context_overrides)

        context["compilation_date"] = datetime.today().strftime("%d %B %Y")

        # --- Photo evidence integration ---
        context["adjudication"] = self._build_photos_context(case_id, context)
        self._log_generation(case_id, form_data)

        templates_to_generate = self._get_templates_to_generate(context)
        if isinstance(templates_to_generate, tuple):
            return templates_to_generate

        outputs: list[tuple[str, bytes]] = []
        for tpl, prefix in templates_to_generate:
            rendered_html = render_template(tpl, **context)
            rendered_html = post_process_pdf_html(
                rendered_html, case_id=case_id if self.case_type == "case_file" else None,
                adjudication_id=None if self.case_type == "case_file" else case_id,
            )
            pdf_bytes, error = generate_pdf_from_html(rendered_html)
            if pdf_bytes:
                outputs.append((f"{prefix}.pdf", pdf_bytes))
            else:
                current_app.logger.error(f"PDF generation failed for {tpl}: {error}")
                return (
                    jsonify(
                        {
                            "error": f"PDF generation failed: {error}. "
                            "Documents cannot be generated without WeasyPrint.",
                        }
                    ),
                    500,
                )

        return self._build_zip_response(outputs, case_id)

    def _build_photos_context(self, case_id: int, context: dict) -> dict:
        """Fetch photos and embed as base64 for template rendering."""
        all_photos = (
            Evidence.query.filter(
                Evidence.evidence_type == "photo",
                or_(Evidence.case_id == case_id, Evidence.adjudication_id == case_id),
            )
            .order_by(Evidence.captured_at.asc())
            .all()
        )

        include_flagged = request.args.get("include_flagged", "false").lower() == "true"
        flag_override_reason = request.args.get("flag_override_reason", "").strip()

        verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
        flagged_photos = [p for p in all_photos if p.verification_status == "FLAG"]

        if include_flagged:
            if not flag_override_reason:
                return jsonify(
                    {"error": "flag_override_reason is required when include_flagged=true"}
                ), 400
            final_photos = verified_photos + flagged_photos
            flagged_image_ids = [p.id for p in flagged_photos]
            if flagged_image_ids:
                log_audit(
                    "photo",
                    ",".join(flagged_image_ids),
                    "FLAGGED_PHOTO_INCLUDED",
                    actor=context.get("food_safety_officer_name", "unknown"),
                    details={"reason": flag_override_reason},
                )
        else:
            final_photos = verified_photos

        return {
            "photos": final_photos,
            "photo_embeds": embed_photos_as_base64(
                [p.filepath for p in final_photos]
            ),
        }

    def _get_templates_to_generate(self, context: dict) -> list[tuple[str, str]] | tuple:
        """Subclass hook — return list of (template, prefix) tuples.

        For case_file_generator the templates come from the ``templates``
        dict passed at construction.  Adjudication uses pre-auth vs
        non-pre-auth template selection.  Override by providing a
        ``templates_fn`` callback.
        """
        if self.case_type == "case_file":
            return [("case_file_generator/petition.html", "Petition"),
                    ("case_file_generator/permission_letter.html", "Permission_Letter")]
        # Adjudication
        is_pre_auth = str(context.get("pre_authorization", "no")).strip().lower() == "yes"
        if is_pre_auth:
            return [("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")]
        if not context.get("authorization_date"):
            return jsonify(
                {"error": "authorization_date is required for non-pre-authorization cases."}
            ), 400
        return [("adjudication/template_nonsample_petition.html", "Petition")]

    def _build_zip_response(self, outputs: list[tuple[str, bytes]], case_id: int) -> Any:
        """Build an in-memory ZIP response from generated PDFs."""
        zip_prefix = "Case" if self.case_type == "case_file" else "Petition"
        if self.case_type != "case_file":
            is_pre_auth = str(
                request.form.get("pre_authorization", "no")
            ).strip().lower() == "yes"
            zip_prefix = "PermissionLetter" if is_pre_auth else "Petition"

        case_number = outputs[0][0].replace(".pdf", "") if outputs else str(case_id)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
            for fname, data in outputs:
                z.writestr(fname, data)
        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"{zip_prefix}_Case_{case_number}_Regenerated.zip",
            mimetype="application/zip",
        )

    def _log_generation(self, case_id: int, form_data: dict) -> None:
        """Log audit entry for document generation."""
        image_ids = form_data.get("_photo_image_ids", [])
        statuses = form_data.get("_photo_statuses", [])
        if image_ids:
            log_audit(
                "adjudication_order" if self.case_type == "adjudication" else "case_file",
                str(case_id),
                "ADJUDICATION_ORDER_REGENERATED" if self.case_type == "adjudication"
                else "CASE_FILE_REGENERATED",
                actor=form_data.get("food_safety_officer_name", "unknown"),
                details={"image_ids": image_ids, "statuses": statuses},
            )

    # ------------------------------------------------------------------ #
    # Generation (new case creation + PDF dispatch)
    # ------------------------------------------------------------------ #

    def generate_case(self, form_data: dict) -> tuple[dict, int]:
        """Create a new case record from form data and dispatch PDF generation.

        For ``case_file`` type: uses QStash async PDF dispatch.
        For ``adjudication`` type: generates PDFs synchronously in-memory.

        Returns ``(metadata_dict, status_code)``.
        """
        if self.validate_form_fn:
            errors = self.validate_form_fn(form_data)
            if errors:
                return (
                    {"error": "Please correct the highlighted fields below.",
                     "errors": errors},
                    400,
                )

        try:
            record = self.process_form_fn(form_data)
            db.session.add(record)
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
            return (
                jsonify(
                    {"error": "This case was modified by another user. Please reload and try again."}
                ),
                409,
            )

        # --- Sheets sync (best-effort) ---
        self._sync_to_sheets(form_data, record)

        # --- Link to inspection (adjudication only) ---
        if self.case_type == "adjudication":
            from_inspection = form_data.get("from_inspection")
            if from_inspection:
                self._link_inspection(record, from_inspection)

        # --- PDF generation ---
        if self.case_type == "case_file":
            return self._dispatch_case_file_pdf(record, form_data)
        return self._generate_adjudication_pdfs(record, form_data)

    def _sync_to_sheets(self, form_data: dict, record: Any) -> None:
        """Best-effort Google Sheets sync."""
        allowed = self._sheets_columns()
        try:
            row_dict = {k: v for k, v in form_data.items() if k in allowed}
            row_dict["created_at"] = record.created_at.isoformat() if record.created_at else ""
            sync_to_sheets(self.case_type, row_dict)
        except Exception as exc:
            current_app.logger.warning(f"{self.case_type}: Sheets sync failed: {exc}")

    def _link_inspection(self, adj: Any, from_inspection: str) -> None:
        """Link adjudication back to an inspection (adjudication only)."""
        from app.models import Inspection

        try:
            inspection = db.session.get(Inspection, int(from_inspection))
            if inspection and not inspection.adjudication_id and not inspection.is_dismissed:
                today = datetime.now(UTC)
                if inspection.compliance_deadline and inspection.compliance_deadline < today:
                    inspection.adjudication_id = adj.id
                try:
                    db.session.commit()
                except StaleDataError:
                    db.session.rollback()
                    current_app.logger.warning(
                        f"Adjudication {adj.id}: StaleDataError linking inspection {from_inspection}"
                    )
        except Exception as exc:
            current_app.logger.warning(f"Adjudication: Failed to link inspection {from_inspection}: {exc}")
            db.session.rollback()

    def _dispatch_case_file_pdf(self, record: Any, form_data: dict) -> tuple[dict, int]:
        """Dispatch PDF generation via QStash (case_file only)."""
        case_data = record.__dict__ if hasattr(record, "__dict__") else form_data
        payload = {"case_file_id": record.id, "case_data": case_data}
        try:
            dispatched = publish_task(
                "generate_case_file_pdf",
                payload=payload,
                dedup_key=make_dedup_key("generate_case_file_pdf", record.id, payload),
            )
        except Exception as exc:
            current_app.logger.error("Case file PDF dispatch failed: %s", exc)
            return {"error": f"Case file PDF generation failed: {exc}"}, 500

        if dispatched["mode"] == "async":
            return (
                {"message": "Case file created; PDF generation queued",
                 "case_file_id": record.id,
                 "task_id": dispatched["message_id"]},
                202,
            )

        result = dispatched["result"]
        if result.get("status") == "error":
            error_msg = result.get("error", "PDF generation failed")
            current_app.logger.error("Case file PDF generation returned error: %s", error_msg)
            return {"error": error_msg}, 500

        return (
            {"message": "Case file created; PDF generated",
             "case_file_id": record.id,
             "pdf_result": result},
            200,
        )

    def _generate_adjudication_pdfs(self, adj: Any, form_data: dict) -> tuple[dict, int]:
        """Generate adjudication PDFs in-memory synchronously."""
        context = self.prepare_context_fn(form_data) if self.prepare_context_fn else form_data
        context["compilation_date"] = datetime.today().strftime("%d %B %Y")
        context["adjudication"] = self._build_photos_context(adj.id, context)
        self._log_generation(adj.id, form_data)

        templates = self._get_templates_to_generate(context)
        if isinstance(templates, tuple):
            return templates[0], templates[1]

        outputs: list[tuple[str, bytes]] = []
        for tpl, prefix in templates:
            rendered_html = render_template(tpl, **context)
            rendered_html = post_process_pdf_html(
                rendered_html, adjudication_id=adj.id
            )
            pdf_bytes, error = generate_pdf_from_html(rendered_html)
            if pdf_bytes:
                outputs.append((f"{prefix}.pdf", pdf_bytes))
            else:
                current_app.logger.error(f"PDF generation failed for {tpl}: {error}")
                return (
                    jsonify(
                        {"error": f"PDF generation failed: {error}. "
                                  "Documents cannot be generated without WeasyPrint."}
                    ),
                    500,
                )

        return self._build_zip_response(outputs, adj.id)

    # ------------------------------------------------------------------ #
    # Model-specific property accessors (override via subclasses or callbacks)
    # ------------------------------------------------------------------ #

    def _case_summary(self, case) -> dict:
        """Return a summary dict for list_cases — model-specific."""
        if self.case_type == "case_file":
            return {
                "id": case.id,
                "case_number": case.case_number,
                "product_name": case.product_name,
                "manufacturer_name": case.manufacturer_name,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
        return {
            "id": case.id,
            "case_number": case.case_number,
            "fbo_name": case.fbo_name,
            "food_safety_officer": case.food_safety_officer,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        }

    def _get_case_number(self, case) -> str:
        return case.case_number

    def _get_fbo_name(self, case) -> str:
        if self.case_type == "case_file":
            return case.manufacturer_name
        return case.fbo_name

    def _get_fso(self, case):
        if self.case_type == "case_file":
            return None
        return case.food_safety_officer

    def _sheets_columns(self) -> set[str]:
        """Return the set of column names eligible for Sheets sync."""
        if self.case_type == "case_file":
            return {
                "case_number", "food_safety_officer_name", "authorization_date",
                "inspection_date", "inspection_time", "sample_id",
                "manufacturer_fssai", "manufacturer_name", "manufacturer_fbo_name",
                "manufacturer_address", "retailer_fssai", "retailer_name",
                "retailer_fbo_name", "retailer_address", "product_name",
                "batch_no", "sample_quantity", "packet_count", "mfg_date",
                "expiry_date", "other_food_articles", "total_cost",
                "cost_in_words", "sample_code", "sample_submission_date",
                "Lab_Registration_No", "do_receipt_date", "is_misbranded",
                "is_substandard", "analyst_report_no", "analyst_report_date",
                "directive_letter_no", "directive_letter_date",
                "retailer_report_receive_date", "manufacturer_report_receive_date",
                "applicable_regulation", "applicable_clause", "sample_name",
                "applicable_sections",
            }
        return {
            "case_number", "food_safety_officer", "non_license",
            "pre_authorization", "complaint_lodged", "ce_license_no",
            "ce_trade_name", "ce_proprietor", "ce_address", "ce_status",
            "fbo_owner", "fbo_name", "fbo_address", "fssai_license",
            "concerned_food", "problem", "First_inspection_date",
            "compliance_deadline", "Complaint_date", "inspection_date",
            "authorization_date", "clean_premise", "refrigerator_clean",
            "proper_attire", "proper_covered_utensil", "date_tag",
            "veg_nonveg_separation", "food_segregation", "license_display",
            "artificial_colour", "Expired_item", "Pest_report", "Water_report",
            "section_55", "section_56", "section_58", "section_63", "section_64",
        }

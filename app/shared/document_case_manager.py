"""Document case management — shared abstraction for CaseFile and Adjudication.

Thin facade over the focused helpers in :mod:`app.shared.document_lookup`,
:mod:`app.shared.document_reports`, :mod:`app.shared.document_routes`, and
:mod:`app.shared.document_generation`.

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

import logging
from collections.abc import Callable
from typing import Any

from flask import Blueprint

from app.shared import document_generation as generation
from app.shared import document_lookup as lookup
from app.shared import document_reports as reports
from app.shared.document_routes import register_document_routes

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
        register_document_routes(
            bp,
            self.model,
            self.case_type,
            self.bp_name,
            self.template_dir,
            self.model_to_dict_fn,
        )

    # ------------------------------------------------------------------ #
    # Lookup helpers
    # ------------------------------------------------------------------ #

    def get_case(self, case_id: int) -> Any | None:
        return lookup.get_case(self.model, case_id)

    def get_case_by_number(self, case_number: str) -> Any | None:
        return lookup.get_case_by_number(self.model, case_number)

    def list_cases(self) -> list[dict]:
        cases = self.model.query.order_by(self.model.created_at.desc()).all()
        return [self._case_summary(c) for c in cases]

    # ------------------------------------------------------------------ #
    # Document generation / regeneration
    # ------------------------------------------------------------------ #

    def render_editor(self, case_id: int) -> str:
        """Render the Quill editor page pre-filled with a case's documents."""
        return reports.render_editor(self.model, self.case_type, self.bp_name, case_id)

    def xref_report(self, case_id: int, doc_type: str = "petition") -> str:
        """Render the cross-reference report for a case."""
        return reports.xref_report(self.model, self.case_type, self.bp_name, case_id, doc_type)

    def toc_report(self, case_id: int, doc_type: str = "petition") -> str:
        """Render the table-of-contents report for a case."""
        return reports.toc_report(self.model, self.case_type, self.bp_name, case_id, doc_type)

    def renumber_annexures(self, case_id: int) -> dict:
        """Renumber annexure letters in upload order."""
        return reports.renumber_annexures(self.case_type, case_id)

    def regenerate(self, case_id: int, context_overrides: dict | None = None) -> Any:
        """Regenerate documents from an existing case.

        Delegates context preparation to ``prepare_context_fn`` (injected
        at construction). Returns a Flask ``send_file`` response (ZIP in
        memory) or a JSON error response.

        For CaseFile, the ``templates`` dict should map
        ``"petition"`` and ``"permission"`` to template paths and
        ``case_data`` should be pre-built by ``process_form_fn``.
        """
        return generation.regenerate(
            self.model,
            self.case_type,
            self.model_to_dict_fn,
            self.process_form_fn,
            self.prepare_context_fn,
            case_id,
            context_overrides,
        )

    def generate_case(self, form_data: dict) -> tuple[dict, int]:
        """Create a new case record from form data and dispatch PDF generation.

        For ``case_file`` type: uses QStash async PDF dispatch.
        For ``adjudication`` type: generates PDFs synchronously in-memory.

        Returns ``(metadata_dict, status_code)``.
        """
        return generation.generate_case(
            self.model,
            self.case_type,
            self.validate_form_fn,
            self.process_form_fn,
            self.prepare_context_fn,
            form_data,
        )

    # ------------------------------------------------------------------ #
    # Model-specific property accessors (thin delegation to lookup helpers)
    # ------------------------------------------------------------------ #

    def _officer_column(self):
        """The model attribute holding the responsible officer's name."""
        return lookup.officer_column(self.model, self.case_type)

    def _visible_to_current_user(self, case) -> bool:
        """Phase 18 record-level scope: officers see only their own cases."""
        return lookup.visible_to_current_user(self.model, self.case_type, case)

    def _case_summary(self, case) -> dict:
        return lookup.case_summary(self.case_type, case)

    def _get_case_number(self, case) -> str:
        return lookup.get_case_number(case)

    def _get_fbo_name(self, case) -> str:
        return lookup.get_fbo_name(self.case_type, case)

    def _get_fso(self, case):
        return lookup.get_fso(self.case_type, case)

    def _sheets_columns(self) -> set[str]:
        """Return the set of column names eligible for Sheets sync."""
        return lookup.sheets_columns(self.case_type)

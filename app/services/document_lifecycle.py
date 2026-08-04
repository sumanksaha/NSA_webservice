"""Document lifecycle coordination service.

Extracted from ``app/document_viewer/routes.py`` where five private helpers
coupled the route layer to ``VersionService``, ``log_audit``, and
``save_saved_document``.  This service encapsulates the save → version-snapshot
→ audit-log pipeline behind a single method so the route handlers become thin
HTTP adapters.

Relies on :class:`~app.shared.case_resolver.CaseResolver` for case-type
disambiguation (D1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

from flask import current_app
from flask_login import current_user

from app.extensions import db
from app.services.audit import log_audit
from app.services.version_control import VersionService
from app.shared.case_resolver import CaseResolver
from app.utils.document_storage import save_saved_document

logger = logging.getLogger(__name__)

_VALID_DOC_TYPES = ("petition", "permission")


@dataclass
class SaveResult:
    """Result of a coordinated document save."""

    timestamp: str
    version_number: Optional[int]
    content_hash: Optional[str]
    success: bool


class DocumentSaveCoordinator:
    """Coordinate persistence, versioning, and audit-logging of edited documents.

    Encapsulates the three side-effects that every editor save produces:

    1. **Content persistence** — HTML (+ optional Delta) written to
       ``instance/saved/`` via :func:`save_saved_document`.
    2. **Version snapshot** — a :class:`~app.models.Version` row created
       via :class:`VersionService`.  Explicit saves always snapshot;
       auto-saves snapshot only when content changed (SHA-256 dedup).
    3. **Audit logging** — best-effort :func:`log_audit` call that never
       blocks the response.

    Any step that fails is logged and swallowed so that a versioning or
    audit failure never prevents the document from being saved.
    """

    def __init__(self, case_resolver: Optional[CaseResolver] = None) -> None:
        self._case_resolver = case_resolver or CaseResolver()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def save(
        self,
        case_id: int,
        case_type: str,
        doc_type: str,
        html_content: str,
        delta_content: Optional[dict] = None,
        force_snapshot: bool = False,
    ) -> SaveResult:
        """Persist an edited document and create a version snapshot.

        Args:
            case_id: Primary key of the CaseFile or Adjudication.
            case_type: ``"case_file"`` or ``"adjudication"``.
            doc_type: ``"petition"`` or ``"permission"``.
            html_content: The edited HTML string.
            delta_content: Optional Quill Delta dict.
            force_snapshot: When ``True`` always create a version (explicit
                save).  When ``False`` only snapshot if content changed
                (auto-save dedup).

        Returns:
            :class:`SaveResult` with the on-disk timestamp, version info,
            and success flag.
        """
        # --- 1. Content persistence ---
        try:
            timestamp_str = save_saved_document(
                current_app.instance_path,
                case_id,
                doc_type,
                html_content,
                delta_content,
            )
        except OSError as exc:
            current_app.logger.error("Failed to save HTML for case %s: %s", case_id, exc)
            return SaveResult(
                timestamp=datetime.now(UTC).strftime("%Y%m%d_%H%M%S"),
                version_number=None,
                content_hash=None,
                success=False,
            )

        # --- 2. Version snapshot (best-effort, never blocks) ---
        version = self._snapshot_version(
            case_type, case_id, doc_type, html_content, delta_content, force_snapshot
        )

        # --- 3. Audit logging (best-effort) ---
        self._log_audit(
            case_type,
            case_id,
            doc_type,
            force_snapshot,
            delta_content,
            timestamp_str,
        )

        return SaveResult(
            timestamp=timestamp_str,
            version_number=version.version_number if version else None,
            content_hash=version.content_hash if version else None,
            success=True,
        )

    # ------------------------------------------------------------------ #
    # Private helpers (previously inlined in routes.py)
    # ------------------------------------------------------------------ #

    def _snapshot_version(
        self,
        case_type: str,
        case_id: int,
        doc_type: str,
        html_content: str,
        delta_content: Optional[dict],
        force: bool,
    ) -> Optional[object]:
        """Create a version snapshot via :class:`VersionService`.

        Explicit saves (``force=True``) always create a snapshot; auto-saves
        create one only when the content actually changed (deduped by
        SHA-256 content hash).

        Any failure is logged and swallowed so save/autosave behaviour is
        unchanged.
        """
        try:
            user_id = current_user.get_id() if current_user.is_authenticated else None
            if case_type == "case_file":
                target_kwargs = {"case_id": case_id, "adjudication_id": None}
            else:
                target_kwargs = {"adjudication_id": case_id, "case_id": None}

            service = VersionService()
            if force:
                return service.create_version(
                    doc_type=doc_type,
                    html_content=html_content,
                    delta_content=delta_content,
                    user_id=user_id,
                    **target_kwargs,
                )
            return service.create_version_if_changed(
                doc_type=doc_type,
                html_content=html_content,
                delta_content=delta_content,
                user_id=user_id,
                **target_kwargs,
            )
        except Exception as exc:
            current_app.logger.warning("Version snapshot skipped for case %s: %s", case_id, exc)
            return None

    def _log_audit(
        self,
        case_type: str,
        case_id: int,
        doc_type: str,
        force_snapshot: bool,
        delta_content: Optional[dict],
        timestamp_str: str,
    ) -> None:
        """Best-effort audit logging — never fails a save operation."""
        action = f"DOCUMENT_EDITED_{doc_type.upper()}" if force_snapshot else f"DOCUMENT_AUTOSAVED_{doc_type.upper()}"
        try:
            log_audit(
                entity_type=case_type,
                entity_id=str(case_id),
                action=action,
                actor=self._actor(),
                details={
                    "doc_type": doc_type,
                    "has_delta": delta_content is not None,
                    "timestamp": timestamp_str,
                },
            )
        except Exception:
            current_app.logger.warning("Audit log write failed for case %s; continuing.", case_id)

    @staticmethod
    def _actor() -> str:
        """Return the current user's username or 'anonymous'."""
        return current_user.username if current_user.is_authenticated and current_user.is_active else "anonymous"

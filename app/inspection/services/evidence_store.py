"""Evidence storage: DB operations and audit logging for photo evidence."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

from app.extensions import db
from app.services.audit_context import audit_logger


class EvidenceStore:
    """Database operations for photo evidence with audit logging.

    Single interface: save(processed_photo, inspection_id, **kwargs) -> Evidence.
    Handles Evidence record creation, stamping, updates, deletion, and listing.
    """

    def save_evidence(
        self,
        photo_id: str,
        inspection_id: int | None = None,
        filepath: str = "",
        filename: str = "",
        mime_type: str | None = None,
        raw_lat: float = 0.0,
        raw_lng: float = 0.0,
        accuracy: float = 0.0,
        captured_at: datetime | None = None,
        case_id: str | None = None,
        **kwargs,
    ):
        """Create an Evidence DB record for photo evidence."""
        from app.models import Evidence

        photo_evidence = Evidence(
            id=photo_id,
            inspection_id=inspection_id,
            case_id=case_id or str(inspection_id or ""),
            evidence_type="photo",
            filepath=filepath,
            filename=filename,
            mime_type=mime_type,
            raw_lat=raw_lat,
            raw_lng=raw_lng,
            accuracy=accuracy,
            captured_at=captured_at,
            uploaded_at=datetime.now(UTC),
            verification_status="PENDING",
            stamped=False,
        )

        try:
            db.session.add(photo_evidence)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise RuntimeError(f"Failed to save photo evidence: {exc!s}") from exc

        return photo_evidence

    def update_stamped_evidence(self, evidence_id: str, locality: str | None,
                                 verification_result: dict, stamped_filepath: str):
        """Update evidence record after stamping and verification."""
        from app.models import Evidence

        photo = db.session.get(Evidence, evidence_id)
        if not photo:
            raise FileNotFoundError(f"Photo with id {evidence_id} not found")

        photo.locality = locality
        photo.ip_match = verification_result.get("ip_match", False)
        photo.distance_to_fbo_m = verification_result.get("distance_to_fbo_m")
        photo.verification_status = verification_result.get("verification_status", "PASS")
        photo.filepath = stamped_filepath
        photo.stamped = True

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise RuntimeError(f"Failed to update photo evidence: {exc!s}") from exc

        return photo

    def delete(self, photo_id: str) -> bool:
        """Delete a photo evidence record from DB."""
        from app.models import Evidence

        photo = db.session.get(Evidence, photo_id)
        if not photo:
            raise FileNotFoundError(f"Photo with id {photo_id} not found")

        try:
            db.session.delete(photo)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise RuntimeError("Database error") from None

        return True

    def list_for_inspection(self, inspection_id: int):
        """List all photo evidence for an inspection."""
        from app.models import Evidence, Inspection

        inspection = db.session.get(Inspection, inspection_id)
        if not inspection:
            raise FileNotFoundError(f"Inspection with id {inspection_id} not found")

        return Evidence.query.filter_by(
            inspection_id=inspection_id, evidence_type="photo"
        ).order_by(Evidence.uploaded_at.desc()).all()

    def list_adjudication(self, adjudication_id: int, page: int = 1, per_page: int = 50):
        """List all photos for an adjudication, with pagination."""
        from app.models import Adjudication, Evidence

        adjudication = db.session.get(Adjudication, adjudication_id)
        if not adjudication:
            raise FileNotFoundError(
                f"Adjudication with id {adjudication_id} not found"
            )

        per_page = min(per_page, 200)
        return Evidence.query.filter_by(
            adjudication_id=adjudication_id, evidence_type="photo"
        ).order_by(Evidence.uploaded_at.asc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

    def log_audit(self, photo_id: str, action: str, actor: str, **metadata):
        """Log an audit event for photo evidence."""
        audit_logger("photo").log(photo_id, action, actor=actor, **metadata)

    def cleanup_file(self, filepath: str) -> None:
        """Remove a file if it exists (cleanup on error)."""
        with contextlib.suppress(OSError, FileNotFoundError):
            import os
            if filepath and os.path.exists(filepath):
                os.remove(filepath)

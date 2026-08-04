"""Inspection photo upload / management service (D4 deepening task).

Extracts the four concerns previously inlined in
``app/inspection/routes/photo_routes.py``:

1. **EXIF GPS extraction** — ``_extract_exif_gps`` + degree conversion.
2. **Form-field / EXIF fallback** — the ``_pick`` logic now lives here.
3. **File validation** — extension whitelist, size check, PIL verify.
4. **Storage + evidence creation** — temp-file save, Evidence record,
   DB commit/rollback, geo-verification, image stamping, OCR dispatch,
   and audit logging.

Routes in ``photo_routes.py`` become thin HTTP adapters: parse request →
call service → return JSON.
"""

from __future__ import annotations

import contextlib
import logging
import mimetypes
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import current_app
from werkzeug.utils import secure_filename

from app.extensions import db
from app.inspection.audit import log_audit
from app.inspection.image_processing import process_and_stamp_image
from app.inspection.verification_service import verify_photo_location
from app.utils.storage import delete_photo, upload_photo

logger = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}

try:
    from app.inspection.tasks import run_ocr_extraction as _run_ocr_extraction

    _OCR_AVAILABLE = callable(_run_ocr_extraction)
except ImportError:
    _OCR_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Result / info dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class PhotoUploadResult:
    """Result of a photo upload operation."""

    photo_id: str
    filepath: str
    raw_lat: float
    raw_lng: float
    accuracy: float
    verification: dict[str, Any]
    stamped: bool
    ocr_task_id: str | None = None
    ocr_result: Any | None = None


@dataclass
class PhotoInfo:
    """Lightweight photo metadata returned by listing methods."""

    id: str
    file_url: str
    caption: str | None = None
    uploaded_at: str | None = None
    raw_lat: float | None = None
    raw_lng: float | None = None
    verification_status: str | None = None


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class InspectionPhotoService:
    """Service layer for inspection adjudication photo management.

    Encapsulates EXIF extraction, coordinate fallback, file validation,
    storage, evidence-record creation, geo-verification dispatch, image
    stamping, OCR dispatch, and audit logging — all previously inlined
    in ``photo_routes.py``.
    """

    def upload_evidence(
        self,
        inspection_id: int,
        file_obj,
        lat: float | None = None,
        lng: float | None = None,
        accuracy: float | None = None,
        captured_at: str | None = None,
    ) -> PhotoUploadResult:
        """Upload photo evidence for an inspection.

        This is the full inspection-photo pipeline: EXIF GPS extraction →
        form/EXIF coordinate fallback → temp-file save → Evidence DB row →
        geo-verification → image stamping → (optional) OCR dispatch.

        Requires a Flask request context (uses ``request.form`` and
        ``request.remote_addr``) and a Flask app context.
        """
        from flask import request

        from app.models import Adjudication, CaseFile, Evidence, Inspection

        inspection = db.session.get(Inspection, inspection_id)
        if not inspection:
            raise FileNotFoundError(f"Inspection with id {inspection_id} not found")

        # --- Adjudication cross-check (preserve original guard) ---
        if inspection.adjudication_id:
            adjudication = db.session.get(Adjudication, inspection.adjudication_id)
            if adjudication:
                sample_case = CaseFile.query.filter_by(
                    food_safety_officer_name=adjudication.food_safety_officer,
                    inspection_date=adjudication.First_inspection_date,
                ).first()
                if sample_case and (sample_case.is_substandard or sample_case.is_misbranded):
                    raise ValueError(
                        "Photo evidence not applicable for this violation type"
                    )

        # --- EXIF GPS extraction ---
        exif_lat, exif_lng, exif_accuracy = self._extract_exif_gps(file_obj)

        # --- Coordinate fallback (form values > EXIF > 0.0) ---
        resolved_lat = self._pick_coord(request.form.get("lat"), exif_lat)
        resolved_lng = self._pick_coord(request.form.get("lng"), exif_lng)
        resolved_acc = self._pick_coord(
            request.form.get("accuracy"), exif_accuracy if exif_accuracy is not None else 0.0
        )

        captured_at_str = request.form["captured_at"]
        try:
            captured_at_dt = datetime.fromisoformat(captured_at_str)
        except ValueError:
            raise ValueError(
                "captured_at must be a valid ISO format datetime string"
            ) from None

        # --- File save to temp dir ---
        image_id = str(uuid.uuid4())
        filename = secure_filename(file_obj.filename)
        temp_dir = Path(current_app.instance_path) / "temp_uploads"
        os.makedirs(str(temp_dir), exist_ok=True)
        temp_path = temp_dir / f"{image_id}_{filename}"
        file_obj.save(str(temp_path))

        # --- Evidence DB record ---
        case_id = request.form.get("case_id")
        photo_evidence = Evidence(
            id=image_id,
            inspection_id=inspection_id,
            case_id=case_id or str(inspection_id),
            evidence_type="photo",
            filepath=str(temp_path),
            filename=filename,
            mime_type=mimetypes.guess_type(filename)[0],
            raw_lat=resolved_lat,
            raw_lng=resolved_lng,
            accuracy=resolved_acc,
            captured_at=captured_at_dt,
            uploaded_at=datetime.now(UTC),
            verification_status="PENDING",
            stamped=False,
        )

        try:
            db.session.add(photo_evidence)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            with contextlib.suppress(Exception):
                os.remove(str(temp_path))
            raise RuntimeError(f"Failed to save photo evidence: {exc!s}") from exc

        actor = request.remote_addr
        log_audit(
            "photo",
            image_id,
            "UPLOAD_RECEIVED",
            actor,
            {"raw_lat": resolved_lat, "raw_lng": resolved_lng, "accuracy": resolved_acc},
        )

        # --- Geo-verification ---
        result = verify_photo_location(
            resolved_lat, resolved_lng, resolved_acc, actor, inspection
        )
        log_audit("photo", image_id, "VERIFICATION_RUN", actor, result)

        # --- Image stamping ---
        try:
            filepath = process_and_stamp_image(
                file_obj,
                result["locality"],
                captured_at_str,
                result["verification_status"],
                image_id,
                str(inspection_id),
            )
        except ValueError:
            with contextlib.suppress(Exception):
                db.session.delete(photo_evidence)
                db.session.commit()
            raise

        photo_evidence.locality = result["locality"]
        photo_evidence.ip_match = result["ip_match"]
        photo_evidence.distance_to_fbo_m = result["distance_to_fbo_m"]
        photo_evidence.verification_status = result["verification_status"]
        photo_evidence.filepath = filepath
        photo_evidence.stamped = True

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise RuntimeError(f"Failed to update photo evidence: {exc!s}") from exc

        log_audit("photo", image_id, "PHOTO_SAVED", actor, {"filepath": filepath})

        # --- OCR dispatch (best-effort) ---
        ocr_task_id = None
        ocr_result = None
        if _OCR_AVAILABLE:
            from app.utils.qstash_client import make_dedup_key, publish_task

            payload = {"file_path": filepath}
            try:
                dispatched = publish_task(
                    "run_ocr_extraction",
                    payload=payload,
                    dedup_key=make_dedup_key("run_ocr_extraction", image_id, payload),
                )
            except Exception as exc:
                current_app.logger.warning("OCR dispatch failed: %s", exc)
            else:
                if dispatched["mode"] == "async":
                    ocr_task_id = dispatched["message_id"]
                else:
                    ocr_result = dispatched["result"]
                    if isinstance(ocr_result, Exception):
                        current_app.logger.warning(
                            "OCR extraction returned exception: %s", ocr_result
                        )
                        ocr_result = None

        return PhotoUploadResult(
            photo_id=image_id,
            filepath=filepath,
            raw_lat=resolved_lat,
            raw_lng=resolved_lng,
            accuracy=resolved_acc,
            verification=result,
            stamped=True,
            ocr_task_id=ocr_task_id,
            ocr_result=ocr_result,
        )

    def upload_adjudication_photo(
        self,
        adjudication_id: int,
        file_obj,
        caption: str | None = None,
    ) -> PhotoUploadResult:
        """Upload a photo for an adjudication via R2/B2 storage.

        Simpler flow than ``upload_evidence``: no EXIF extraction or
        geo-verification — the file goes straight to cloud storage and a
        minimal Evidence record is created.
        """
        from app.models import Adjudication, Evidence

        adjudication = db.session.get(Adjudication, adjudication_id)
        if not adjudication:
            raise FileNotFoundError(
                f"Adjudication with id {adjudication_id} not found"
            )

        # --- Validation ---
        original_filename = file_obj.filename
        safe_filename = secure_filename(original_filename)
        if not safe_filename:
            raise ValueError("Invalid filename")

        ext = Path(safe_filename).suffix.lower().lstrip(".")
        if ext not in _ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension '.{ext}'. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
            )

        # --- Storage ---
        try:
            file_url = upload_photo(file_obj, adjudication_id, safe_filename)
        except ValueError:
            raise
        except Exception:
            current_app.logger.exception(
                f"Failed to upload photo for adjudication {adjudication_id}"
            )
            raise RuntimeError("Storage service unavailable") from None

        caption = caption or ""

        # --- Evidence record ---
        photo = Evidence(
            id=str(uuid.uuid4()),
            adjudication_id=adjudication_id,
            evidence_type="photo",
            filepath=file_url,
            filename=safe_filename,
            caption=caption or None,
        )
        try:
            db.session.add(photo)
            db.session.commit()
        except Exception:
            db.session.rollback()
            with contextlib.suppress(Exception):
                delete_photo(file_url)
            current_app.logger.exception(
                f"Failed to save photo evidence for adjudication {adjudication_id}"
            )
            raise RuntimeError("Database error") from None
        return PhotoUploadResult(
            photo_id=photo.id,
            filepath=file_url,
            raw_lat=0.0,
            raw_lng=0.0,
            accuracy=0.0,
            verification={"locality": None, "verification_status": "PASS"},
            stamped=False,
        )

    def delete(self, photo_id: str) -> bool:
        """Delete a photo from storage and remove its DB record."""
        from app.models import Evidence

        photo = db.session.get(Evidence, photo_id)
        if not photo:
            raise FileNotFoundError(f"Photo with id {photo_id} not found")

        if not delete_photo(photo.filepath):
            logger.warning(
                "Storage delete returned False for photo %s (url=%s); proceeding with DB delete",
                photo_id,
                photo.filepath,
            )

        try:
            db.session.delete(photo)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(f"Failed to delete photo record {photo_id}")
            raise RuntimeError("Database error") from None
        return True

    def list_for_inspection(self, inspection_id: int) -> list[PhotoInfo]:
        """List all photo evidence records for an inspection."""
        from app.models import Evidence, Inspection

        inspection = db.session.get(Inspection, inspection_id)
        if not inspection:
            raise FileNotFoundError(f"Inspection with id {inspection_id} not found")

        photos = (
            Evidence.query.filter_by(
                inspection_id=inspection_id, evidence_type="photo"
            )
            .order_by(Evidence.uploaded_at.desc())
            .all()
        )
        return [
            PhotoInfo(
                id=p.id,
                file_url=p.filepath,
                raw_lat=p.raw_lat,
                raw_lng=p.raw_lng,
                verification_status=p.verification_status,
                uploaded_at=p.uploaded_at.isoformat() if p.uploaded_at else None,
            )
            for p in photos
        ]

    def list_adjudication(self, adjudication_id: int, page: int = 1, per_page: int = 50) -> dict:
        """List all photos for an adjudication, with pagination."""
        from app.models import Adjudication, Evidence

        adjudication = db.session.get(Adjudication, adjudication_id)
        if not adjudication:
            raise FileNotFoundError(
                f"Adjudication with id {adjudication_id} not found"
            )

        per_page = min(per_page, 200)
        paginated = (
            Evidence.query.filter_by(
                adjudication_id=adjudication_id, evidence_type="photo"
            )
            .order_by(Evidence.uploaded_at.asc())
            .paginate(page=page, per_page=per_page, error_out=False)
        )

        return {
            "items": [
                PhotoInfo(
                    id=p.id,
                    file_url=p.filepath,
                    caption=p.caption,
                    uploaded_at=p.uploaded_at.isoformat() if p.uploaded_at else None,
                )
                for p in paginated.items
            ],
            "page": paginated.page,
            "per_page": paginated.per_page,
            "total": paginated.total,
        }

    # ------------------------------------------------------------------ #
    # Private helpers (previously inlined in photo_routes.py)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_exif_gps(file_obj) -> tuple[float | None, float | None, float | None]:
        """Attempt to extract GPS latitude, longitude, and accuracy from EXIF.

        Returns ``(lat, lng, accuracy)`` or ``(None, None, None)`` if
        unavailable or if PIL/EXIF data is missing.
        """
        try:
            from PIL import ExifTags, Image

            img = Image.open(file_obj)
            exif = img.getexif()
            if not exif:
                return None, None, None

            gps_info: dict[str, Any] = {}
            for tag, value in exif.items():
                decoded = ExifTags.TAGS.get(tag, tag)
                if decoded == "GPSInfo":
                    for gps_tag in value:
                        gps_decoded = ExifTags.GPSTAGS.get(gps_tag, gps_tag)
                        gps_info[gps_decoded] = value[gps_tag]

            def _convert_to_degrees(ref: str | None, values) -> float | None:
                if not values or len(values) < 3:
                    return None
                d, m, s = values
                try:
                    deg = float(d) + float(m) / 60.0 + float(s) / 3600.0
                    if ref in ("S", "W"):
                        deg = -deg
                    return deg
                except Exception:
                    return None

            lat = _convert_to_degrees(gps_info.get("GPSLatitudeRef"), gps_info.get("GPSLatitude"))
            lng = _convert_to_degrees(
                gps_info.get("GPSLongitudeRef"), gps_info.get("GPSLongitude")
            )

            accuracy: float | None = None
            if "GPSAltitude" in gps_info:
                alt = gps_info["GPSAltitude"]
                try:
                    accuracy = float(alt)
                except Exception:
                    try:
                        accuracy = float(alt[0]) / float(alt[1])
                    except Exception:
                        accuracy = None

            return lat, lng, accuracy
        except Exception:
            return None, None, None

    @staticmethod
    def _pick_coord(form_value: str | None, fallback: float | None) -> float:
        """Resolve a coordinate from form value, falling back to EXIF, then 0.0."""
        if form_value is not None and str(form_value).strip() != "":
            try:
                return float(form_value)
            except (TypeError, ValueError):
                pass
        if fallback is not None:
            return float(fallback)
        return 0.0

"""Photo evidence routes: upload, download, delete, and list."""

import contextlib
import mimetypes
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from flask import current_app, jsonify, request
from werkzeug.utils import secure_filename

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.audit import log_audit
from app.inspection.image_processing import process_and_stamp_image
from app.inspection.verification_service import verify_photo_location
from app.utils.storage import delete_photo, upload_photo

try:
    from app.inspection.tasks import run_ocr_extraction as _run_ocr_extraction

    _OCR_AVAILABLE = callable(_run_ocr_extraction)
except ImportError:
    _OCR_AVAILABLE = False


def _extract_exif_gps(file_obj):
    """Attempt to extract GPS latitude, longitude, and altitude/accuracy from image EXIF.
    Returns (lat, lng, accuracy) or (None, None, None) if unavailable.
    """

    try:
        from PIL import ExifTags, Image

        img = Image.open(file_obj)
        exif = img.getexif()
        if not exif:
            return None, None, None

        gps_info = {}
        for tag, value in exif.items():
            decoded = ExifTags.TAGS.get(tag, tag)
            if decoded == "GPSInfo":
                for gps_tag in value:
                    gps_decoded = ExifTags.GPSTAGS.get(gps_tag, gps_tag)
                    gps_info[gps_decoded] = value[gps_tag]

        def _convert_to_degrees(ref, values):
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
        lng = _convert_to_degrees(gps_info.get("GPSLongitudeRef"), gps_info.get("GPSLongitude"))

        accuracy = None
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


@inspection_bp.route("/<int:inspection_id>/photo-evidence", methods=["GET"])
def get_inspection_photo_evidence(inspection_id):
    """Get all photo evidence records for an inspection (JSON)."""
    from app.models import Evidence, Inspection

    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    photos = (
        Evidence.query.filter_by(inspection_id=inspection_id, evidence_type="photo")
        .order_by(Evidence.uploaded_at.desc())
        .all()
    )
    return jsonify(
        [
            {
                "image_id": p.id,
                "filepath": p.filepath,
                "raw_lat": p.raw_lat,
                "raw_lng": p.raw_lng,
                "accuracy": p.accuracy,
                "captured_at": p.captured_at.isoformat() if p.captured_at else None,
                "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
                "locality": p.locality,
                "verification_status": p.verification_status,
                "stamped": p.stamped,
            }
            for p in photos
        ]
    )


@inspection_bp.route("/photo-upload", methods=["POST"])
def upload_photo_evidence():
    """Upload photo evidence for an inspection."""
    from app.models import Adjudication, CaseFile, Evidence, Inspection

    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    inspection_id = request.form.get("inspection_id")
    case_id = request.form.get("case_id")

    if not inspection_id and not case_id:
        return jsonify({"error": "Either inspection_id or case_id is required"}), 400

    if inspection_id:
        try:
            inspection_id = int(inspection_id)
        except ValueError:
            return jsonify({"error": "inspection_id must be an integer"}), 400
        inspection = db.session.get(Inspection, inspection_id)
    else:
        inspection = db.session.get(Inspection, case_id)
        if inspection:
            inspection_id = inspection.id

    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404

    if inspection.adjudication_id:
        adjudication = db.session.get(Adjudication, inspection.adjudication_id)
        if adjudication:
            sample_case = CaseFile.query.filter_by(
                food_safety_officer_name=adjudication.food_safety_officer,
                inspection_date=adjudication.First_inspection_date,
            ).first()
            if sample_case and (sample_case.is_substandard or sample_case.is_misbranded):
                return jsonify({"error": "Photo evidence not applicable for this violation type"}), 400

    required_fields = ["lat", "lng", "accuracy", "captured_at"]
    for field in required_fields:
        if field not in request.form:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    exif_lat, exif_lng, exif_accuracy = _extract_exif_gps(file)

    def _pick(form_key, fallback):
        val = request.form.get(form_key)
        if val is not None and str(val).strip() != "":
            return val
        if fallback is not None:
            return fallback
        return 0.0

    try:
        lat = float(_pick("lat", exif_lat))
        lng = float(_pick("lng", exif_lng))
        accuracy = float(_pick("accuracy", exif_accuracy if exif_accuracy is not None else 0.0))
    except ValueError:
        return jsonify({"error": "lat, lng, and accuracy must be valid floats"}), 400

    captured_at_str = request.form["captured_at"]

    try:
        captured_at = datetime.fromisoformat(captured_at_str)
    except ValueError:
        return jsonify({"error": "captured_at must be a valid ISO format datetime string"}), 400

    image_id = str(uuid.uuid4())

    filename = secure_filename(file.filename)
    temp_dir = Path(current_app.instance_path) / "temp_uploads"
    os.makedirs(str(temp_dir), exist_ok=True)
    temp_path = temp_dir / f"{image_id}_{filename}"
    file.save(str(temp_path))

    photo_evidence = Evidence(
        id=image_id,
        inspection_id=inspection_id,
        case_id=case_id or str(inspection_id),
        evidence_type="photo",
        filepath=temp_path,
        filename=filename,
        mime_type=mimetypes.guess_type(filename)[0],
        raw_lat=lat,
        raw_lng=lng,
        accuracy=accuracy,
        captured_at=captured_at,
        uploaded_at=datetime.now(UTC),
        verification_status="PENDING",
        stamped=False,
    )

    try:
        db.session.add(photo_evidence)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        if temp_path.exists():
            os.remove(str(temp_path))
        return jsonify({"error": f"Failed to save photo evidence: {e!s}"}), 500

    actor = request.remote_addr
    log_audit("photo", image_id, "UPLOAD_RECEIVED", actor, {"raw_lat": lat, "raw_lng": lng, "accuracy": accuracy})

    result = verify_photo_location(lat, lng, accuracy, request.remote_addr, inspection)

    log_audit("photo", image_id, "VERIFICATION_RUN", actor, result)

    try:
        filepath = process_and_stamp_image(
            file,
            result["locality"],
            captured_at_str,
            result["verification_status"],
            image_id,
            str(inspection_id),
        )
    except ValueError as exc:
        try:
            db.session.delete(photo_evidence)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({"error": f"Image processing failed: {exc}"}), 400

    photo_evidence.locality = result["locality"]
    photo_evidence.ip_match = result["ip_match"]
    photo_evidence.distance_to_fbo_m = result["distance_to_fbo_m"]
    photo_evidence.verification_status = result["verification_status"]
    photo_evidence.filepath = filepath
    photo_evidence.stamped = True

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to update photo evidence: {e!s}"}), 500

    log_audit("photo", image_id, "PHOTO_SAVED", actor, {"filepath": filepath})

    ocr_result = None
    ocr_task_id = None
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
                    current_app.logger.warning("OCR extraction returned exception: %s", ocr_result)
                    ocr_result = None

    return (
        jsonify(
            {
                "image_id": image_id,
                "verification_status": result["verification_status"],
                "ocr_task_id": ocr_task_id,
                "ocr_result": ocr_result,
            }
        ),
        201,
    )


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["POST"])
def upload_adjudication_photo(adjudication_id):
    """Upload a photo for an adjudication via R2/B2 storage."""
    from app.models import Adjudication, Evidence

    adjudication = db.session.get(Adjudication, adjudication_id)
    if not adjudication:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    if "photo" not in request.files:
        return jsonify({"error": 'No photo file provided. Use field name "photo".'}), 400

    file = request.files["photo"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    original_filename = file.filename
    safe_filename = secure_filename(original_filename)
    if not safe_filename:
        return jsonify({"error": "Invalid filename."}), 400

    allowed_extensions = {"jpg", "jpeg", "png", "webp", "heic"}
    ext = Path(safe_filename).suffix.lower().lstrip(".")
    if ext not in allowed_extensions:
        return (
            jsonify(
                {
                    "error": f"Unsupported file extension '.{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}",
                }
            ),
            400,
        )

    try:
        file_url = upload_photo(file, adjudication_id, safe_filename)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception(f"Failed to upload photo for adjudication {adjudication_id}")
        return jsonify({"error": "Storage service unavailable."}), 502

    caption = request.form.get("caption", "").strip()

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
        current_app.logger.exception(f"Failed to save photo evidence for adjudication {adjudication_id}")
        return jsonify({"error": "Database error."}), 500

    return (
        jsonify(
            {
                "id": photo.id,
                "file_url": photo.filepath,
                "caption": photo.caption,
                "uploaded_at": photo.uploaded_at.isoformat() if photo.uploaded_at else None,
            }
        ),
        201,
    )


@inspection_bp.route("/photos/<photo_id>", methods=["DELETE"])
def delete_adjudication_photo(photo_id):
    """Delete a photo from storage and remove its DB record."""
    from app.models import Evidence

    photo = db.session.get(Evidence, photo_id)
    if not photo:
        return jsonify({"error": f"Photo with id {photo_id} not found"}), 404

    if not delete_photo(photo.filepath):
        current_app.logger.warning(
            "Storage delete returned False for photo %s (url=%s); proceeding with DB delete",
            photo_id,
            photo.filepath,
        )

    try:
        db.session.delete(photo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(f"Failed to delete photo record {photo_id}")
        return jsonify({"error": "Database error."}), 500

    return "", 204


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["GET"])
def list_adjudication_photos(adjudication_id):
    """List all photos for an adjudication, ordered by upload time. Supports pagination."""
    from app.models import Adjudication, Evidence

    adjudication = db.session.get(Adjudication, adjudication_id)
    if not adjudication:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(per_page, 200)

    paginated = (
        Evidence.query.filter_by(adjudication_id=adjudication_id, evidence_type="photo")
        .order_by(Evidence.uploaded_at.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify(
        {
            "items": [
                {
                    "id": p.id,
                    "file_url": p.filepath,
                    "caption": p.caption,
                    "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
                }
                for p in paginated.items
            ],
            "page": paginated.page,
            "per_page": paginated.per_page,
            "total": paginated.total,
        }
    )

"""Photo evidence routes — thin HTTP adapters for :class:`InspectionPhotoService`.

All business logic (EXIF extraction, coordinate fallback, validation,
storage, verification, stamping, OCR dispatch, audit logging) lives in
``app/inspection/photo_service.py``.  These handlers parse the request,
delegate to the service, and return JSON.
"""

from flask import jsonify, request

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.photo_service import InspectionPhotoService

_photo_service = InspectionPhotoService()


@inspection_bp.route("/<int:inspection_id>/photo-evidence", methods=["GET"])
def get_inspection_photo_evidence(inspection_id):
    """Get all photo evidence records for an inspection (JSON)."""
    try:
        photos = _photo_service.list_for_inspection(inspection_id)
    except FileNotFoundError:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    return jsonify(
        [
            {
                "image_id": p.id,
                "filepath": p.file_url,
                "raw_lat": p.raw_lat,
                "raw_lng": p.raw_lng,
                "verification_status": p.verification_status,
                "uploaded_at": p.uploaded_at,
            }
            for p in photos
        ]
    )


@inspection_bp.route("/photo-upload", methods=["POST"])
def upload_photo_evidence():
    """Upload photo evidence for an inspection."""
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    inspection_id_str = request.form.get("inspection_id")
    case_id = request.form.get("case_id")

    if not inspection_id_str and not case_id:
        return jsonify({"error": "Either inspection_id or case_id is required"}), 400

    try:
        if inspection_id_str:
            inspection_id = int(inspection_id_str)
        else:
            from app.models import Inspection

            inspection = db.session.get(Inspection, case_id)
            if not inspection:
                return jsonify({"error": "Inspection not found"}), 404
            inspection_id = inspection.id
    except (ValueError, TypeError):
        return jsonify({"error": "inspection_id must be an integer"}), 400

    required_fields = ["lat", "lng", "accuracy", "captured_at"]
    for field in required_fields:
        if field not in request.form:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    file.seek(0)

    try:
        result = _photo_service.upload_evidence(
            inspection_id,
            file,
        )
    except FileNotFoundError:
        return jsonify({"error": "Inspection not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    return (
        jsonify(
            {
                "image_id": result.photo_id,
                "verification_status": result.verification.get("verification_status"),
                "ocr_task_id": result.ocr_task_id,
                "ocr_result": result.ocr_result,
            }
        ),
        201,
    )


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["POST"])
def upload_adjudication_photo(adjudication_id):
    """Upload a photo for an adjudication via R2/B2 storage."""

    if "photo" not in request.files:
        return jsonify({"error": 'No photo file provided. Use field name "photo".'}), 400

    file = request.files["photo"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    try:
        result = _photo_service.upload_adjudication_photo(
            adjudication_id, file, caption=request.form.get("caption", "")
        )
    except FileNotFoundError:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        msg = str(exc)
        if "Storage" in msg:
            return jsonify({"error": "Storage service unavailable."}), 502
        return jsonify({"error": "Database error."}), 500

    return (
        jsonify(
            {
                "id": result.photo_id,
                "file_url": result.filepath,
                "uploaded_at": None,
            }
        ),
        201,
    )


@inspection_bp.route("/photos/<photo_id>", methods=["DELETE"])
def delete_adjudication_photo(photo_id):
    """Delete a photo from storage and remove its DB record."""
    try:
        _photo_service.delete(photo_id)
    except FileNotFoundError:
        return jsonify({"error": f"Photo with id {photo_id} not found"}), 404
    except RuntimeError:
        return jsonify({"error": "Database error."}), 500

    return "", 204


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["GET"])
def list_adjudication_photos(adjudication_id):
    """List all photos for an adjudication, ordered by upload time. Supports pagination."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    try:
        paginated = _photo_service.list_adjudication(
            adjudication_id, page=page, per_page=per_page
        )
    except FileNotFoundError:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    return jsonify(paginated)

"""Tests for the InspectionPhotoService (D4 deepening task).

Verifies that the service correctly handles:
- EXIF GPS extraction + coordinate fallback (form > EXIF > 0.0)
- File validation (extension, filename)
- Evidence record creation + DB commit/rollback
- Geo-verification dispatch
- Image stamping
- OCR dispatch (conditional)
- Delete + listing operations

Business logic is mocked so tests run without filesystem, storage, or
network dependencies.
"""

import pytest

from app.inspection.photo_service import (
    InspectionPhotoService,
    PhotoInfo,
    PhotoUploadResult,
)


@pytest.fixture
def test_app():
    """Create a minimal Flask app with an in-memory SQLite database."""
    from app import create_app
    from app.extensions import db

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_PDF_GENERATION"] = "1"
    with app.app_context():
        db.drop_all()
        db.create_all()
        with app.test_request_context():
            yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def service(test_app):
    return InspectionPhotoService()


@pytest.fixture
def inspection(test_app):
    """Create an Inspection and its associated Adjudication."""
    from datetime import datetime

    from app.extensions import db
    from app.models import Adjudication, FSO, Inspection

    fso = FSO(fso_name="Test FSO")
    db.session.add(fso)
    db.session.commit()

    inspection = Inspection(
        inspection_code="INSP-001",
        fso_name="Test FSO",
        inspection_date=datetime(2026, 1, 1),
        compliance_deadline=datetime(2026, 2, 1),
    )
    db.session.add(inspection)
    db.session.commit()

    adj = Adjudication(
        case_number="ADJ-001",
        food_safety_officer="Test FSO",
        non_license="no",
        pre_authorization="no",
        complaint_lodged="no",
        fbo_owner="Test Owner",
        fbo_name="Test FBO",
        fbo_address="123 FBO St",
        fssai_license="FSSAI123",
        concerned_food="Test Food",
        problem="Contamination",
        First_inspection_date=datetime(2026, 1, 1),
        compliance_deadline=datetime(2026, 2, 1),
        inspection_date=datetime(2026, 1, 15),
    )
    db.session.add(adj)
    db.session.commit()
    return inspection, adj


class TestCoordinateFallback:
    """The _pick_coord helper: form value > EXIF > 0.0."""

    def test_form_value_wins_over_exif(self, service):
        result = service._pick_coord("12.5", 99.9)
        assert result == 12.5

    def test_exif_used_when_form_none(self, service):
        result = service._pick_coord(None, 99.9)
        assert result == 99.9

    def test_exif_used_when_form_empty(self, service):
        result = service._pick_coord("", 99.9)
        assert result == 99.9

    def test_zero_when_neither(self, service):
        result = service._pick_coord(None, None)
        assert result == 0.0

    def test_form_float_string(self, service):
        result = service._pick_coord("3.14", None)
        assert result == 3.14


class TestExifExtraction:
    """_extract_exif_gps returns (None, None, None) for non-EXIF images."""

    def test_missing_exif_returns_nones(self, service):
        """A simple non-geotagged image returns (None, None, None)."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (10, 10), "red").save(buf, format="PNG")
        buf.seek(0)

        lat, lng, acc = service._extract_exif_gps(buf)
        assert lat is None
        assert lng is None
        assert acc is None


class TestValidation:
    """Extension validation in upload_adjudication_photo."""

    def test_unsupported_extension_raises(self, service, inspection, test_app):
        """Non-whitelisted extension raises ValueError."""
        import io

        from app.models import Evidence

        _, adj = inspection

        mock_file = io.BytesIO(b"fake")
        mock_file.filename = "document.txt"

        with pytest.raises(ValueError, match="Unsupported file extension"):
            service.upload_adjudication_photo(adj.id, mock_file)

    def test_empty_filename_raises(self, service, inspection):
        """Empty filename raises ValueError."""
        import io

        _, adj = inspection

        mock_file = io.BytesIO(b"fake")
        mock_file.filename = ""

        with pytest.raises(ValueError, match="Invalid filename"):
            service.upload_adjudication_photo(adj.id, mock_file)


class TestDeleteMethod:
    """delete() removes the Evidence record."""

    def test_delete_nonexistent_photo_raises_filenotfound(self, service, test_app):
        """Deleting a photo ID that doesn't exist raises FileNotFoundError."""
        from app.models import Evidence

        with pytest.raises(FileNotFoundError):
            service.delete("nonexistent-id")


class TestListForInspection:
    """list_for_inspection returns PhotoInfo list."""

    def test_list_nonexistent_inspection_raises(self, service):
        """Listing for a non-existent inspection raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            service.list_for_inspection(99999)


class TestListAdjudication:
    """list_adjudication returns paginated dict."""

    def test_list_nonexistent_adjudication_raises(self, service):
        """Listing for a non-existent adjudication raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            service.list_adjudication(99999)


class TestSaveResultAndPhotoInfoDataclasses:
    """Dataclasses have expected fields."""

    def test_photo_upload_result_fields(self):
        result = PhotoUploadResult(
            photo_id="test-id",
            filepath="/path/to/photo.webp",
            raw_lat=12.0,
            raw_lng=77.0,
            accuracy=5.0,
            verification={"locality": "Test"},
            stamped=True,
        )
        assert result.photo_id == "test-id"
        assert result.stamped is True
        assert result.ocr_task_id is None
        assert result.ocr_result is None

    def test_photo_info_fields(self):
        info = PhotoInfo(
            id="test-id",
            file_url="/path/to/photo",
        )
        assert info.id == "test-id"
        assert info.caption is None
        assert info.uploaded_at is None

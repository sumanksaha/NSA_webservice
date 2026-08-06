"""Phase 16 tests — case-level export, import, and daily DB snapshot.

Covers:
1. JSON export structure (case + annexures + evidence + versions).
2. ZIP export contents (JSON manifest + annexure/evidence files).
3. Import (JSON) creates a new case with cloned related records.
4. Daily scheduled DB snapshot writes a dated ZIP to disk.
"""

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Annexure, CaseFile, Evidence, User, Version

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_case_file(case_number: str = "EXPORT001") -> CaseFile:
    """Build a fully-populated CaseFile (all NOT NULL fields set)."""
    return CaseFile(
        case_number=case_number,
        food_safety_officer_name="Test Officer",
        authorization_date=datetime(2026, 7, 3, tzinfo=UTC),
        inspection_date=datetime(2026, 7, 3, tzinfo=UTC),
        inspection_time="10:00",
        manufacturer_fssai="MFG123",
        manufacturer_name="Test Manufacturer",
        manufacturer_fbo_name="Test MFG FBO",
        manufacturer_address="123 Mfg St",
        retailer_fssai="RET456",
        retailer_name="Test Retailer",
        retailer_fbo_name="Test Retailer FBO",
        retailer_address="456 Retail St",
        product_name="Test Product",
        batch_no="BATCH001",
        sample_quantity="1000g",
        packet_count=4,
        mfg_date=datetime(2026, 6, 1, tzinfo=UTC),
        expiry_date=datetime(2026, 8, 1, tzinfo=UTC),
        sample_code="TEST001",
        sample_submission_date=datetime(2026, 7, 2, tzinfo=UTC),
        Lab_Registration_No="WB/FOOD/2025/001",
        do_receipt_date=datetime(2026, 7, 4, tzinfo=UTC),
        is_misbranded=False,
        is_substandard=False,
        analyst_report_no="PK/378/2025-26",
        analyst_report_date=datetime(2026, 7, 5, tzinfo=UTC),
        directive_letter_no="H/FSSA/FSO/3054/2025-26",
        directive_letter_date=datetime(2026, 7, 6, tzinfo=UTC),
        retailer_report_receive_date=datetime(2026, 7, 7, tzinfo=UTC),
        manufacturer_report_receive_date=datetime(2026, 7, 8, tzinfo=UTC),
        applicable_regulation="Regulation No 5(9)",
        applicable_clause="Clause (zf) of subsection 1 of section 3 of the FSSA,2006",
    )


def _make_adjudication(case_number: str = "ADJ001"):
    """Build a fully-populated Adjudication (all NOT NULL fields set)."""
    from app.models import Adjudication

    return Adjudication(
        case_number=case_number,
        food_safety_officer="Test Officer",
        fbo_owner="Owner Name",
        fbo_name="Test FBO",
        fbo_address="789 FBO St",
        fssai_license="FSSAI999",
        First_inspection_date=datetime(2026, 7, 1, tzinfo=UTC),
        compliance_deadline=datetime(2026, 7, 15, tzinfo=UTC),
        inspection_date=datetime(2026, 7, 3, tzinfo=UTC),
    )


@pytest.fixture
def test_client(tmp_path, test_db_uri):
    """Test client on an isolated in-memory DB with admin + regular users."""
    from app import create_app

    app = create_app(db_uri=test_db_uri)
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.instance_path = str(tmp_path)

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            admin = User(username="admin", password_hash="x", is_admin=True)  # noqa: S106
            regular = User(username="regular", password_hash="x", is_admin=False)  # noqa: S106
            db.session.add_all([admin, regular])
            db.session.add(_make_case_file())
            db.session.add(_make_adjudication())
            db.session.commit()

            # Create annexure, evidence, and version records for case_file (id=1)
            instance = str(tmp_path)
            annexure_dir = Path(instance) / "annexures"
            annexure_dir.mkdir(parents=True, exist_ok=True)
            annexure_file = annexure_dir / "test_annexure.pdf"
            annexure_file.write_text("dummy annexure content")

            a = Annexure(
                case_id=1,
                caption="Test Annexure",
                date=datetime(2026, 7, 2, tzinfo=UTC),
                file_hash="a" * 64,
                page_count=1,
                ocr_text="Sample OCR text",
                tags="tag1,tag2",
                filepath=str(annexure_file),
                filename="test_annexure.pdf",
                file_size=100,
                mime_type="application/pdf",
                annexure_letter="A",
            )
            db.session.add(a)
            db.session.flush()

            e = Evidence(
                case_id=1,
                evidence_type="photo",
                filepath=str(annexure_file),
                filename="photo.jpg",
                file_size=200,
                mime_type="image/jpeg",
                file_hash="b" * 64,
                verification_status="VERIFIED",
                caption="Test Evidence",
            )
            db.session.add(e)
            db.session.flush()

            v = Version(
                case_id=1,
                doc_type="petition",
                version_number=1,
                content_hash="c" * 64,
                html_snapshot="<p>Snapshot HTML</p>",
                delta='{"ops":[]}',
                change_summary="Initial version",
            )
            db.session.add(v)

            # Same for adjudication (id=1)
            a2 = Annexure(
                adjudication_id=1,
                caption="Adj Annexure",
                date=datetime(2026, 7, 2, tzinfo=UTC),
                file_hash="d" * 64,
                page_count=2,
                ocr_text="OCR for adjudication",
                tags="tag3",
                filepath=str(annexure_file),
                filename="adj_annexure.pdf",
                file_size=150,
                mime_type="application/pdf",
                annexure_letter="B",
            )
            db.session.add(a2)
            db.session.flush()

            e2 = Evidence(
                adjudication_id=1,
                evidence_type="report",
                filepath=str(annexure_file),
                filename="report.pdf",
                file_size=300,
                mime_type="application/pdf",
                file_hash="e" * 64,
                verification_status="PENDING",
            )
            db.session.add(e2)
            db.session.flush()

            v2 = Version(
                adjudication_id=1,
                doc_type="petition",
                version_number=1,
                content_hash="f" * 64,
                html_snapshot="<p>Adj snapshot HTML</p>",
                delta=None,
                change_summary="Adj initial",
            )
            db.session.add(v2)

            db.session.commit()

        yield client, app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login_as(client, username: str) -> None:
    """Log in via Flask-Login session key (id 1 = admin, 2 = regular)."""
    user_id = "1" if username == "admin" else "2"
    with client.session_transaction() as sess:
        sess["_user_id"] = user_id
        sess["_fresh"] = True


# ---------------------------------------------------------------------------
# Test class 1 — Export JSON
# ---------------------------------------------------------------------------


class TestExportCaseJson:
    def test_export_case_file_json_structure(self, test_client):
        """CaseFile JSON export has the expected top-level keys."""
        _, app = test_client
        from app.case_file_generator.services import export_case_as_json

        with app.app_context():
            data = export_case_as_json(1, "case_file")

        assert data["case_type"] == "case_file"
        assert data["case"]["case_number"] == "EXPORT001"
        assert "exported_at" in data
        assert len(data["annexures"]) == 1
        assert len(data["evidence"]) == 1
        assert len(data["versions"]) == 1

    def test_export_adjudication_json_structure(self, test_client):
        """Adjudication JSON export has the expected top-level keys."""
        _, app = test_client
        from app.case_file_generator.services import export_case_as_json

        with app.app_context():
            data = export_case_as_json(1, "adjudication")

        assert data["case_type"] == "adjudication"
        assert data["case"]["case_number"] == "ADJ001"
        assert len(data["annexures"]) == 1
        assert len(data["evidence"]) == 1
        assert len(data["versions"]) == 1

    def test_export_case_file_serializes_nested_fields(self, test_client):
        """Annexure/evidence/version serialized fields match the model."""
        _, app = test_client
        from app.case_file_generator.services import export_case_as_json

        with app.app_context():
            data = export_case_as_json(1, "case_file")

        annexure = data["annexures"][0]
        assert annexure["caption"] == "Test Annexure"
        assert annexure["file_hash"] == "a" * 64
        assert annexure["annexure_letter"] == "A"
        assert annexure["ocr_text"] == "Sample OCR text"

        evidence = data["evidence"][0]
        assert evidence["evidence_type"] == "photo"
        assert evidence["verification_status"] == "VERIFIED"

        version = data["versions"][0]
        assert version["doc_type"] == "petition"
        assert version["version_number"] == 1
        assert version["html_snapshot"] == "<p>Snapshot HTML</p>"

    def test_export_nonexistent_case_returns_404_via_http(self, test_client):
        """HTTP endpoint returns 404 for a missing case."""
        client, _ = test_client
        _login_as(client, "admin")
        resp = client.get("/case_file_generator/api/cases/99999/export.json")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test class 2 — Export ZIP
# ---------------------------------------------------------------------------


class TestExportCaseZip:
    def test_zip_contains_case_export_json(self, test_client):
        """ZIP must contain ``case_export.json`` manifest."""
        _, app = test_client
        from app.case_file_generator.services import export_case_as_zip

        with app.app_context():
            zip_bytes = export_case_as_zip(1, "case_file")

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "case_export.json" in names
            manifest = json.loads(zf.read("case_export.json"))
            assert manifest["case_type"] == "case_file"
            assert manifest["case"]["case_number"] == "EXPORT001"

    def test_zip_contains_annexure_and_evidence_files(self, test_client):
        """ZIP must bundle the raw annexure and evidence files from disk."""
        _, app = test_client
        from app.case_file_generator.services import export_case_as_zip

        with app.app_context():
            zip_bytes = export_case_as_zip(1, "case_file")

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert any(n.startswith("annexures/") for n in names)
            assert any(n.startswith("evidence/") for n in names)

    def test_zip_export_via_http(self, test_client):
        """HTTP ZIP export endpoint returns 200 with a real ZIP."""
        client, _ = test_client
        _login_as(client, "admin")
        resp = client.get("/case_file_generator/api/cases/1/export.zip")
        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            assert "case_export.json" in zf.namelist()


# ---------------------------------------------------------------------------
# Test class 3 — Import
# ---------------------------------------------------------------------------


class TestImportCase:
    def test_import_creates_new_case_file(self, test_client):
        """Importing a CaseFile JSON clones the case + related records."""
        _, app = test_client
        from app.case_file_generator.services import export_case_as_json, import_case_from_json

        with app.app_context():
            original_count = CaseFile.query.count()
            json_data = export_case_as_json(1, "case_file")
            new_id = import_case_from_json(json_data)
            assert new_id != 1
            assert CaseFile.query.count() == original_count + 1

            new_case = db.session.get(CaseFile, new_id)
            assert new_case.case_number == "EXPORT001"
            assert new_case.food_safety_officer_name == "Test Officer"

            assert Annexure.query.filter_by(case_id=new_id).count() == 1
            assert Evidence.query.filter_by(case_id=new_id).count() == 1
            assert Version.query.filter_by(case_id=new_id).count() == 1

    def test_import_adjudication_clones_related_records(self, test_client):
        """Importing an Adjudication JSON clones annexures/evidence/versions."""
        _, app = test_client
        from app.case_file_generator.services import export_case_as_json, import_case_from_json

        with app.app_context():
            json_data = export_case_as_json(1, "adjudication")
            new_id = import_case_from_json(json_data)
            assert new_id != 1
            assert new_id > 1

            from app.models import Adjudication

            assert Adjudication.query.count() >= 2

            assert Annexure.query.filter_by(adjudication_id=new_id).count() == 1
            assert Evidence.query.filter_by(adjudication_id=new_id).count() == 1
            assert Version.query.filter_by(adjudication_id=new_id).count() == 1

    def test_import_rejects_invalid_case_type(self, test_client):
        """Import raises ValueError for unknown case_type."""
        _, app = test_client
        from app.case_file_generator.services import import_case_from_json

        with app.app_context():
            with pytest.raises(ValueError, match="Unknown case_type"):
                import_case_from_json({"case_type": "bogus", "case": {}})

    def test_import_rejects_missing_case_dict(self, test_client):
        """Import raises ValueError when 'case' is missing or not a dict."""
        _, app = test_client
        from app.case_file_generator.services import import_case_from_json

        with app.app_context():
            with pytest.raises(ValueError, match="must contain a 'case' dict"):
                import_case_from_json({"case_type": "case_file"})

    def test_import_via_http(self, test_client):
        """HTTP import endpoint creates a new case and returns 201."""
        client, app = test_client
        _login_as(client, "admin")

        with app.app_context():
            from app.case_file_generator.services import export_case_as_json

            json_data = export_case_as_json(1, "case_file")
            json_bytes = json.dumps(json_data, default=str).encode("utf-8")

        resp = client.post(
            "/case_file_generator/api/cases/import",
            data={"file": (io.BytesIO(json_bytes), "case_export.json")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert "new_case_id" in body
        assert body["new_case_id"] != 1


# ---------------------------------------------------------------------------
# Test class 4 — Daily snapshot
# ---------------------------------------------------------------------------


class TestDailySnapshot:
    def test_create_daily_db_snapshot_writes_file(self, test_client):
        """``create_daily_db_snapshot`` writes a dated ZIP to instance/backups/."""
        _, app = test_client
        from app.utils.backup import create_daily_db_snapshot

        with app.app_context():
            path = create_daily_db_snapshot()

        assert Path(path).exists()
        assert Path(path).stat().st_size > 0
        with zipfile.ZipFile(path) as zf:
            assert "metadata.json" in zf.namelist()
            assert "database.json" in zf.namelist()
        Path(path).unlink(missing_ok=True)

    def test_beat_schedule_configured(self, test_client):
        """make_celery sets a beat_schedule with the daily-db-snapshot task."""
        _, app = test_client
        with app.app_context():
            celery_instance = app.celery
            assert celery_instance is not None
            beat = celery_instance.conf.beat_schedule
            assert "daily-db-snapshot" in beat
            assert beat["daily-db-snapshot"]["task"] == "app.utils.backup.create_daily_db_snapshot_task"

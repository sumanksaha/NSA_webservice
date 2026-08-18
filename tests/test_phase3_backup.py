"""Phase 3 tests — local database backup & restore (roadmap TODO #5).

Covers:
1. Backup archive structure (metadata.json + database.json + files/).
2. Database round-trip (dump → mutate → restore → verify).
3. Instance-file round-trip (file bundled, removed, restored).
4. Admin gating of the download/restore endpoints.
5. Restore endpoint end-to-end via HTTP.
"""

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Annexure, CaseFile, User


def _make_case_file(case_number: str = "BACKUP001") -> CaseFile:
    """Build a fully-populated CaseFile (all NOT NULL fields set)."""
    return CaseFile(
        case_number=case_number,
        food_safety_officer_name="Test Officer",
        authorization_date=datetime(2026, 7, 3),
        inspection_date=datetime(2026, 7, 3),
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
        mfg_date=datetime(2026, 6, 1),
        expiry_date=datetime(2026, 8, 1),
        sample_code="TEST001",
        sample_submission_date=datetime(2026, 7, 2),
        Lab_Registration_No="WB/FOOD/2025/001",
        do_receipt_date=datetime(2026, 7, 4),
        is_misbranded=False,
        is_substandard=False,
        analyst_report_no="PK/378/2025-26",
        analyst_report_date=datetime(2026, 7, 5),
        directive_letter_no="H/FSSA/FSO/3054/2025-26",
        directive_letter_date=datetime(2026, 7, 6),
        retailer_report_receive_date=datetime(2026, 7, 7),
        manufacturer_report_receive_date=datetime(2026, 7, 8),
        applicable_regulation="Regulation No 5(9)",
        applicable_clause="Clause (zf) of subsection 1 of section 3 of the FSSA,2006",
    )


@pytest.fixture
def test_client(tmp_path):
    """Test client on an isolated in-memory DB with admin + regular users.

    ``app.instance_path`` is pointed at ``tmp_path`` so the archive file
    round-trip tests never touch the real ``instance/`` folder.
    """
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.instance_path = str(tmp_path)

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            admin = User(username="admin", password_hash="x", is_admin=True)
            regular = User(username="regular", password_hash="x", is_admin=False)
            db.session.add_all([admin, regular])
            db.session.add(_make_case_file())
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


def _build_backup(app) -> bytes:
    """Build a backup ZIP bytes for the current DB."""
    from app.utils.backup import build_backup_archive

    with app.app_context():
        return build_backup_archive().getvalue()


# ---------------------------------------------------------------------------
# Archive structure
# ---------------------------------------------------------------------------


class TestBackupArchive:
    def test_archive_contains_metadata_database_and_files(self, test_client):
        _, app = test_client
        archive = _build_backup(app)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = zf.namelist()
            assert "metadata.json" in names
            assert "database.json" in names

            metadata = json.loads(zf.read("metadata.json"))
            assert metadata["version"] == 1
            assert metadata["dialect"] == "sqlite"
            assert metadata["table_counts"]["case_files"] == 1

            database = json.loads(zf.read("database.json"))
            assert any(row["case_number"] == "BACKUP001" for row in database["case_files"])

    def test_archive_bundles_instance_files(self, test_client):
        _, app = test_client
        saved_dir = Path(app.instance_path) / "saved"
        saved_dir.mkdir(parents=True, exist_ok=True)
        sample_file = saved_dir / "backup_phase3_test.html"
        sample_file.write_text("<p>hello</p>", encoding="utf-8")
        try:
            archive = _build_backup(app)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                names = zf.namelist()
                assert any(name.startswith("files/saved/") for name in names), "saved/ files must be bundled"
        finally:
            sample_file.unlink(missing_ok=True)

    def test_restore_rejects_invalid_archive(self, test_client):
        _, app = test_client
        from app.utils.backup import restore_from_archive

        with app.app_context():
            with pytest.raises(ValueError):
                restore_from_archive(b"this is not a zip file")

            with pytest.raises(ValueError):
                restore_from_archive(b"")


# ---------------------------------------------------------------------------
# Round-trips
# ---------------------------------------------------------------------------


class TestRestoreRoundTrip:
    def test_database_round_trip(self, test_client):
        _, app = test_client
        archive = _build_backup(app)

        with app.app_context():
            from app.utils.backup import restore_from_archive

            # Corrupt the live DB: wipe case files + annexures.
            db.session.execute(db.delete(CaseFile))
            db.session.execute(db.delete(Annexure))
            db.session.commit()
            assert CaseFile.query.count() == 0

            stats = restore_from_archive(archive)

            assert stats["dialect"] == "sqlite"
            assert stats["tables"] >= 1
            assert CaseFile.query.count() == 1
            assert CaseFile.query.filter_by(case_number="BACKUP001").first() is not None
            # PKs are preserved, so the fixture ids survive.
            assert User.query.filter_by(username="admin").first() is not None

    def test_restore_aborts_on_schema_drift(self, test_client):
        """A backup missing a current table must abort without mutating data."""
        _, app = test_client
        with app.app_context():
            from app.utils.backup import _restore_database, dump_database

            snapshot = dump_database()
            stale_dump = {name: rows for name, rows in snapshot.items() if name != "case_files"}
            before = CaseFile.query.count()

            with pytest.raises(ValueError, match="predates current tables"):
                _restore_database(stale_dump)

            # The aborted restore must have left the DB untouched.
            assert CaseFile.query.count() == before
            assert CaseFile.query.filter_by(case_number="BACKUP001").first() is not None

    def test_instance_files_round_trip(self, test_client):
        _, app = test_client
        saved_dir = Path(app.instance_path) / "saved"
        saved_dir.mkdir(parents=True, exist_ok=True)
        sample_file = saved_dir / "backup_phase3_roundtrip.html"
        sample_file.write_text("<p>roundtrip</p>", encoding="utf-8")

        archive = _build_backup(app)
        sample_file.unlink()
        assert not sample_file.exists()

        with app.app_context():
            from app.utils.backup import restore_from_archive

            stats = restore_from_archive(archive)
            assert stats["files_restored"] >= 1

        assert sample_file.exists()
        assert sample_file.read_text(encoding="utf-8") == "<p>roundtrip</p>"
        sample_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# HTTP endpoints & admin gating
# ---------------------------------------------------------------------------


class TestBackupEndpoints:
    def test_backup_page_requires_admin(self, test_client):
        client, _ = test_client

        # Unauthenticated -> redirect to login.
        resp = client.get("/settings/backup", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

        # Regular user -> 403.
        _login_as(client, "regular")
        resp = client.get("/settings/backup", follow_redirects=False)
        assert resp.status_code == 403

        # Admin -> 200.
        _login_as(client, "admin")
        resp = client.get("/settings/backup", follow_redirects=False)
        assert resp.status_code == 200
        assert "Download Backup" in resp.data.decode("utf-8")

    def test_download_requires_admin(self, test_client):
        client, _ = test_client

        _login_as(client, "regular")
        resp = client.get("/settings/backup/download", follow_redirects=False)
        assert resp.status_code == 403

        _login_as(client, "admin")
        resp = client.get("/settings/backup/download", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        # Payload is a real ZIP.
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            assert "metadata.json" in zf.namelist()

    def test_restore_validates_upload(self, test_client):
        client, _ = test_client
        _login_as(client, "admin")

        # No file selected -> flash + redirect back to the backup page.
        resp = client.post(
            "/settings/backup/restore",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Invalid (non-zip) upload -> flash error + redirect.
        resp = client.post(
            "/settings/backup/restore",
            data={"file": (io.BytesIO(b"garbage"), "bad.zip")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_restore_endpoint_restores_data(self, test_client):
        client, app = test_client
        archive = _build_backup(app)

        # Wipe case files, then restore through the HTTP endpoint.
        with app.app_context():
            db.session.execute(db.delete(CaseFile))
            db.session.commit()
            assert CaseFile.query.count() == 0

        _login_as(client, "admin")
        resp = client.post(
            "/settings/backup/restore",
            data={"file": (io.BytesIO(archive), "nsa_backup.zip")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Restore complete" in resp.data.decode("utf-8")

        with app.app_context():
            assert CaseFile.query.count() == 1
            assert CaseFile.query.filter_by(case_number="BACKUP001").first() is not None

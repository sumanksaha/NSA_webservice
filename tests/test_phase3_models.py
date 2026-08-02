"""Unit tests for Phase 3 / Step 3 models: Settings, Annexure, Evidence, Version."""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import Annexure, Evidence, Settings, User, Version


@pytest.fixture
def test_db():
    """In-memory SQLite DB with all tables created."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()
        yield db
        db.session.remove()
        db.drop_all()


class TestSettingsModel:
    """Test the Settings key/value configuration model."""

    def test_create_string_setting(self, test_db):
        s = Settings(key="site_name", value="NSA Webservice")
        test_db.session.add(s)
        test_db.session.commit()
        assert s.key == "site_name"
        assert s.value == "NSA Webservice"
        assert s.value_type == "string"

    def test_create_int_setting(self, test_db):
        s = Settings(key="items_per_page", value="25", value_type="int")
        test_db.session.add(s)
        test_db.session.commit()
        assert Settings.get("items_per_page") == 25

    def test_create_float_setting(self, test_db):
        s = Settings(key="pdf_scale", value="1.5", value_type="float")
        test_db.session.add(s)
        test_db.session.commit()
        assert Settings.get("pdf_scale") == 1.5

    def test_create_bool_setting(self, test_db):
        s = Settings(key="debug_mode", value="true", value_type="bool")
        test_db.session.add(s)
        test_db.session.commit()
        assert Settings.get("debug_mode") is True

    def test_bool_setting_false(self, test_db):
        s = Settings(key="feature_x", value="0", value_type="bool")
        test_db.session.add(s)
        test_db.session.commit()
        assert Settings.get("feature_x") is False

    def test_create_json_setting(self, test_db):
        s = Settings(key="fso_config", value='{"timeout": 30}', value_type="json")
        test_db.session.add(s)
        test_db.session.commit()
        result = Settings.get("fso_config")
        assert result == {"timeout": 30}

    def test_get_returns_default_for_missing_key(self, test_db):
        assert Settings.get("nonexistent_key", default="fallback") == "fallback"

    def test_updated_at_set_on_create(self, test_db):
        s = Settings(key="test", value="val")
        test_db.session.add(s)
        test_db.session.commit()
        assert s.updated_at is not None


class TestAnnexureModel:
    """Test the Annexure document model."""

    def test_uuid_generated_on_init(self, test_db):
        a = Annexure(caption="Invoice", filepath="/tmp/invoice.pdf", filename="invoice.pdf", file_hash="abc123")
        assert a.id is not None
        assert len(a.id) == 36  # UUID string length

    def test_uuid_unique(self, test_db):
        a1 = Annexure(caption="Doc A", filepath="/tmp/a.pdf", filename="a.pdf", file_hash="hash_a")
        a2 = Annexure(caption="Doc B", filepath="/tmp/b.pdf", filename="b.pdf", file_hash="hash_b")
        assert a1.id != a2.id

    def test_required_fields(self, test_db):
        a = Annexure(caption="Invoice", filepath="/tmp/invoice.pdf", filename="invoice.pdf", file_hash="abc123")
        test_db.session.add(a)
        test_db.session.commit()
        assert a.caption == "Invoice"
        assert a.filepath == "/tmp/invoice.pdf"
        assert a.filename == "invoice.pdf"
        assert a.file_hash == "abc123"

    def test_optional_fields_nullable(self, test_db):
        a = Annexure(caption="Invoice", filepath="/tmp/invoice.pdf", filename="invoice.pdf", file_hash="abc123")
        test_db.session.add(a)
        test_db.session.commit()
        assert a.case_id is None
        assert a.adjudication_id is None
        assert a.page_count is None
        assert a.ocr_text is None
        assert a.tags is None
        assert a.mime_type is None
        assert a.annexure_letter is None

    def test_annexure_metadata_fields(self, test_db):
        a = Annexure(
            caption="Annexure A",
            filepath="/tmp/a.pdf",
            filename="a.pdf",
            file_hash="abc123",
            annexure_letter="A",
            page_count=10,
            tags="invoice,financial",
            mime_type="application/pdf",
            file_size=204800,
        )
        test_db.session.add(a)
        test_db.session.commit()
        assert a.annexure_letter == "A"
        assert a.page_count == 10
        assert a.tags == "invoice,financial"
        assert a.mime_type == "application/pdf"
        assert a.file_size == 204800

    def test_annexure_repr(self, test_db):
        a = Annexure(caption="Invoice", filepath="/tmp/invoice.pdf", filename="invoice.pdf", file_hash="abc123")
        assert "Annexure" in repr(a)
        assert "Invoice" in repr(a)


class TestEvidenceModel:
    """Test the Evidence model covering all evidence types."""

    def test_evidence_types_constant(self, test_db):
        assert Evidence.EVIDENCE_TYPES == ("photo", "video", "report", "licence", "bill", "lab_report")

    def test_create_photo_evidence(self, test_db):
        e = Evidence(
            evidence_type="photo",
            filepath="/tmp/photo.jpg",
            filename="photo.jpg",
            raw_lat=22.5726,
            raw_lng=88.3639,
            accuracy=5.0,
            verification_status="VERIFIED",
            stamped=True,
        )
        test_db.session.add(e)
        test_db.session.commit()
        assert e.evidence_type == "photo"
        assert e.raw_lat == 22.5726
        assert e.raw_lng == 88.3639
        assert e.stamped is True

    def test_create_video_evidence_photo_fields_nullable(self, test_db):
        e = Evidence(
            evidence_type="video",
            filepath="/tmp/video.mp4",
            filename="video.mp4",
            mime_type="video/mp4",
        )
        test_db.session.add(e)
        test_db.session.commit()
        assert e.evidence_type == "video"
        assert e.raw_lat is None
        assert e.raw_lng is None
        assert e.accuracy is None
        assert e.verification_status == "PENDING"
        assert e.stamped is False

    def test_create_all_evidence_types(self, test_db):
        for etype in Evidence.EVIDENCE_TYPES:
            e = Evidence(evidence_type=etype, filepath=f"/tmp/{etype}.bin", filename=f"{etype}.bin")
            test_db.session.add(e)
            test_db.session.commit()
        assert e.evidence_type == etype

    def test_evidence_uuid_generated(self, test_db):
        e = Evidence(evidence_type="photo", filepath="/tmp/photo.jpg", filename="photo.jpg")
        assert e.id is not None
        assert len(e.id) == 36

    def test_evidence_uuid_unique(self, test_db):
        e1 = Evidence(evidence_type="photo", filepath="/tmp/a.jpg", filename="a.jpg")
        e2 = Evidence(evidence_type="photo", filepath="/tmp/b.jpg", filename="b.jpg")
        assert e1.id != e2.id

    def test_evidence_repr(self, test_db):
        e = Evidence(evidence_type="photo", filepath="/tmp/photo.jpg", filename="photo.jpg")
        assert "Evidence" in repr(e)


class TestVersionModel:
    """Test the Version history model."""

    def test_create_version(self, test_db):
        v = Version(
            doc_type="petition",
            version_number=1,
            html_snapshot="<p>This is the HTML content</p>",
        )
        test_db.session.add(v)
        test_db.session.commit()
        assert v.doc_type == "petition"
        assert v.version_number == 1
        assert v.html_snapshot == "<p>This is the HTML content</p>"
        assert v.delta is None
        assert v.created_at is not None

    def test_version_with_delta(self, test_db):
        v = Version(
            doc_type="permission",
            version_number=2,
            html_snapshot="<p>Updated content</p>",
            delta='{"ops":[{"insert":"hello"}]}',
            created_by=1,
        )
        test_db.session.add(v)
        test_db.session.commit()
        assert v.delta == '{"ops":[{"insert":"hello"}]}'
        assert v.created_by == 1

    def test_version_created_at_defaults(self, test_db):
        v = Version(doc_type="petition", version_number=1, html_snapshot="<p>test</p>")
        test_db.session.add(v)
        test_db.session.commit()
        assert v.created_at is not None

    def test_version_repr(self, test_db):
        v = Version(doc_type="petition", version_number=3, html_snapshot="<p>test</p>")
        assert "petition" in repr(v)
        assert "3" in repr(v)




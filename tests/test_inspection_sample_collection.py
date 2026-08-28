"""Tests for Inspection sample collection functionality.

Covers:
- DB schema: sample_collected (Boolean) and sample_code (String 100)
- Create / update inspection with sample collection fields
- Validation: sample_code required when sample_collected is true,
  sample_code must match SL/WB/XXXXXX/XXXX/XXXXX format
- Work Diary engine includes sample_collected / sample_code
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.fixture()
def env():
    """App + logged-in client + in-memory schema with an FSO."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()

    user = User(username="sampleuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    yield app, client, ctx

    db.session.remove()
    db.drop_all()
    ctx.pop()


class TestSampleCollectionSchema:
    def test_inspection_has_sample_collected_column(self, env):
        from app.models import Inspection

        _app, _client, ctx = env
        insp = Inspection(
            inspection_code="T001",
            fso_name="Test Officer",
            fbo_name="FBO",
            inspection_date=datetime.now(UTC),
            compliance_deadline=datetime.now(UTC),
        )
        assert hasattr(insp, "sample_collected")
        assert insp.sample_collected is None

    def test_inspection_has_sample_code_column(self, env):
        from app.models import Inspection

        _app, _client, ctx = env
        insp = Inspection(
            inspection_code="T002",
            fso_name="Test Officer",
            fbo_name="FBO",
            inspection_date=datetime.now(UTC),
            compliance_deadline=datetime.now(UTC),
        )
        assert hasattr(insp, "sample_code")
        assert insp.sample_code is None


class TestCreateInspectionSampleCollection:
    def test_create_without_sample(self, env):
        """Create inspection without sample_collected works."""
        _app, client, ctx = env
        resp = client.post(
            "/inspection/create",
            data={
                "food_safety_officer_name": "Test Officer",
                "inspection_date": "2026-08-01",
                "concerned_food": "Milk",
                "problem": "No problem",
                "visit_purpose": "routine",
            },
        )
        assert resp.status_code in (200, 201)
        data = resp.get_json(silent=True) or {}
        assert data.get("message") == "Inspection created successfully"

    def test_create_with_sample_collected_and_code(self, env):
        """Create inspection with sample collected and valid code."""
        _app, client, ctx = env
        resp = client.post(
            "/inspection/create",
            data={
                "food_safety_officer_name": "Test Officer",
                "inspection_date": "2026-08-01",
                "concerned_food": "Milk",
                "problem": "No problem",
                "visit_purpose": "routine",
                "sample_collected": "on",
                "sample_code": "SL/WB/123456/7890/12345",
            },
        )
        assert resp.status_code in (200, 201)
        data = resp.get_json(silent=True) or {}
        assert data.get("message") == "Inspection created successfully"
        from app.models import Inspection

        insp = Inspection.query.filter_by(
            inspection_code=data.get("inspection_code")
        ).first()
        assert insp is not None
        assert insp.sample_collected is True
        assert insp.sample_code == "SL/WB/123456/7890/12345"

    def test_create_sample_collected_without_code(self, env):
        """Creating with sample_collected but no code returns 400."""
        _app, client, ctx = env
        resp = client.post(
            "/inspection/create",
            data={
                "food_safety_officer_name": "Test Officer",
                "inspection_date": "2026-08-01",
                "concerned_food": "Milk",
                "problem": "No problem",
                "visit_purpose": "routine",
                "sample_collected": "on",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json(silent=True) or {}
        assert "sample_code" in str(data.get("error", "")).lower()

    def test_create_sample_collected_invalid_code_format(self, env):
        """Creating with invalid sample code format returns 400."""
        _app, client, ctx = env
        resp = client.post(
            "/inspection/create",
            data={
                "food_safety_officer_name": "Test Officer",
                "inspection_date": "2026-08-01",
                "concerned_food": "Milk",
                "problem": "No problem",
                "visit_purpose": "routine",
                "sample_collected": "on",
                "sample_code": "INVALIDCODE",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json(silent=True) or {}
        assert "SL/WB" in str(data.get("error", ""))


class TestUpdateInspectionSampleCollection:
    def test_update_sample_collected_and_code(self, env):
        _app, client, ctx = env
        from app.extensions import db
        from app.models import Inspection

        insp = Inspection(
            inspection_code="T001",
            fso_name="Test Officer",
            fbo_name="FBO",
            inspection_date=datetime.now(UTC),
            compliance_deadline=datetime.now(UTC),
        )
        db.session.add(insp)
        db.session.commit()

        resp = client.put(
            f"/inspection/{insp.id}",
            data={
                "sample_collected": "on",
                "sample_code": "SL/WB/654321/0987/54321",
            },
        )
        assert resp.status_code in (200, 201)
        db.session.refresh(insp)
        assert insp.sample_collected is True
        assert insp.sample_code == "SL/WB/654321/0987/54321"

    def test_update_sample_collected_no_code(self, env):
        _app, client, ctx = env
        from app.extensions import db
        from app.models import Inspection

        insp = Inspection(
            inspection_code="T002",
            fso_name="Test Officer",
            fbo_name="FBO",
            inspection_date=datetime.now(UTC),
            compliance_deadline=datetime.now(UTC),
        )
        db.session.add(insp)
        db.session.commit()

        resp = client.put(
            f"/inspection/{insp.id}",
            data={"sample_collected": "on"},
        )
        assert resp.status_code == 400
        data = resp.get_json(silent=True) or {}
        assert "sample_code" in str(data.get("error", "")).lower()

    def test_update_invalid_sample_code(self, env):
        _app, client, ctx = env
        from app.extensions import db
        from app.models import Inspection

        insp = Inspection(
            inspection_code="T003",
            fso_name="Test Officer",
            fbo_name="FBO",
            inspection_date=datetime.now(UTC),
            compliance_deadline=datetime.now(UTC),
        )
        db.session.add(insp)
        db.session.commit()

        resp = client.put(
            f"/inspection/{insp.id}",
            data={
                "sample_collected": "on",
                "sample_code": "BAD",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json(silent=True) or {}
        assert "SL/WB" in str(data.get("error", ""))

    def test_update_uncheck_sample(self, env):
        _app, client, ctx = env
        from app.extensions import db
        from app.models import Inspection

        insp = Inspection(
            inspection_code="T004",
            fso_name="Test Officer",
            fbo_name="FBO",
            inspection_date=datetime.now(UTC),
            compliance_deadline=datetime.now(UTC),
            sample_collected=True,
            sample_code="SL/WB/111111/2222/33333",
        )
        db.session.add(insp)
        db.session.commit()

        resp = client.put(
            f"/inspection/{insp.id}",
            data={"sample_collected": "", "sample_code": ""},
        )
        assert resp.status_code in (200, 201)
        db.session.refresh(insp)
        assert insp.sample_collected is False
        assert insp.sample_code is None


class TestValidateSampleCode:
    def test_valid_codes(self, env):
        from app.inspection.routes.inspection_routes import validate_sample_code

        assert validate_sample_code("SL/WB/123456/7890/12345") is True
        assert validate_sample_code("SL/WB/000000/0000/00000") is True

    def test_invalid_codes(self, env):
        from app.inspection.routes.inspection_routes import validate_sample_code

        assert validate_sample_code("") is True
        assert validate_sample_code(None) is True
        assert validate_sample_code("INVALID") is False
        assert validate_sample_code("SL/WB/123/45/678") is False
        assert validate_sample_code("SL/WB/123456/7890/1234") is False


class TestWorkDiarySampleFields:
    def test_engine_includes_sample_fields(self, env):
        from app.extensions import db
        from app.models import Inspection
        from app.workdiary.engine import WorkDiaryEngine

        _app, _client, ctx = env
        insp = Inspection(
            inspection_code="T010",
            fso_name="Test Officer",
            fbo_name="FBO",
            inspection_date=datetime(2026, 8, 15),
            compliance_deadline=datetime(2026, 9, 14),
            sample_collected=True,
            sample_code="SL/WB/123456/7890/12345",
        )
        db.session.add(insp)
        db.session.commit()

        engine = WorkDiaryEngine()
        entries = engine.build_entries(
            fso_name="Test Officer",
            date_from="2026-08-01",
            date_to="2026-08-31",
        )
        assert len(entries) > 0
        entry = entries[0]
        assert entry.get("sample_collected") is True
        assert entry.get("sample_code") == "SL/WB/123456/7890/12345"
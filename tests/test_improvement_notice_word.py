"""Tests for the Improvement Notice Word (.docx) converter.

Covers:
- Document generation from a full context dict
- All text content is present in the .docx XML
- Empty violations/actions fallback text
- Missing optional fields handled gracefully
- Route: GET /food-cell/improvement-notice/inspection/<id>/docx returns valid docx
"""

from __future__ import annotations

import io
import zipfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_test_env():
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

    user = User(username="doctest", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    return app, client, ctx


def _teardown_test_env(ctx):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    ctx.pop()


def _base_ctx(**overrides):
    base = dict(
        fbo_name="ABC Foods Pvt Ltd",
        fbo_address="45 Park Street, Kolkata - 700016",
        inspection_date="15/01/2026",
        fbo_fssai="FSSAI-12345",
        fso_name="Soumitra Chatterjee",
        notice_date="26/08/2026",
        improvement_notice_ref="SMP-2026-001",
        violations=[
            {"title": "Unhygienic storage", "observation": "Food on floor."},
            {"title": "Pest issues", "observation": "Rodent droppings."},
        ],
        actions=["Clean area.", "Install platforms."],
        compliance_deadline="31/01/2026",
        enclosures=["Inspection report", "Photos"],
    )
    base.update(overrides)
    return base


def _extract_text(docx_bytes: bytes) -> str:
    """Extract all text from a .docx file for assertion checks."""
    zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
    xml = zf.read("word/document.xml").decode("utf-8")
    return xml


# ---------------------------------------------------------------------------
# Converter unit tests
# ---------------------------------------------------------------------------


class TestWordConverter:
    """Test ImprovementNoticeWordConverter.build() output."""

    def test_produces_valid_docx(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        docx = ImprovementNoticeWordConverter().build(_base_ctx())
        assert len(docx) > 1000
        zf = zipfile.ZipFile(io.BytesIO(docx))
        assert "word/document.xml" in zf.namelist()

    def test_letterhead_present(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "KOLKATA MUNICIPAL CORPORATION" in xml
        assert "Food Safety Department" in xml
        assert "Food Safety and Standards Act, 2006" in xml

    def test_subject_line(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "ABC Foods Pvt Ltd" in xml
        assert "45 Park Street, Kolkata - 700016" in xml
        assert "15/01/2026" in xml
        assert "Inspection report regarding an inspection of" in xml

    def test_violations_table(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "Unhygienic storage" in xml
        assert "Food on floor." in xml
        assert "Pest issues" in xml
        assert "Rodent droppings." in xml
        assert "Nature of Deviation" in xml
        assert "Observation" in xml

    def test_actions_list(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "Clean area." in xml
        assert "Install platforms." in xml

    def test_compliance_deadline(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "31/01/2026" in xml
        assert "on or before" in xml

    def test_enclosures(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "Inspection report" in xml
        assert "Photos" in xml

    def test_signature_block(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "Soumitra Chatterjee" in xml
        assert "Kolkata Municipal Corporation" in xml
        assert "26/08/2026" in xml

    def test_reference_badge(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "SMP-2026-001" in xml

    def test_section_headings(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(ImprovementNoticeWordConverter().build(_base_ctx()))
        assert "DETAILS OF INSPECTION" in xml
        assert "PART 1" in xml
        assert "PART 2" in xml
        assert "IMPROVEMENT NOTICE" in xml

    def test_empty_violations_fallback(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(
            ImprovementNoticeWordConverter().build(_base_ctx(violations=[]))
        )
        assert "No specific deviations were recorded" in xml

    def test_empty_actions_fallback(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(
            ImprovementNoticeWordConverter().build(_base_ctx(actions=[]))
        )
        assert "No specific remedial actions prescribed" in xml

    def test_no_compliance_deadline(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(
            ImprovementNoticeWordConverter().build(
                _base_ctx(compliance_deadline=None)
            )
        )
        assert "on or before" not in xml

    def test_no_enclosures(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(
            ImprovementNoticeWordConverter().build(_base_ctx(enclosures=[]))
        )
        assert "Enclosures:" not in xml

    def test_no_reference(self):
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        xml = _extract_text(
            ImprovementNoticeWordConverter().build(
                _base_ctx(improvement_notice_ref=None)
            )
        )
        assert "SMP-2026-001" not in xml

    def test_minimal_context(self):
        """Converter should not crash with minimal context."""
        from app.food_cell.word_converter import ImprovementNoticeWordConverter

        docx = ImprovementNoticeWordConverter().build({})
        assert len(docx) > 500
        zf = zipfile.ZipFile(io.BytesIO(docx))
        assert "word/document.xml" in zf.namelist()


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


def _make_inspection(client, **overrides):
    """Create a minimal inspection via the create route."""
    form = {
        "food_safety_officer_name": "Test Officer",
        "inspection_date": "2026-08-20",
        "compliance_deadline": "2026-09-20",
        "fbo_name": "Acme Foods",
    }
    form.update(overrides)
    resp = client.post("/inspection/create", data=form)
    assert resp.status_code == 201, resp.data
    return resp.get_json()["inspection_id"]


class TestWordRoute:
    def _setup(self):
        app, client, ctx = _setup_test_env()
        return app, client, ctx

    def _teardown(self, ctx):
        _teardown_test_env(ctx)

    def test_docx_returns_valid_docx(self):
        app, client, ctx = self._setup()
        try:
            form = {
                "food_safety_officer_name": "Test Officer",
                "inspection_date": "2026-08-20",
                "compliance_deadline": "2026-09-20",
                "fbo_name": "Acme Foods",
                "clean_premise": "no",
                "license_display": "no",
            }
            insp_id = _make_inspection(client, **form)
            resp = client.get(
                f"/food-cell/improvement-notice/inspection/{insp_id}/docx"
            )
            assert resp.status_code == 200
            assert resp.data[:4] == b"PK\x03\x04"  # ZIP header (docx = zip)
            assert (
                resp.headers["Content-Type"]
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
        finally:
            self._teardown(ctx)

    def test_docx_correct_filename(self):
        app, client, ctx = self._setup()
        try:
            form = {
                "food_safety_officer_name": "Test Officer",
                "inspection_date": "2026-08-20",
                "compliance_deadline": "2026-09-20",
                "fbo_name": "Acme Foods",
                "clean_premise": "no",
                "license_display": "no",
            }
            insp_id = _make_inspection(client, **form)
            resp = client.get(
                f"/food-cell/improvement-notice/inspection/{insp_id}/docx"
            )
            assert resp.status_code == 200
            cd = resp.headers.get("Content-Disposition", "")
            assert f"Improvement_Notice_{insp_id}.docx" in cd
        finally:
            self._teardown(ctx)

    def test_docx_404_for_missing_inspection(self):
        app, client, ctx = self._setup()
        try:
            resp = client.get(
                "/food-cell/improvement-notice/inspection/99999/docx"
            )
            assert resp.status_code == 404
        finally:
            self._teardown(ctx)

    def test_docx_400_without_violations(self):
        app, client, ctx = self._setup()
        try:
            insp_id = _make_inspection(client)
            resp = client.get(
                f"/food-cell/improvement-notice/inspection/{insp_id}/docx"
            )
            assert resp.status_code == 400
        finally:
            self._teardown(ctx)

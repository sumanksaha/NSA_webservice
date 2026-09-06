"""Tests for the Food Cell Improvement Notice (u/s 32, FSS Act) — inspection-keyed.

Improvement Notices are always keyed to an *Inspection* (never a Sample);
the old sample-keyed routes were replaced in 5443c1e and the renderer
context now maps Inspection fields (b216b44 added report mode for
violation-free inspections).

Covers:
- Renderer context mapping from an Inspection (fbo/fso/ref/date fields)
- HTML template rendering: letterhead, recipient, subject, findings table,
  Part-2 grounds/actions, compliance callout, enclosures, signatory block
- Report mode: violation-free inspections render the "Inspection Report"
  badge and omit the u/s 32 Part-2 grounds
- Routes under /food-cell/improvement-notice/inspection/<id>/:
  html (200 + freeze on violations, no freeze without), pdf (valid PDF,
  correct filename), docx (valid ZIP/docx), 404s
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

# --------------------------------------------------------------------------- #
# Test data
# --------------------------------------------------------------------------- #

VIOLATING_CHECKLIST = {"clean_premise": "no", "license_display": "no", "Pest_report": "no"}

SAMPLE_VIOLATIONS = [
    {
        "title": "Unclean Premises",
        "observation": "The premises were found inadequately maintained and unhygienic.",
    },
    {
        "title": "Improper License Display",
        "observation": "License not prominently displayed.",
    },
]

SAMPLE_ACTIONS = [
    "Take corrective action: Unclean Premises — The premises were found inadequately maintained and unhygienic.",
    "Take corrective action: Improper License Display — License not prominently displayed.",
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _setup_test_env():
    """Create a test app with a user and an FSO, and return (app, client, ctx)."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="inctest", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    return app, client, app_context


def _teardown_test_env(app_context):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    app_context.pop()


def _make_inspection(client, **kwargs):
    """Create an Inspection via the public create route; return its id."""
    form = {
        "food_safety_officer_name": "Test Officer",
        "inspection_date": "2026-08-20",
        "compliance_deadline": "2026-09-20",
        "fbo_name": "Acme Foods",
        "fbo_address": "45 Park Street, Kolkata - 700016",
        "fssai_license": "FSSAI-12345",
    }
    form.update(kwargs)
    resp = client.post("/inspection/create", data=form)
    assert resp.status_code == 201, resp.data
    return resp.get_json()["inspection_id"]


def _violating_inspection(client, **kwargs):
    """A violating inspection; per-test kwargs override VIOLATING_CHECKLIST."""
    form = dict(VIOLATING_CHECKLIST)
    form.update(kwargs)
    return _make_inspection(client, **form)


def _norm(html: str) -> str:
    """Collapse all whitespace runs to single spaces (template source wraps
    sentences across lines, so text assertions must be whitespace-insensitive)."""
    import re

    return re.sub(r"\s+", " ", html)


def _clean_instance(app):
    """Remove the instance food_cell dir (best-effort) before/after each test."""
    from pathlib import Path

    d = Path(app.instance_path) / "food_cell"
    if d.exists():
        import shutil

        shutil.rmtree(d)


def _notice_url(inspection_id: int, fmt: str) -> str:
    return f"/food-cell/improvement-notice/inspection/{inspection_id}/{fmt}"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def env():
    app, client, app_context = _setup_test_env()
    _clean_instance(app)
    yield app, client, app_context
    _clean_instance(app)
    _teardown_test_env(app_context)


@pytest.fixture()
def app(env):
    app, _client, _ctx = env
    return app


@pytest.fixture()
def client(env):
    _app, client, _ctx = env
    return client


@pytest.fixture()
def inspection(client):
    """A violating inspection (violations derivable from its checklist)."""
    return _violating_inspection(client)


@pytest.fixture()
def plain_inspection(client):
    """A violation-free inspection (no checklist)."""
    return _make_inspection(client)


def _notice_issued_at(app, inspection_id):
    from app.extensions import db
    from app.models.inspection import Inspection

    with app.app_context():
        insp = db.session.get(Inspection, inspection_id)
        return insp.notice_issued_at if insp is not None else None


# --------------------------------------------------------------------------- #
# Renderer context tests
# --------------------------------------------------------------------------- #


class TestImprovementNoticeRendererContext:
    """DODocumentRenderer.build_improvement_notice_context maps Inspection fields."""

    def _make_model(self):
        from app.models.inspection import Inspection

        return Inspection(
            inspection_code="INSP-2026-0001",
            fso_name="Test Officer",
            fssai_license="FSSAI-12345",
            fbo_name="ABC Foods Pvt Ltd",
            fbo_address="45 Park Street, Kolkata - 700016",
            inspection_date=datetime(2026, 1, 15, 10, 0),
        )

    def test_field_mapping(self, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            ctx = DODocumentRenderer().build_improvement_notice_context(self._make_model())

        assert ctx["fbo_name"] == "ABC Foods Pvt Ltd"
        assert ctx["fbo_address"] == "45 Park Street, Kolkata - 700016"
        assert ctx["fbo_fssai"] == "FSSAI-12345"
        assert ctx["fso_name"] == "Test Officer"
        assert ctx["inspection_date"] == "15/01/2026"
        # Ref is the inspection code (never a sample code)
        assert ctx["improvement_notice_ref"] == "INSP-2026-0001"
        assert "notice_date" in ctx
        assert ctx["violations"] == []
        assert ctx["actions"] == []
        assert ctx["enclosures"] == []
        assert ctx["is_inspection_report"] is False

    def test_violations_and_actions_passthrough(self, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            ctx = DODocumentRenderer().build_improvement_notice_context(
                self._make_model(),
                violations=SAMPLE_VIOLATIONS,
                actions=SAMPLE_ACTIONS,
                compliance_deadline="31/01/2026",
                is_inspection_report=False,
            )

        assert ctx["violations"] == SAMPLE_VIOLATIONS
        assert ctx["actions"] == SAMPLE_ACTIONS
        assert ctx["compliance_deadline"] == "31/01/2026"

    def test_report_mode_flag(self, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            ctx = DODocumentRenderer().build_improvement_notice_context(
                self._make_model(), is_inspection_report=True
            )

        assert ctx["is_inspection_report"] is True


# --------------------------------------------------------------------------- #
# Template-level tests (render with explicit context — no database needed)
# --------------------------------------------------------------------------- #


class TestImprovementNoticeTemplate:
    """Render the template directly with a context dict and assert content."""

    def _ctx(self, **overrides):
        """Context mirroring build_improvement_notice_context output."""
        base = dict(
            fbo_name="ABC Foods Pvt Ltd",
            fbo_address="45 Park Street, Kolkata - 700016",
            inspection_date="15/01/2026",
            fbo_fssai="FSSAI-12345",
            fso_name="Test Officer",
            notice_date="26/08/2026",
            improvement_notice_ref="INSP-2026-0001",
            violations=SAMPLE_VIOLATIONS,
            actions=SAMPLE_ACTIONS,
            compliance_deadline="31/01/2026",
            enclosures=["Inspection report", "Photographic evidence"],
            is_inspection_report=False,
        )
        base.update(overrides)
        return base

    def _render(self, app, **overrides):
        from flask import render_template

        with app.app_context():
            return _norm(render_template("food_cell/improvement_notice.html", **self._ctx(**overrides)))

    # -- letterhead / recipient ------------------------------------------ #

    def test_letterhead_present(self, app):
        html = self._render(app)
        assert "Kolkata Municipal Corporation" in html
        assert "Food Safety Department" in html
        assert "Food Safety and Standards Act, 2006" in html

    def test_notice_badge(self, app):
        html = self._render(app)
        assert "Improvement Notice — Section 32, FSS Act, 2006" in html
        assert "Inspection Report — Food Safety Department, KMC" not in html

    def test_recipient_block(self, app):
        html = self._render(app)
        assert "The Designated Officer" in html
        assert "Food Cell" in html
        assert "Kolkata Municipal Corporation" in html

    def test_subject_line(self, app):
        html = self._render(app)
        assert (
            "Inspection report regarding an inspection of ABC Foods Pvt Ltd "
            "situated at 45 Park Street, Kolkata - 700016 on 15/01/2026" in html
        )

    def test_ref_badge_shows_inspection_code(self, app):
        html = self._render(app)
        assert "INSP-2026-0001" in html

    # -- inspection details + findings ------------------------------------ #

    def test_details_table(self, app):
        html = self._render(app)
        assert "FBO Name" in html
        assert "ABC Foods Pvt Ltd" in html
        assert "FSSAI License No." in html
        assert "FSSAI-12345" in html
        assert "Inspection Date" in html

    def test_part1_intro_and_violations_table(self, app):
        html = self._render(app)
        assert "An inspection was performed at" in html
        assert "and the following deviation was observed." in html
        assert "Nature of Deviation" in html
        for v in SAMPLE_VIOLATIONS:
            assert v["title"] in html
            assert v["observation"] in html

    # -- Part 2 grounds + actions ----------------------------------------- #

    def test_part2_grounds_and_actions(self, app):
        html = self._render(app)
        assert "improvement notice u/s 32" in html
        assert "may kindly be granted on the following ground" in html
        for action in SAMPLE_ACTIONS:
            assert action in html

    # -- optional blocks --------------------------------------------------- #

    def test_compliance_deadline_callout(self, app):
        html = self._render(app)
        assert "on or before" in html
        assert "31/01/2026" in html

    def test_no_compliance_deadline_section(self, app):
        html = self._render(app, compliance_deadline=None)
        assert "on or before" not in html

    def test_enclosures_list(self, app):
        html = self._render(app)
        assert "Enclosures:" in html
        assert "Inspection report" in html

    def test_no_enclosures_section(self, app):
        html = self._render(app, enclosures=[])
        assert "Enclosures:" not in html

    def test_empty_violations_fallback(self, app):
        html = self._render(app, violations=[])
        assert "No specific deviations were recorded" in html

    def test_empty_actions_fallback(self, app):
        html = self._render(app, actions=[])
        assert "No specific remedial actions prescribed" in html

    # -- signatory ---------------------------------------------------------- #

    def test_signatory_block(self, app):
        html = self._render(app)
        assert "Issued by" in html
        assert "Food Safety Officer" in html
        assert "Kolkata Municipal Corporation" in html
        assert "26/08/2026" in html

    # -- report mode -------------------------------------------------------- #

    def test_report_mode_badge_and_no_part2(self, app):
        html = self._render(app, is_inspection_report=True)
        assert "Inspection Report — Food Safety Department, KMC" in html
        assert "Improvement Notice — Section 32" not in html
        # u/s 32 grounds block omitted entirely
        assert "improvement notice u/s 32" not in html


# --------------------------------------------------------------------------- #
# Route tests — html / pdf / docx
# --------------------------------------------------------------------------- #


class TestImprovementNoticeRoutes:
    def test_view_html_renders_notice(self, inspection, client):
        resp = client.get(_notice_url(inspection, "html"))
        assert resp.status_code == 200
        body = _norm(resp.data.decode())
        assert "Improvement Notice — Section 32" in body
        assert "An inspection was performed at" in body
        assert "Acme Foods" in body
        # Derived from the checklist, in deterministic rule order
        assert "Unclean Premises" in body
        assert "Improper License Display" in body

    def test_view_html_subject_contains_fbo_details(self, inspection, client):
        resp = client.get(_notice_url(inspection, "html"))
        body = _norm(resp.data.decode())
        assert "Inspection report regarding an inspection of Acme Foods" in body
        assert "45 Park Street, Kolkata - 700016" in body

    def test_view_html_404_for_missing_inspection(self, client):
        resp = client.get(_notice_url(999999, "html"))
        assert resp.status_code == 404

    def test_first_html_render_freezes_inspection(self, app, inspection, client):
        assert _notice_issued_at(app, inspection) is None
        client.get(_notice_url(inspection, "html"))
        assert _notice_issued_at(app, inspection) is not None

    def test_pdf_download_serves_pdf(self, inspection, client):
        resp = client.get(_notice_url(inspection, "pdf"))
        assert resp.status_code == 200
        assert resp.data[:5] == b"%PDF-"

    def test_pdf_correct_filename(self, inspection, client):
        resp = client.get(_notice_url(inspection, "pdf"))
        cd = resp.headers.get("Content-Disposition", "")
        assert f"Improvement_Notice_{inspection}.pdf" in cd

    def test_pdf_404_for_missing_inspection(self, client):
        resp = client.get(_notice_url(999999, "pdf"))
        assert resp.status_code == 404

    def test_pdf_render_freezes_inspection(self, app, inspection, client):
        client.get(_notice_url(inspection, "pdf"))
        assert _notice_issued_at(app, inspection) is not None

    def test_docx_download_serves_docx(self, inspection, client):
        resp = client.get(_notice_url(inspection, "docx"))
        assert resp.status_code == 200
        assert resp.data[:4] == b"PK\x03\x04"  # ZIP header (docx = zip)
        assert (
            resp.headers["Content-Type"]
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_docx_correct_filename(self, inspection, client):
        resp = client.get(_notice_url(inspection, "docx"))
        cd = resp.headers.get("Content-Disposition", "")
        assert f"Improvement_Notice_{inspection}.docx" in cd

    def test_docx_404_for_missing_inspection(self, client):
        resp = client.get(_notice_url(999999, "docx"))
        assert resp.status_code == 404

    def test_report_mode_html_renders_inspection_report(self, app, plain_inspection, client):
        resp = client.get(_notice_url(plain_inspection, "html"))
        assert resp.status_code == 200
        body = _norm(resp.data.decode())
        assert "Inspection Report — Food Safety Department, KMC" in body
        assert "Improvement Notice — Section 32" not in body
        assert "improvement notice u/s 32" not in body

    def test_report_mode_does_not_freeze(self, app, plain_inspection, client):
        client.get(_notice_url(plain_inspection, "html"))
        assert _notice_issued_at(app, plain_inspection) is None


# --------------------------------------------------------------------------- #
# Route ↔ checklist integration
# --------------------------------------------------------------------------- #


class TestViolationsDerivationFromChecklist:
    """The routes derive violations/actions from the stored checklist JSON."""

    def test_all_compliant_renders_report(self, client):
        # Semantics of derive_violations: a "no" on any of the 10 regular
        # checklist fields is a violation, and a "yes" on the two special
        # fields (artificial_colour, Expired_item) is a violation. So a fully
        # compliant form sets regular fields to "yes" and leaves the special
        # ones unset (only non-empty form values are stored).
        # NOTE: Expired_item is currently listed in BOTH rule tables, so any
        # *stored* value ("yes" or "no") yields a violation — known quirk,
        # not exercised here.
        regular = (
            "clean_premise",
            "refrigerator_clean",
            "proper_attire",
            "proper_covered_utensil",
            "date_tag",
            "veg_nonveg_separation",
            "food_segregation",
            "license_display",
            "Pest_report",
            "Water_report",
        )
        compliant = {field: "yes" for field in regular}
        insp_id = _make_inspection(client, **compliant)
        resp = client.get(_notice_url(insp_id, "html"))
        assert resp.status_code == 200
        body = _norm(resp.data.decode())
        assert "Inspection Report — Food Safety Department, KMC" in body
        assert "Nature of Deviation" not in body

    def test_single_violation_appears_once(self, client):
        insp_id = _violating_inspection(client, clean_premise="no", license_display="yes")
        body = _norm(client.get(_notice_url(insp_id, "html")).data.decode())
        assert "Unclean Premises" in body
        assert "Improper License Display" not in body

    def test_corrupt_checklist_json_degrades_to_report(self, app, client):
        """A corrupt checklist_json blob degrades to report mode, not a 500."""
        from app.extensions import db
        from app.models.inspection import Inspection

        insp_id = _violating_inspection(client)
        with app.app_context():
            insp = db.session.get(Inspection, insp_id)
            insp.checklist_json = "{not-json"
            db.session.commit()

        resp = client.get(_notice_url(insp_id, "html"))
        assert resp.status_code == 200
        body = _norm(resp.data.decode())
        assert "Inspection Report — Food Safety Department, KMC" in body

    def test_derive_actions_roundtrip_through_route(self, client):
        """Actions shown on the notice are derived 1:1 from violations."""
        insp_id = _violating_inspection(client)
        body = client.get(_notice_url(insp_id, "html")).data.decode()
        for v in SAMPLE_VIOLATIONS:
            assert f"Take corrective action: {v['title']}" in body

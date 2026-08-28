"""Tests for the Food Cell Improvement Notice template (u/s 32, FSS Act).

Covers:
- HTML template rendering with sample context variables (FBO name, address,
  inspection date, notice date, FSO name)
- Subject line contains the exact prescribed text
- Part-1 intro text: "An inspection was performed at ..."
- Violations table (Sl. / Nature of Deviation / Observation)
- Part-2 intro text: "Based on the following observations, an improvement
  notice u/s 32 may kindly be granted on the following ground:"
- Actions list rendering
- FSO signatory block
- Graceful empty fallbacks for violations and actions
- Letterhead + recipient ("The Designated Officer, Food Cell, KMC")
- Renderer context-building from a Sample object
- PDF generation via the renderer
- Routes: HTML view (200), PDF download (200), 404 for missing sample,
  violations/actions via query parameters
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

# --------------------------------------------------------------------------- #
# Test data
# --------------------------------------------------------------------------- #

SAMPLE_VIOLATIONS = [
    {
        "title": "Unhygienic storage of food articles",
        "observation": "Food articles stored directly on the floor without an elevated platform.",
    },
    {
        "title": "Pest control issues",
        "observation": "Rodent droppings and gnaw marks seen in the storage area.",
    },
    {
        "title": "Expired stock on sale",
        "observation": "Expired packaged items found displayed for sale with no batch-wise segregation.",
    },
]

SAMPLE_ACTIONS = [
    "Immediately clean and sanitize the entire food storage area using food-grade disinfectants.",
    "Install rodent-proof storage platforms and engage a certified pest-control operator within 7 days.",
    "Remove all expired stock from the sales floor and submit a disposal certificate within 5 days.",
    "File a written compliance report signed by the FBO with the Food Safety Officer within 15 days.",
]


# --------------------------------------------------------------------------- #
# Helpers (mirror the do_intimation test helpers for consistency)
# --------------------------------------------------------------------------- #


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and an FSO."""
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


def _make_sample(**kwargs):
    """Create and persist a minimal Sample for testing."""
    from app.extensions import db
    from app.models import Sample

    defaults = dict(
        sample_code=f"SMP-INC-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        sample_name="Test Food Item",
        sample_type="enforcement",
        fso_name="Test Officer",
        collection_date=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        retailer_fssai="FSSAI-12345",
        retailer_name="Test Retailer Pvt Ltd",
        manufacturer_details="123 Food Street, Kolkata - 700001",
        price="1500",
        nature_of_food="Bakery",
        batch_no="BATCH-001",
    )
    defaults.update(kwargs)
    sample = Sample(**defaults)
    db.session.add(sample)
    db.session.commit()
    return sample


def _clean_instance(app):
    """Remove the instance food_cell dir (best-effort) before/after each test."""
    from pathlib import Path

    d = Path(app.instance_path) / "food_cell"
    if d.exists():
        import shutil

        shutil.rmtree(d)


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
def sample(env):
    _app, _client, _ctx = env
    return _make_sample()


@pytest.fixture()
def app(env):
    app, _client, _ctx = env
    return app


@pytest.fixture()
def client(env):
    _app, client, _ctx = env
    return client


# --------------------------------------------------------------------------- #
# Template-level tests (render with explicit context — no database needed)
# --------------------------------------------------------------------------- #


class TestImprovementNoticeTemplate:
    """Render the template directly with context dict and assert content."""

    def _ctx(self, **overrides):
        """Default context that mirrors what build_improvement_notice_context
        produces, with sample violations and actions."""
        base = dict(
            fbo_name="ABC Foods Pvt Ltd",
            fbo_address="45 Park Street, Kolkata - 700016",
            inspection_date="15/01/2026",
            fbo_fssai="FSSAI-12345",
            fso_name="Soumitra Chatterjee",
            notice_date="26/08/2026",
            improvement_notice_ref="SMP-2026-001",
            violations=SAMPLE_VIOLATIONS,
            actions=SAMPLE_ACTIONS,
            compliance_deadline="31/01/2026",
            enclosures=["Inspection report", "Photographic evidence"],
        )
        base.update(overrides)
        return base

    def test_subject_line_contains_fbo_name_address_date(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert "ABC Foods Pvt Ltd" in html
        assert "45 Park Street, Kolkata - 700016" in html
        assert "15/01/2026" in html
        assert "Inspection report regarding an inspection of" in html

    def test_full_subject_text(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert (
            "Inspection report regarding an inspection of ABC Foods Pvt Ltd "
            "situated at 45 Park Street, Kolkata - 700016 on 15/01/2026" in html
        )

    def test_first_part_intro_text(self, env):
        """Verify the exact Part-1 intro sentence."""
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert "An inspection was performed at" in html
        assert "and the following deviation was observed." in html
        assert "45 Park Street, Kolkata - 700016" in html
        assert "15/01/2026" in html

    def test_violations_table_rendered(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert "Nature of Deviation" in html
        assert "Observation" in html
        for v in SAMPLE_VIOLATIONS:
            assert v["title"] in html
            assert v["observation"] in html

    def test_sl_numbers_in_violations_table(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        for i in range(1, len(SAMPLE_VIOLATIONS) + 1):
            assert str(i) in html

    def test_second_part_u_s_32_text(self, env):
        """Verify the Part-2 intro mentioning u/s 32."""
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert "improvement notice u/s 32" in html
        assert "may kindly be granted on the following ground" in html

    def test_actions_list_rendered(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        for action in SAMPLE_ACTIONS:
            assert action in html

    def test_fso_signatory_block(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert "Food Safety Officer" in html
        assert "Soumitra Chatterjee" in html
        assert "Kolkata Municipal Corporation" in html
        assert "26/08/2026" in html  # notice_date in signature

    def test_letterhead_present(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert "Kolkata Municipal Corporation" in html
        assert "Food Safety Department" in html
        assert "Food Safety and Standards Act, 2006" in html

    def test_recipient_block(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx())
        assert "The Designated Officer" in html
        assert "Food Cell" in html
        assert "Kolkata Municipal Corporation" in html

    def test_notice_date_inserted(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template("food_cell/improvement_notice.html", **self._ctx(notice_date="01/09/2026"))
        assert "01/09/2026" in html

    def test_improvement_notice_ref_in_header(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template(
                "food_cell/improvement_notice.html",
                **self._ctx(improvement_notice_ref="IN/2026/0042"),
            )
        assert "IN/2026/0042" in html

    def test_compliance_deadline_shown(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template(
                "food_cell/improvement_notice.html",
                **self._ctx(compliance_deadline="15/02/2026"),
            )
        assert "15/02/2026" in html
        assert "on or before" in html

    def test_enclosures_list_rendered(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template(
                "food_cell/improvement_notice.html",
                **self._ctx(enclosures=["Inspection photos", "Lab report"]),
            )
        assert "Inspection photos" in html
        assert "Lab report" in html

    def test_empty_violations_fallback(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template(
                "food_cell/improvement_notice.html",
                **self._ctx(violations=[]),
            )
        assert "No specific deviations were recorded" in html

    def test_empty_actions_fallback(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template(
                "food_cell/improvement_notice.html",
                **self._ctx(actions=[]),
            )
        assert "No specific remedial actions prescribed" in html

    def test_no_compliance_deadline_section(self, env):
        """When compliance_deadline is None the section should be absent."""
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template(
                "food_cell/improvement_notice.html",
                **self._ctx(compliance_deadline=None),
            )
        assert "on or before" not in html

    def test_no_enclosures_section(self, env):
        from flask import render_template

        app, _, _ = env
        with app.app_context():
            html = render_template(
                "food_cell/improvement_notice.html",
                **self._ctx(enclosures=[]),
            )
        assert "Enclosures:" not in html


# --------------------------------------------------------------------------- #
# Renderer tests
# --------------------------------------------------------------------------- #


class TestImprovementNoticeRenderer:
    """Test DODocumentRenderer improvement-notice methods."""

    def test_build_context_from_sample(self, sample, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            renderer = DODocumentRenderer()
            ctx = renderer.build_improvement_notice_context(sample)

        assert ctx["fbo_name"] == sample.retailer_name
        assert ctx["fbo_address"] == sample.manufacturer_details
        assert ctx["inspection_date"] == "15/01/2026"
        assert ctx["fbo_fssai"] == sample.retailer_fssai
        assert ctx["fso_name"] == sample.fso_name
        assert ctx["improvement_notice_ref"] == sample.sample_code
        assert "notice_date" in ctx
        assert ctx["violations"] == []
        assert ctx["actions"] == []

    def test_build_context_with_violations_and_actions(self, sample, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            renderer = DODocumentRenderer()
            ctx = renderer.build_improvement_notice_context(
                sample,
                violations=SAMPLE_VIOLATIONS,
                actions=SAMPLE_ACTIONS,
            )

        assert ctx["violations"] == SAMPLE_VIOLATIONS
        assert ctx["actions"] == SAMPLE_ACTIONS

    def test_render_html_from_sample(self, sample, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            renderer = DODocumentRenderer()
            html = renderer.render_improvement_notice_html(sample)

        assert "<!DOCTYPE html>" in html
        assert sample.retailer_name in html
        assert sample.manufacturer_details in html
        assert "An inspection was performed at" in html
        assert "improvement notice u/s 32" in html

    def test_render_html_with_violations_and_actions(self, sample, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            renderer = DODocumentRenderer()
            html = renderer.render_improvement_notice_html(
                sample,
                violations=SAMPLE_VIOLATIONS,
                actions=SAMPLE_ACTIONS,
            )

        for v in SAMPLE_VIOLATIONS:
            assert v["title"] in html
            assert v["observation"] in html
        for a in SAMPLE_ACTIONS:
            assert a in html

    def test_render_pdf_creates_file(self, sample, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            renderer = DODocumentRenderer()
            html = renderer.render_improvement_notice_html(sample)
            pdf_path = renderer.render_improvement_notice_pdf(html, sample)

        assert pdf_path.endswith(".pdf")
        assert os.path.basename(pdf_path).startswith("improvement_notice_")
        assert os.path.isfile(pdf_path)
        with open(pdf_path, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"


# --------------------------------------------------------------------------- #
# Route tests
# --------------------------------------------------------------------------- #


class TestImprovementNoticeRoutes:
    def test_view_html_returns_200(self, sample, client):
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/html")
        assert resp.status_code == 200
        assert b"improvement notice u/s 32" in resp.data
        assert b"An inspection was performed at" in resp.data

    def test_view_html_contains_fbo_name(self, sample, client):
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/html")
        assert resp.status_code == 200
        assert sample.retailer_name.encode() in resp.data
        assert sample.manufacturer_details.encode() in resp.data

    def test_view_html_returns_subject(self, sample, client):
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/html")
        body = resp.data.decode()
        assert "Inspection report regarding an inspection of" in body
        assert sample.retailer_name in body
        assert sample.manufacturer_details in body

    def test_view_html_404_for_missing_sample(self, client):
        resp = client.get("/food-cell/improvement-notice/999999/html")
        assert resp.status_code == 404

    def test_download_pdf_returns_pdf(self, sample, client):
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/pdf")
        assert resp.status_code == 200
        assert b"%PDF" in resp.data

    def test_download_pdf_correct_filename(self, sample, client):
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/pdf")
        assert resp.status_code == 200
        cd = resp.headers.get("Content-Disposition", "")
        assert f"Improvement_Notice_{sample.id}.pdf" in cd

    def test_download_pdf_404_for_missing_sample(self, client):
        resp = client.get("/food-cell/improvement-notice/999999/pdf")
        assert resp.status_code == 404

    def test_violations_via_query_params(self, sample, client):
        raw = json.dumps(SAMPLE_VIOLATIONS)
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/html?violations={raw}")
        assert resp.status_code == 200
        body = resp.data.decode()
        for v in SAMPLE_VIOLATIONS:
            assert v["title"] in body
            assert v["observation"] in body

    def test_actions_via_query_params(self, sample, client):
        raw = json.dumps(SAMPLE_ACTIONS)
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/html?actions={raw}")
        assert resp.status_code == 200
        body = resp.data.decode()
        for action in SAMPLE_ACTIONS:
            assert action in body

    def test_both_violations_and_actions_via_query_params(self, sample, client):
        resp = client.get(
            f"/food-cell/improvement-notice/{sample.id}/html"
            f"?violations={json.dumps(SAMPLE_VIOLATIONS)}"
            f"&actions={json.dumps(SAMPLE_ACTIONS)}"
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Nature of Deviation" in body
        for action in SAMPLE_ACTIONS:
            assert action in body

    def test_no_violations_shows_fallback(self, sample, client):
        """Without query params the violations table should show the empty
        fallback."""
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/html")
        assert resp.status_code == 200
        assert b"No specific deviations were recorded" in resp.data

    def test_no_actions_shows_fallback(self, sample, client):
        resp = client.get(f"/food-cell/improvement-notice/{sample.id}/html")
        assert resp.status_code == 200
        assert b"No specific remedial actions prescribed" in resp.data

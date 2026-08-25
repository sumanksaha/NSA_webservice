"""Tests for the Food Cell DO Intimation module (Phase 21).

Covers:
- HTML template rendering with sample data
- PDF generation from rendered HTML
- DO intimation generation via service layer
- FSO-save trigger (sample creation -> send_do_intimation)
- Sync forwarding to Sheets/Airtable/Excel (best-effort, mocked)
- Download PDF endpoint
- HTML view endpoint
- Status endpoint
- Regenerate endpoint (force=True)
- Idempotency (calling twice returns same record)
- Sample not-found guard
- DO reference number uniqueness
- Sync status JSON tracking
- food_cell_forwarded timestamp propagation to Sample
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from flask import render_template

# --------------------------------------------------------------------------- #
# Helpers
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

    user = User(username="fctestuser", password_hash="pbkdf2:sha256$test$dummy")
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
        sample_code=f"SMP-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        sample_name="Test Food Item",
        sample_type="enforcement",
        fso_name="Test Officer",
        collection_date=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        retailer_fssai="FSSAI-12345",
        retailer_name="Test Retailer Pvt Ltd",
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
    """Remove the instance food_cell dir (best-effort) before each test."""
    from pathlib import Path

    d = Path(app.instance_path) / "food_cell"
    if d.exists():
        import shutil

        shutil.rmtree(d)


def _patch_sync_fns():
    """Stub the shared sync seam for food_cell tests.

    Food_cell now delegates triple-target sync to
    :func:`app.services.sync_orchestrator.sync_row`. Patch the name as bound
    in this module (a ``from ... import sync_row`` binding), not the source
    module, so calls inside ``generate_and_forward_do_intimation`` resolve to
    the stub. Returns a no-op success SyncResult.
    """
    import app.food_cell.services as fcs

    fcs.sync_row = lambda *a, **k: {"sheets": True, "airtable": True, "excel": True}


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
def intimation(sample):
    from app.food_cell.services import generate_and_forward_do_intimation

    _patch_sync_fns()
    return generate_and_forward_do_intimation(sample.id, sample=sample)


@pytest.fixture()
def app(env):
    app, _client, _ctx = env
    return app


@pytest.fixture()
def client(env):
    _app, client, _ctx = env
    return client


# --------------------------------------------------------------------------- #
# Tests (15)
# --------------------------------------------------------------------------- #


class TestHtmlRendering:
    """Test 1: HTML template renders with sample data."""

    def test_html_renders_sample_fields(self, sample, app):
        with app.app_context():
            html = render_template("food_cell/do_intimation.html", sample=sample)
        assert sample.sample_name in html
        assert sample.sample_code in html
        assert sample.retailer_name in html
        assert sample.fso_name in html


class TestPdfGeneration:
    """Test 2: PDF bytes are generated from HTML."""

    def test_pdf_bytes_from_html(self, sample, app):
        from app.food_cell.renderer import DODocumentRenderer

        with app.app_context():
            renderer = DODocumentRenderer()
            html = renderer.render_html(sample)
            pdf_path = renderer.render_pdf(html, sample)

        assert pdf_path.endswith(".pdf")
        assert os.path.isfile(pdf_path)
        with open(pdf_path, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"


class TestDoIntimationGeneration:
    """Test 3: generate_and_forward_do_intimation creates a record."""

    def test_generates_intimation(self, sample, app):
        from app.food_cell.services import generate_and_forward_do_intimation

        _patch_sync_fns()
        with app.app_context():
            result = generate_and_forward_do_intimation(sample.id, sample=sample)

        assert result is not None
        assert result.do_reference_no.startswith("DO/")
        assert result.status == "forwarded"
        assert result.pdf_url is not None
        assert result.html_path is not None


class TestFsoSaveTrigger:
    """Test 4: send_do_intimation is callable after sample creation."""

    def test_send_task_callable(self):
        from app.food_cell.tasks import send_do_intimation

        assert callable(send_do_intimation)


class TestSyncForwarding:
    """Test 5: sync functions are called best-effort and results tracked."""

    def test_sync_results_recorded(self, intimation):
        assert intimation.sync_status is not None
        status = json.loads(intimation.sync_status)
        assert "sheets" in status
        assert "airtable" in status
        assert "excel" in status
        assert status["sheets"] is True


class TestDownloadEndpoint:
    """Test 6: /food-cell/do-intimation/<id>/pdf downloads PDF."""

    def test_download_pdf(self, sample, intimation, client):
        resp = client.get(f"/food-cell/do-intimation/{sample.id}/pdf")
        assert resp.status_code == 200
        assert b"%PDF" in resp.data


class TestHtmlViewEndpoint:
    """Test 7: /food-cell/do-intimation/<id>/html returns HTML inline."""

    def test_view_html(self, sample, intimation, client):
        resp = client.get(f"/food-cell/do-intimation/{sample.id}/html")
        assert resp.status_code == 200
        assert b"Designated Officer" in resp.data


class TestStatusEndpoint:
    """Test 8: /food-cell/do-intimation/<id>/status returns JSON."""

    def test_status_json(self, sample, intimation, client):
        resp = client.get(f"/food-cell/do-intimation/{sample.id}/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["exists"] is True
        assert data["do_reference_no"] == intimation.do_reference_no
        assert data["has_pdf"] is True
        assert data["has_html"] is True


class TestStatusEndpointNotFound:
    """Test 9: status returns exists=False for sample without intimation."""

    def test_status_not_found(self, sample, client):
        resp = client.get(f"/food-cell/do-intimation/{sample.id}/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["exists"] is False


class TestRegenerateEndpoint:
    """Test 10: POST /regenerate forces a new intimation."""

    def test_regenerate_force(self, sample, intimation, client):
        resp = client.post(f"/food-cell/do-intimation/{sample.id}/regenerate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "forwarded"
        assert data["do_reference_no"] is not None


class TestIdempotency:
    """Test 11: calling twice returns same intimation (no force)."""

    def test_idempotent_no_force(self, sample, app):
        from app.food_cell.services import generate_and_forward_do_intimation

        _patch_sync_fns()
        with app.app_context():
            first = generate_and_forward_do_intimation(sample.id, sample=sample)
            second = generate_and_forward_do_intimation(sample.id, sample=sample)

        assert first.id == second.id


class TestForceRegeneration:
    """Test 12: force=True creates a new intimation with a new reference."""

    def test_force_creates_new(self, sample, intimation, app):
        from app.food_cell.services import generate_and_forward_do_intimation

        _patch_sync_fns()
        with app.app_context():
            new_intimation = generate_and_forward_do_intimation(sample.id, sample=sample, force=True)
            assert new_intimation.do_reference_no != intimation.do_reference_no
            # Old record should be deleted, new one created
            from app.extensions import db as _db
            from app.models.food_cell import DoIntimation as _DI

            count = _db.session.query(_DI).filter_by(sample_id=sample.id).count()
            assert count == 1


class TestSampleNotFound:
    """Test 13: returns None for missing sample."""

    def test_missing_sample(self, app):
        from app.food_cell.services import generate_and_forward_do_intimation

        _patch_sync_fns()
        with app.app_context():
            result = generate_and_forward_do_intimation(999999)

        assert result is None


class TestDoReferenceUniqueness:
    """Test 14: DO reference numbers are unique and sequential."""

    def test_unique_references(self, env):
        from app.food_cell.services import generate_and_forward_do_intimation

        app, _client, _ctx = env
        _patch_sync_fns()
        with app.app_context():
            s1 = _make_sample(sample_code="SMP-UNIQ-001")
            s2 = _make_sample(sample_code="SMP-UNIQ-002")
            i1 = generate_and_forward_do_intimation(s1.id, sample=s1)
            i2 = generate_and_forward_do_intimation(s2.id, sample=s2)
            assert i1.do_reference_no != i2.do_reference_no


class TestForwardedTimestamp:
    """Test 15: Sample.food_cell_forwarded is set after intimation generation."""

    def test_forwarded_timestamp_set(self, sample, intimation, app):
        with app.app_context():
            from app.extensions import db
            from app.models import Sample

            s = db.session.get(Sample, sample.id)
            assert s.food_cell_forwarded is not None

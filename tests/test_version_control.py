"""Tests for Phase 9 version control.

Covers:
1. VersionService: create_version, incremental version numbering, and the
   create_version_if_changed dedupe used by the autosave path
2. The save/autosave hooks in document_viewer: explicit saves always create
   a snapshot; autosaves create one only when the content changed
3. The version_control blueprint is registered and reachable
"""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import CaseFile, User, Version


@pytest.fixture
def app_ctx():
    """App + in-memory DB with a CaseFile (id=1) for versioning tests."""
    from app import create_app
    from app.models import FSO

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            user = User(username="testuser", password_hash="pbkdf2:sha256$test$dummy")
            db.session.add(user)
            db.session.add(FSO(fso_name="Test Officer"))
            db.session.add(
                CaseFile(
                    case_number="TESTCASE001",
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
            )
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


class TestVersionService:
    """Unit tests for VersionService."""

    def test_create_version_stores_snapshot(self, app_ctx):
        from app.services.version_control import VersionService

        with app_ctx.application.app_context():
            version = VersionService().create_version(
                case_id=1,
                adjudication_id=None,
                doc_type="petition",
                html_content="<p>Snapshot one</p>",
                delta_content={"ops": [{"insert": "Snapshot one"}]},
                change_summary="Initial draft",
            )
            assert version.case_id == 1
            assert version.doc_type == "petition"
            assert version.version_number == 1
            assert version.html_snapshot == "<p>Snapshot one</p>"
            assert version.content_hash == VersionService()._calculate_content_hash("<p>Snapshot one</p>")
            assert version.delta is not None

    def test_version_number_increments(self, app_ctx):
        from app.services.version_control import VersionService

        service = VersionService()
        with app_ctx.application.app_context():
            service.create_version(case_id=1, adjudication_id=None, doc_type="petition", html_content="<p>v1</p>")
            service.create_version(case_id=1, adjudication_id=None, doc_type="petition", html_content="<p>v2</p>")
            third = service.create_version(
                case_id=1, adjudication_id=None, doc_type="petition", html_content="<p>v3</p>"
            )
            assert third.version_number == 3

    def test_create_version_if_changed_dedupes(self, app_ctx):
        from app.services.version_control import VersionService

        service = VersionService()
        with app_ctx.application.app_context():
            html = "<p>Same content</p>"
            first = service.create_version_if_changed(
                case_id=1, adjudication_id=None, doc_type="petition", html_content=html
            )
            assert first is not None
            # Identical content must NOT create a second snapshot.
            assert (
                service.create_version_if_changed(
                    case_id=1, adjudication_id=None, doc_type="petition", html_content=html
                )
                is None
            )
            assert Version.query.count() == 1

    def test_create_version_if_changed_creates_on_change(self, app_ctx):
        from app.services.version_control import VersionService

        service = VersionService()
        with app_ctx.application.app_context():
            service.create_version_if_changed(
                case_id=1, adjudication_id=None, doc_type="petition", html_content="<p>old</p>"
            )
            second = service.create_version_if_changed(
                case_id=1, adjudication_id=None, doc_type="petition", html_content="<p>new</p>"
            )
            assert second is not None
            assert second.version_number == 2


class TestSaveVersionHook:
    """Version snapshots are created by the editor save/autosave paths."""

    def _login(self, client):
        with client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

    def test_save_creates_version(self, app_ctx):
        from unittest.mock import patch

        from app.models import Version

        self._login(app_ctx)
        with patch(
            "app.document_viewer.routes.generate_pdf_from_html",
            return_value=(b"%PDF-fake", None),
        ):
            resp = app_ctx.post(
                "/document_viewer/save/1",
                json={"html": "<h1>Title</h1>", "doc_type": "petition"},
                follow_redirects=False,
            )
        assert resp.status_code == 200

        with app_ctx.application.app_context():
            version = Version.query.filter_by(case_id=1, doc_type="petition").first()
            assert version is not None
            assert version.version_number == 1
            assert version.html_snapshot == "<h1>Title</h1>"
            assert version.user_id == 1  # flask-login user id from the session

    def test_autosave_dedupes_versions(self, app_ctx):
        from app.models import Version

        self._login(app_ctx)
        html = "<p>Autosaved</p>"
        for _ in range(2):
            resp = app_ctx.post(
                "/document_viewer/autosave/1",
                json={"html": html, "doc_type": "petition"},
                follow_redirects=False,
            )
            assert resp.status_code == 200

        with app_ctx.application.app_context():
            assert Version.query.filter_by(case_id=1, doc_type="petition").count() == 1

        # Changed content creates a second snapshot.
        app_ctx.post(
            "/document_viewer/autosave/1",
            json={"html": "<p>Autosaved edited</p>", "doc_type": "petition"},
            follow_redirects=False,
        )
        with app_ctx.application.app_context():
            assert Version.query.filter_by(case_id=1, doc_type="petition").count() == 2


class TestVersionControlBlueprint:
    """The version_control API blueprint is registered and reachable."""

    def _login(self, client):
        with client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

    def test_blueprint_registered(self, app_ctx):
        rules = {rule.endpoint for rule in app_ctx.application.url_map.iter_rules()}
        assert "version_control.save_version" in rules
        assert "version_control.compare_versions" in rules
        assert "version_control.restore_version" in rules
        assert "version_control.create_branch" in rules
        assert "version_control.get_version_history" in rules
        assert "version_control.history_page" in rules

    def test_save_version_endpoint_requires_login(self, app_ctx):
        resp = app_ctx.post("/api/version-control/save-version", json={}, follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_save_version_endpoint_creates_snapshot(self, app_ctx):
        self._login(app_ctx)
        resp = app_ctx.post(
            "/api/version-control/save-version",
            json={"case_id": 1, "doc_type": "petition", "html": "<p>api version</p>"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["version_number"] == 1

        with app_ctx.application.app_context():
            assert Version.query.filter_by(case_id=1, doc_type="petition").count() == 1

    def test_history_endpoint(self, app_ctx):
        self._login(app_ctx)
        app_ctx.post(
            "/api/version-control/save-version",
            json={"case_id": 1, "doc_type": "petition", "html": "<p>history</p>"},
            follow_redirects=False,
        )
        resp = app_ctx.get("/api/version-control/history/1", follow_redirects=False)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["petition"]) == 1
        assert data["petition"][0]["version_number"] == 1

    def test_history_ui_page_renders(self, app_ctx):
        self._login(app_ctx)
        resp = app_ctx.get("/api/version-control/history/ui/1", follow_redirects=False)
        assert resp.status_code == 200
        assert b"Version History" in resp.data


# ---------------------------------------------------------------------------
# Phase 9 completion: real restore, branches, difflib diff, disambiguation
# ---------------------------------------------------------------------------


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def _save_version(client, payload):
    resp = client.post("/api/version-control/save-version", json=payload, follow_redirects=False)
    assert resp.status_code == 200
    return resp.get_json()


def _add_adjudication(app):
    """Insert a minimal Adjudication and return its id."""
    from app.models import Adjudication

    with app.app_context():
        adj = Adjudication(
            case_number="ADJ-TEST-1",
            food_safety_officer="Test Officer",
            fbo_owner="Test Owner",
            fbo_name="Test FBO",
            fbo_address="123 Test St",
            fssai_license="LIC123",
            First_inspection_date=datetime(2026, 7, 1),
            compliance_deadline=datetime(2026, 8, 1),
            inspection_date=datetime(2026, 7, 2),
        )
        db.session.add(adj)
        db.session.commit()
        return adj.id


class TestRestoreVersion:
    """restore_version now writes the snapshot back to instance/saved/ and
    records an append-only "Restored to version N" snapshot."""

    def test_restore_writes_snapshot_and_appends_version(self, app_ctx):
        from pathlib import Path

        _login(app_ctx)
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v1 original</p>"})
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v2 changed</p>"})

        # API save-version writes no files; only the restore writes one, so
        # counting before/after makes the assertion independent of leftovers
        # in the shared instance/saved directory.
        saved_dir = Path(app_ctx.application.instance_path) / "saved"
        files_before = len(list(saved_dir.glob("1_petition_*.html")))

        with app_ctx.application.app_context():
            target = Version.query.filter_by(case_id=1, doc_type="petition", version_number=1).first()
            v1_id = target.id

        resp = app_ctx.post(
            f"/api/version-control/restore/1/petition/{v1_id}",
            json={"change_summary": "Rollback to first draft"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["restored_version"]["version_number"] == 3

        with app_ctx.application.app_context():
            versions = (
                Version.query.filter_by(case_id=1, doc_type="petition").order_by(Version.version_number.asc()).all()
            )
            assert len(versions) == 3
            restored = versions[-1]
            assert restored.version_number == 3
            assert restored.html_snapshot == "<p>v1 original</p>"
            assert "Rollback to first draft" in (restored.change_summary or "")
            assert restored.branch_name is None

        # The restore must write exactly one new file under instance/saved/
        # so the document-viewer session-restore endpoint picks it up as the
        # latest saved document.
        files_after = len(list(saved_dir.glob("1_petition_*.html")))
        assert files_after == files_before + 1

    def test_restore_unknown_version_returns_404(self, app_ctx):
        _login(app_ctx)
        resp = app_ctx.post(
            "/api/version-control/restore/1/petition/9999",
            json={},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_restore_unknown_case_returns_404(self, app_ctx):
        _login(app_ctx)
        resp = app_ctx.post(
            "/api/version-control/restore/9999/petition/1",
            json={},
            follow_redirects=False,
        )
        assert resp.status_code == 404


class TestBranching:
    """create_branch persists a branch root with isolated numbering."""

    def test_create_branch_persists_branch_root(self, app_ctx):
        _login(app_ctx)
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v1</p>"})
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v2</p>"})

        resp = app_ctx.post(
            "/api/version-control/branch",
            json={
                "case_id": 1,
                "doc_type": "petition",
                "from_version": 1,
                "branch_name": "draft-a",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["branch"]["branch_name"] == "draft-a"
        assert data["branch"]["branch_root"]["version_number"] == 1

        with app_ctx.application.app_context():
            branch_root = Version.query.filter_by(
                case_id=1, doc_type="petition", branch_name="draft-a", version_number=1
            ).first()
            assert branch_root is not None
            assert branch_root.html_snapshot == "<p>v1</p>"
            # branch_of points at the mainline source version (id of v1)
            source = Version.query.filter_by(case_id=1, doc_type="petition", version_number=1, branch_name=None).first()
            assert branch_root.branch_of == source.id

    def test_branch_numbering_is_isolated_from_mainline(self, app_ctx):
        _login(app_ctx)
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v1</p>"})
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v2</p>"})
        app_ctx.post(
            "/api/version-control/branch",
            json={"case_id": 1, "doc_type": "petition", "from_version": 1, "branch_name": "draft-a"},
            follow_redirects=False,
        )
        # New mainline version -> #3
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v3 mainline</p>"})
        # New branch version -> #2 (isolated from the mainline sequence)
        _save_version(
            app_ctx,
            {"case_id": 1, "doc_type": "petition", "html": "<p>draft edit</p>", "branch_name": "draft-a"},
        )

        with app_ctx.application.app_context():
            mainline_max = (
                Version.query
                .filter_by(case_id=1, doc_type="petition", branch_name=None)
                .order_by(Version.version_number.desc())
                .first()
            )
            assert mainline_max.version_number == 3
            branch_max = (
                Version.query
                .filter_by(case_id=1, doc_type="petition", branch_name="draft-a")
                .order_by(Version.version_number.desc())
                .first()
            )
            assert branch_max.version_number == 2

    def test_branch_requires_name(self, app_ctx):
        _login(app_ctx)
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>v1</p>"})
        resp = app_ctx.post(
            "/api/version-control/branch",
            json={"case_id": 1, "doc_type": "petition", "from_version": 1, "branch_name": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 400


class TestVersionDiff:
    """_diff_html now reports real insertions/deletions via difflib."""

    def test_compare_reports_insertions_and_deletions(self, app_ctx):
        _login(app_ctx)
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>alpha beta gamma</p>"})
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>alpha NEW gamma</p>"})

        resp = app_ctx.get("/api/version-control/compare/1/petition/1/2")
        assert resp.status_code == 200
        data = resp.get_json()
        diff = data["diff"]
        assert diff["content_changed"] is True
        assert "beta" in diff["deletions"]
        assert "NEW" in diff["insertions"]
        assert diff["word_count_diff"] == 0
        assert diff["similarity"] < 1.0

    def test_compare_identical_versions(self, app_ctx):
        _login(app_ctx)
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>same</p>"})
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>same</p>"})
        resp = app_ctx.get("/api/version-control/compare/1/petition/1/2")
        assert resp.status_code == 200
        assert resp.get_json()["diff"]["content_changed"] is False

    def test_compare_missing_version_returns_404(self, app_ctx):
        _login(app_ctx)
        _save_version(app_ctx, {"case_id": 1, "doc_type": "petition", "html": "<p>only</p>"})
        resp = app_ctx.get("/api/version-control/compare/1/petition/1/99")
        assert resp.status_code == 404


class TestCaseAdjudicationDisambiguation:
    """History/compare/restore resolve the path ID against both tables."""

    def test_history_resolves_adjudication(self, app_ctx):
        _login(app_ctx)
        adj_id = _add_adjudication(app_ctx.application)
        _save_version(
            app_ctx,
            {"adjudication_id": adj_id, "doc_type": "permission", "html": "<p>adj v1</p>"},
        )

        # ``?kind=adjudication`` disambiguates when a CaseFile shares the ID
        # (each table autoincrements from 1 independently).
        resp = app_ctx.get(f"/api/version-control/history/{adj_id}?kind=adjudication")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["permission"]) == 1
        assert data["permission"][0]["version_number"] == 1

        # Case-file history (id 1) stays separate from adjudication history.
        resp2 = app_ctx.get("/api/version-control/history/1?kind=case_file")
        assert resp2.status_code == 200
        assert len(resp2.get_json()["permission"]) == 0

    def test_history_unknown_id_returns_404(self, app_ctx):
        _login(app_ctx)
        resp = app_ctx.get("/api/version-control/history/9999")
        assert resp.status_code == 404

    def test_restore_resolves_adjudication(self, app_ctx):
        _login(app_ctx)
        adj_id = _add_adjudication(app_ctx.application)
        _save_version(
            app_ctx,
            {"adjudication_id": adj_id, "doc_type": "petition", "html": "<p>adj v1</p>"},
        )
        _save_version(
            app_ctx,
            {"adjudication_id": adj_id, "doc_type": "petition", "html": "<p>adj v2</p>"},
        )

        with app_ctx.application.app_context():
            target = Version.query.filter_by(adjudication_id=adj_id, doc_type="petition", version_number=1).first()
            v1_id = target.id

        resp = app_ctx.post(
            f"/api/version-control/restore/{adj_id}/petition/{v1_id}?kind=adjudication",
            json={},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        with app_ctx.application.app_context():
            count = Version.query.filter_by(adjudication_id=adj_id, doc_type="petition").count()
            assert count == 3
            assert "Restored to version 1" in (
                Version.query
                .filter_by(adjudication_id=adj_id, doc_type="petition")
                .order_by(Version.version_number.desc())
                .first()
                .change_summary
                or ""
            )

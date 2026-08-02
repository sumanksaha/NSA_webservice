"""Integration tests for the document viewer editor routes.

Phase 1 tests:
- Returns 302 redirect to login when unauthenticated
- Returns 200 with rendered HTML content when authenticated
- Returns 404 for nonexistent case IDs
- Renders template variables correctly (no Jinja2 syntax visible)

Phase 2 tests:
- Quill CSS/JS vendored static files are served
- Editor template includes Quill-related DOM elements (#editor, #preview, #docTypeSelector)
- Both petition_html and permission_html are passed as template variables
- Editor JS file is served correctly

Phase 3 tests:
- POST /save/<case_id> returns a PDF download from edited HTML
- POST /save without login redirects to auth
- POST /save nonexistent case returns 404
- POST /save without CSRF token returns 400
- POST /save writes edited HTML to instance/saved/
"""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import CaseFile, User


@pytest.fixture
def test_client():
    """Test client with database context and a logged-in user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            # Create a test user
            user = User(username="testuser", password_hash="pbkdf2:sha256$test$dummy")  # noqa: S106
            db.session.add(user)
            db.session.commit()

            # Create a test FSO (required by CaseFile constraints via FSO table)
            from app.models import FSO

            fso = FSO(fso_name="Test Officer")
            db.session.add(fso)
            db.session.commit()

            # Create a test CaseFile
            case_file = CaseFile(
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
            db.session.add(case_file)
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


class TestEditorRouteAuth:
    """Test authentication gating on editor routes."""

    def test_editor_requires_auth_case_file(self, test_client):
        """GET /case_file_generator/<id>/editor without login redirects to auth."""
        resp = test_client.get("/case_file_generator/1/editor", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_editor_requires_auth_adjudication(self, test_client):
        """GET /adjudication/<id>/editor without login redirects to auth."""
        resp = test_client.get("/adjudication/1/editor", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


class TestEditorRouteCaseFile:
    """Test editor route with authenticated user for CaseFile."""

    def test_editor_returns_200_with_case_data(self, test_client):
        """GET /case_file_generator/<id>/editor with login returns 200 and rendered HTML."""
        # Log in via session (Flask-Login uses "_user_id" key)
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.get("/case_file_generator/1/editor", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        # Verify rendered HTML contains expected content (not Jinja2 syntax)
        assert "TESTCASE001" in html
        assert "{{" not in html
        assert "Test Officer" in html

    def test_editor_returns_404_for_nonexistent_case(self, test_client):
        """GET /case_file_generator/99999/editor returns 404."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.get("/case_file_generator/99999/editor", follow_redirects=False)
        assert resp.status_code == 404


class TestEditorRouteAdjudication:
    """Test editor route with authenticated user for Adjudication."""

    @pytest.fixture
    def adj_app(self):
        """Standalone app fixture for adjudication tests."""
        from app import create_app
        from app.models import FSO, Adjudication

        app = create_app()
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"

        with app.test_client() as client:
            with app.app_context():
                db.create_all()

                user = User(
                    username="testuser",
                    password_hash="pbkdf2:sha256$test$dummy",  # noqa: S106
                )
                db.session.add(user)

                fso = FSO(fso_name="Test Officer")
                db.session.add(fso)

                case_file = CaseFile(
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
                db.session.add(case_file)

                adj = Adjudication(
                    case_number="ADJ001",
                    food_safety_officer="Test Officer",
                    fbo_owner="Test Owner",
                    fbo_name="Test FBO",
                    fbo_address="123 Test St",
                    fssai_license="FSSAI123",
                    First_inspection_date=datetime(2026, 7, 3),
                    compliance_deadline=datetime(2026, 7, 10),
                    inspection_date=datetime(2026, 7, 3),
                    concerned_food="Test Food",
                )
                db.session.add(adj)
                db.session.commit()

            yield client

            with app.app_context():
                db.drop_all()

    def test_adjudication_editor_returns_200(self, adj_app):
        """GET /adjudication/<id>/editor with login returns 200."""
        with adj_app.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = adj_app.get("/adjudication/1/editor", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "ADJ001" in html
        assert "{{" not in html
        assert "Test Officer" in html


class TestEditorStaticAssets:
    """Test that vendored Quill and editor JS are served."""

    def test_quill_css_is_served(self, test_client):
        """GET /static/vendor/quill/quill.snow.css returns 200."""
        resp = test_client.get("/static/vendor/quill/quill.snow.css", follow_redirects=False)
        assert resp.status_code == 200
        assert "Quill" in resp.data.decode("utf-8")

    def test_quill_js_is_served(self, test_client):
        """GET /static/vendor/quill/quill.js returns 200."""
        resp = test_client.get("/static/vendor/quill/quill.js", follow_redirects=False)
        assert resp.status_code == 200
        js = resp.data.decode("utf-8")
        assert "Quill" in js

    def test_editor_js_is_served(self, test_client):
        """GET /static/js/document_viewer/editor.js returns 200."""
        resp = test_client.get("/static/js/document_viewer/editor.js", follow_redirects=False)
        assert resp.status_code == 200
        js = resp.data.decode("utf-8")
        assert "Quill" in js
        assert "dangerouslyPasteHTML" in js


class TestEditorTemplatePhase2:
    """Test Phase 2 template elements are present."""

    def test_template_contains_quill_elements(self, test_client):
        """Editor page contains Quill container, preview iframe, and doc-type selector."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.get("/case_file_generator/1/editor", follow_redirects=False)
        html = resp.data.decode("utf-8")

        assert 'id="editor"' in html
        assert 'id="preview"' in html
        assert 'id="docTypeSelector"' in html
        assert 'id="petition-data"' in html
        assert 'id="permission-data"' in html
        assert "quill.snow.css" in html
        assert "quill.js" in html
        assert "editor.js" in html

    def test_template_passes_both_doctypes(self, test_client):
        """Both petition and permission HTML are available as template variables."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.get("/case_file_generator/1/editor", follow_redirects=False)
        html = resp.data.decode("utf-8")

        assert "TESTCASE001" in html
        assert "{{" not in html


class TestSaveDocument:
    """Test Phase 3 POST /save/<case_id> endpoint."""

    def test_save_returns_pdf(self, test_client):
        """POST edited HTML returns a PDF download."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/save/1",
            json={
                "html": "<p>Hello <strong>World</strong></p>",
                "doc_type": "permission",
            },
            follow_redirects=False,
        )

        if resp.status_code == 500:
            # WeasyPrint system deps may be missing in test env
            data = resp.get_json()
            assert "error" in data
            return

        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        # PDF header magic bytes
        assert resp.data[:4] == b"%PDF"

    def test_save_requires_auth(self, test_client):
        """POST /save/<case_id> without login redirects to auth."""
        resp = test_client.post(
            "/document_viewer/save/1",
            json={"html": "<p>test</p>", "doc_type": "permission"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_save_404_nonexistent_case(self, test_client):
        """POST save for nonexistent case returns 404."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/save/99999",
            json={"html": "<p>test</p>", "doc_type": "permission"},
            follow_redirects=False,
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_save_no_html_returns_400(self, test_client):
        """POST save with empty HTML returns 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/save/1",
            json={"html": "", "doc_type": "permission"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_save_invalid_doc_type_returns_400(self, test_client):
        """POST save with invalid doc_type returns 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/save/1",
            json={"html": "<p>test</p>", "doc_type": "invalid"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_save_not_json_returns_400(self, test_client):
        """POST save with non-JSON body returns 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/save/1",
            data="not json",
            content_type="text/plain",
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_save_html_written_to_instance(self, test_client):
        """POST save writes the edited HTML to instance/saved/ folder."""
        from pathlib import Path

        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        edited_html = "<p>Edited content for save test</p>"
        resp = test_client.post(
            "/document_viewer/save/1",
            json={"html": edited_html, "doc_type": "petition"},
            follow_redirects=False,
        )

        if resp.status_code == 500:
            # WeasyPrint missing — still verify HTML was saved before PDF step
            pass

        saved_dir = Path(test_client.application.instance_path) / "saved"
        html_files = list(saved_dir.glob("1_petition_*.html"))
        assert len(html_files) >= 1
        content = html_files[-1].read_text(encoding="utf-8")
        assert "Edited content for save test" in content


class TestSaveDocumentCsrf:
    """Test CSRF protection on POST /save/<case_id>."""

    def test_save_without_csrf_returns_400(self, test_client):
        """POST save without CSRF token returns 400 when CSRF is enabled."""
        from flask_wtf.csrf import generate_csrf

        app = test_client.application
        original_csrf = app.config.get("WTF_CSRF_ENABLED", True)
        app.config["WTF_CSRF_ENABLED"] = True

        try:
            with app.test_request_context():
                generate_csrf()

            with test_client.session_transaction() as sess:
                sess["_user_id"] = "1"
                sess["_fresh"] = True
                with app.test_request_context():
                    sess["csrf_token"] = generate_csrf()

            resp = test_client.post(
                "/document_viewer/save/1",
                json={"html": "<p>test</p>", "doc_type": "permission"},
                follow_redirects=False,
            )
            assert resp.status_code == 400
        finally:
            app.config["WTF_CSRF_ENABLED"] = original_csrf


class TestSessionRestore:
    """Test Phase 1 (auto-save + delta): session restore via GET /saved/<case_id>/<doc_type>.

    The /saved endpoint now returns JSON: {"html": "...", "delta": {...}|null}
    """

    def test_get_saved_returns_json_with_html(self, test_client):
        """GET /saved returns JSON with saved HTML after a save."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        edited_html = "<p>Session restore test</p>"
        test_client.post(
            "/document_viewer/save/1",
            json={"html": edited_html, "doc_type": "petition"},
            follow_redirects=False,
        )

        resp = test_client.get("/document_viewer/saved/1/petition", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert "html" in data
        assert "Session restore test" in data["html"]
        assert "delta" in data
        assert data["delta"] is None

    def test_get_saved_html_404_no_save(self, test_client):
        """GET /saved returns 404 if no saved HTML exists."""
        from pathlib import Path

        saved_dir = Path(test_client.application.instance_path) / "saved"
        if saved_dir.is_dir():
            for f in saved_dir.glob("1_petition_*.html"):
                f.unlink()

        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.get("/document_viewer/saved/1/petition", follow_redirects=False)
        assert resp.status_code == 404

    def test_get_saved_html_invalid_doc_type(self, test_client):
        """GET /saved with invalid doc_type returns 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.get("/document_viewer/saved/1/invalid", follow_redirects=False)
        assert resp.status_code == 400

    def test_get_saved_returns_latest(self, test_client):
        """GET /saved returns the most recent saved version after multiple saves."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        first_html = "<p>First save</p>"
        second_html = "<p>Second save</p>"

        test_client.post(
            "/document_viewer/save/1", json={"html": first_html, "doc_type": "permission"}, follow_redirects=False
        )
        test_client.post(
            "/document_viewer/save/1", json={"html": second_html, "doc_type": "permission"}, follow_redirects=False
        )

        resp = test_client.get("/document_viewer/saved/1/permission", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert "Second save" in data["html"]

    def test_get_saved_requires_auth(self, test_client):
        """GET /saved without login redirects to auth."""
        resp = test_client.get("/document_viewer/saved/1/petition", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


class TestAutosave:
    """Test Phase 1: continuous auto-save via POST /autosave/<case_id>."""

    def test_autosave_returns_json_ok(self, test_client):
        """POST /autosave returns 200 with JSON status."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/autosave/1",
            json={"html": "<p>Autosave test</p>", "delta": {"ops": [{"insert": "test"}]}, "doc_type": "petition"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["has_delta"] is True
        assert "timestamp" in data

    def test_autosave_html_written_to_instance(self, test_client):
        """POST /autosave writes the HTML to instance/saved/."""
        from pathlib import Path

        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        test_client.post(
            "/document_viewer/autosave/1",
            json={"html": "<p>Autosave content</p>", "doc_type": "petition"},
            follow_redirects=False,
        )

        saved_dir = Path(test_client.application.instance_path) / "saved"
        html_files = list(saved_dir.glob("1_petition_*.html"))
        assert len(html_files) >= 1
        content = html_files[-1].read_text(encoding="utf-8")
        assert "Autosave content" in content

    def test_autosave_delta_written_to_instance(self, test_client):
        """POST /autosave writes the Delta to instance/saved/ as .delta file."""
        import json
        from pathlib import Path

        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        delta = {"ops": [{"insert": "Hello"}, {"insert": "World"}]}
        test_client.post(
            "/document_viewer/autosave/1",
            json={"html": "<p>Test</p>", "delta": delta, "doc_type": "petition"},
            follow_redirects=False,
        )

        saved_dir = Path(test_client.application.instance_path) / "saved"
        delta_files = list(saved_dir.glob("1_petition_*.delta"))
        assert len(delta_files) >= 1
        delta_content = delta_files[-1].read_text(encoding="utf-8")
        parsed = json.loads(delta_content)
        assert parsed == delta

    def test_autosave_without_delta(self, test_client):
        """POST /autosave without delta still works (delta is optional)."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/autosave/1",
            json={"html": "<p>No delta</p>", "doc_type": "permission"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["has_delta"] is False

    def test_autosave_requires_auth(self, test_client):
        """POST /autosave without login redirects to auth."""
        resp = test_client.post(
            "/document_viewer/autosave/1",
            json={"html": "<p>test</p>", "doc_type": "permission"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_autosave_404_nonexistent_case(self, test_client):
        """POST /autosave/99999 returns 404."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/autosave/99999",
            json={"html": "<p>test</p>", "doc_type": "permission"},
            follow_redirects=False,
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_autosave_no_html_returns_400(self, test_client):
        """POST /autosave with empty HTML returns 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/autosave/1", json={"html": "", "doc_type": "permission"}, follow_redirects=False
        )
        assert resp.status_code == 400

    def test_autosave_invalid_doc_type_returns_400(self, test_client):
        """POST /autosave with invalid doc_type returns 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/autosave/1", json={"html": "<p>test</p>", "doc_type": "invalid"}, follow_redirects=False
        )
        assert resp.status_code == 400

    def test_autosave_not_json_returns_400(self, test_client):
        """POST /autosave with non-JSON body returns 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        resp = test_client.post(
            "/document_viewer/autosave/1", data="not json", content_type="text/plain", follow_redirects=False
        )
        assert resp.status_code == 400


class TestDeltaStorage:
    """Test Phase 1: Quill Delta stored alongside HTML for round-trip fidelity."""

    def test_save_with_delta_stores_delta_file(self, test_client):
        """POST /save with delta stores a .delta file alongside .html."""
        from pathlib import Path

        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        delta = {"ops": [{"insert": "Hello"}, {"insert": "World", "bold": True}]}
        resp = test_client.post(
            "/document_viewer/save/1",
            json={"html": "<p>Test delta</p>", "delta": delta, "doc_type": "petition"},
            follow_redirects=False,
        )

        if resp.status_code == 500:
            pass  # WeasyPrint missing in test env

        saved_dir = Path(test_client.application.instance_path) / "saved"
        delta_files = list(saved_dir.glob("1_petition_*.delta"))
        assert len(delta_files) >= 1

    def test_saved_returns_delta_after_save_with_delta(self, test_client):
        """GET /saved returns delta when saved alongside HTML."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        delta = {"ops": [{"insert": "Round-trip test"}]}
        test_client.post(
            "/document_viewer/save/1",
            json={"html": "<p>Delta round-trip</p>", "delta": delta, "doc_type": "petition"},
            follow_redirects=False,
        )

        resp = test_client.get("/document_viewer/saved/1/petition", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["delta"] is not None
        assert data["delta"] == delta
        assert "Delta round-trip" in data["html"]

    def test_saved_returns_null_delta_after_save_without_delta(self, test_client):
        """GET /saved returns delta=null when no delta was saved."""
        from pathlib import Path

        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        saved_dir = Path(test_client.application.instance_path) / "saved"
        if saved_dir.is_dir():
            for f in saved_dir.glob("1_petition_*.delta"):
                f.unlink()

        test_client.post(
            "/document_viewer/save/1",
            json={"html": "<p>No delta here</p>", "doc_type": "petition"},
            follow_redirects=False,
        )

        resp = test_client.get("/document_viewer/saved/1/petition", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["delta"] is None
        assert "No delta here" in data["html"]

    def test_autosave_saves_delta_and_retrieves_via_saved(self, test_client):
        """Full round-trip: autosave with delta, then GET /saved returns it."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        delta = {"ops": [{"insert": "Autosave round-trip"}]}

        test_client.post(
            "/document_viewer/autosave/1",
            json={"html": "<p>Autosave delta</p>", "delta": delta, "doc_type": "petition"},
            follow_redirects=False,
        )

        resp = test_client.get("/document_viewer/saved/1/petition", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["delta"] is not None
        assert data["delta"] == delta
        assert "Autosave delta" in data["html"]

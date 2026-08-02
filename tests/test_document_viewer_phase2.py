"""Tests for Phase 2: editor image upload + Markdown export.

Covers:
  - Image upload endpoint: auth gating, success, unsupported type, size limit
  - Editor image serving: auth, traversal protection, missing file
  - Markdown export endpoint: auth, delta -> markdown, html fallback, errors
  - delta_to_markdown converter unit tests
"""

import io
from datetime import datetime

import pytest

from app.document_viewer.markdown_export import delta_to_markdown, html_to_markdown
from app.extensions import db
from app.models import CaseFile, User


def _tiny_png() -> bytes:
    """A valid tiny 1x1 PNG generated with PIL (a project dependency)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def test_client():
    """Test client with DB context and a logged-in user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()

            user = User(
                username="phase2user",
                password_hash="pbkdf2:sha256$test$dummy",  # noqa: S106
            )
            db.session.add(user)

            from app.models import FSO

            fso = FSO(fso_name="Test Officer")
            db.session.add(fso)

            case_file = CaseFile(
                case_number="PHASE2001",
                food_safety_officer_name="Test Officer",
                authorization_date=datetime(2026, 7, 3),
                inspection_date=datetime(2026, 7, 3),
                inspection_time="10:00",
                manufacturer_fssai="MFG123",
                manufacturer_name="Acme Foods Ltd",
                manufacturer_fbo_name="Acme FBO",
                manufacturer_address="123 Mfg St",
                retailer_fssai="RET456",
                retailer_name="Test Retailer",
                retailer_fbo_name="Retailer FBO",
                retailer_address="456 Retail St",
                product_name="Cotton Candy",
                batch_no="BATCH001",
                sample_quantity="1000g",
                packet_count=4,
                mfg_date=datetime(2026, 6, 1),
                expiry_date=datetime(2026, 8, 1),
                sample_code="PHASE2001",
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
                applicable_clause="Clause (zf) of section 3",
                applicable_sections="Sec 3",
            )
            db.session.add(case_file)
            db.session.commit()

            yield client

            db.session.remove()
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def _png_upload():
    return {"image": (io.BytesIO(_tiny_png()), "photo.png")}


# ---------------------------------------------------------------------------
# Image upload
# ---------------------------------------------------------------------------


class TestImageUpload:
    def test_upload_requires_auth(self, test_client):
        resp = test_client.post(
            "/document_viewer/upload_image",
            data=_png_upload(),
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_upload_success_returns_url(self, test_client):
        _login(test_client)
        resp = test_client.post(
            "/document_viewer/upload_image",
            data=_png_upload(),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["url"].startswith("/document_viewer/image/")
        assert data["url"].endswith(".png")

        # Uploaded image should be servable.
        img_resp = test_client.get(data["url"])
        assert img_resp.status_code == 200
        assert img_resp.mimetype == "image/png"
        assert img_resp.data == _tiny_png()

    def test_upload_unsupported_type(self, test_client):
        _login(test_client)
        resp = test_client.post(
            "/document_viewer/upload_image",
            data={"image": (io.BytesIO(b"bad"), "evil.exe")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "Unsupported image type" in resp.get_json()["error"]

    def test_upload_rejects_non_image_content(self, test_client):
        """A non-image payload renamed with an image extension is rejected."""
        _login(test_client)
        resp = test_client.post(
            "/document_viewer/upload_image",
            data={"image": (io.BytesIO(b"<html>polyglot</html>"), "fake.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "not a valid image" in resp.get_json()["error"]

    def test_upload_missing_file(self, test_client):
        _login(test_client)
        resp = test_client.post(
            "/document_viewer/upload_image",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_oversized(self, test_client):
        _login(test_client)
        big = io.BytesIO(b"x" * (6 * 1024 * 1024))
        resp = test_client.post(
            "/document_viewer/upload_image",
            data={"image": (big, "big.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "size limit" in resp.get_json()["error"]


class TestEditorImageServe:
    def test_serve_requires_auth(self, test_client):
        resp = test_client.get("/document_viewer/image/abc.png", follow_redirects=False)
        assert resp.status_code == 302

    def test_serve_rejects_traversal(self, test_client):
        _login(test_client)
        resp = test_client.get(
            "/document_viewer/image/../../etc/passwd",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_serve_rejects_unknown_name(self, test_client):
        _login(test_client)
        resp = test_client.get(
            "/document_viewer/image/nothexname.png",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_serve_missing_file_404(self, test_client):
        _login(test_client)
        resp = test_client.get(
            "/document_viewer/image/" + "a" * 32 + ".png",
            follow_redirects=False,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


class TestMarkdownExportEndpoint:
    def test_export_requires_auth(self, test_client):
        resp = test_client.post(
            "/document_viewer/export_markdown",
            json={"delta": {"ops": [{"insert": "hi"}]}},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_export_delta(self, test_client):
        _login(test_client)
        delta = {
            "ops": [
                {"insert": "Title"},
                {"insert": "\n", "attributes": {"header": 1}},
                {"insert": "Hello", "attributes": {"bold": True}},
                {"insert": " world"},
                {"insert": "\n"},
            ]
        }
        resp = test_client.post("/document_viewer/export_markdown", json={"delta": delta})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "# Title" in data["markdown"]
        assert "**Hello** world" in data["markdown"]
        assert data["filename"].endswith(".md")

    def test_export_html_fallback(self, test_client):
        _login(test_client)
        resp = test_client.post(
            "/document_viewer/export_markdown",
            json={"html": "<h1>Heading</h1><p>Body <strong>bold</strong></p>"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "# Heading" in data["markdown"]
        assert "**bold**" in data["markdown"]

    def test_export_no_content(self, test_client):
        _login(test_client)
        resp = test_client.post("/document_viewer/export_markdown", json={})
        assert resp.status_code == 400

    def test_export_not_json(self, test_client):
        _login(test_client)
        resp = test_client.post(
            "/document_viewer/export_markdown",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# delta_to_markdown converter
# ---------------------------------------------------------------------------


class TestDeltaToMarkdown:
    def test_empty(self):
        assert delta_to_markdown(None) == ""
        assert delta_to_markdown({}) == ""
        assert delta_to_markdown({"ops": []}) == ""

    def test_plain_text(self):
        md = delta_to_markdown({"ops": [{"insert": "Hello\n"}]})
        assert md == "Hello"

    def test_heading(self):
        md = delta_to_markdown({"ops": [{"insert": "Big Title"}, {"insert": "\n", "attributes": {"header": 1}}]})
        assert md == "# Big Title"

    def test_multiple_lines_and_headers(self):
        delta = {
            "ops": [
                {"insert": "H1"},
                {"insert": "\n", "attributes": {"header": 1}},
                {"insert": "H2"},
                {"insert": "\n", "attributes": {"header": 2}},
                {"insert": "body\n"},
            ]
        }
        md = delta_to_markdown(delta)
        assert md == "# H1\n## H2\nbody"

    def test_bold_italic_link(self):
        delta = {
            "ops": [
                {"insert": "bold", "attributes": {"bold": True}},
                {"insert": " and "},
                {"insert": "ital", "attributes": {"italic": True}},
                {"insert": "\n"},
            ]
        }
        md = delta_to_markdown(delta)
        assert "**bold**" in md
        assert "*ital*" in md

    def test_link(self):
        delta = {
            "ops": [
                {"insert": "site", "attributes": {"link": "https://example.com"}},
                {"insert": "\n"},
            ]
        }
        md = delta_to_markdown(delta)
        assert md == "[site](https://example.com)"

    def test_image_embed(self):
        delta = {"ops": [{"insert": {"image": "https://x/y.png"}}, {"insert": "\n"}]}
        md = delta_to_markdown(delta)
        assert md == "![image](https://x/y.png)"

    def test_bullet_list(self):
        delta = {
            "ops": [
                {"insert": "a"},
                {"insert": "\n", "attributes": {"list": "bullet"}},
                {"insert": "b"},
                {"insert": "\n", "attributes": {"list": "bullet"}},
            ]
        }
        md = delta_to_markdown(delta)
        assert md == "- a\n- b"

    def test_ordered_list(self):
        delta = {
            "ops": [
                {"insert": "one"},
                {"insert": "\n", "attributes": {"list": "ordered"}},
                {"insert": "two"},
                {"insert": "\n", "attributes": {"list": "ordered"}},
            ]
        }
        md = delta_to_markdown(delta)
        assert md == "1. one\n1. two"

    def test_blockquote_and_code(self):
        delta = {
            "ops": [
                {"insert": "quoted"},
                {"insert": "\n", "attributes": {"blockquote": True}},
                {"insert": "print(1)"},
                {"insert": "\n", "attributes": {"code-block": True}},
            ]
        }
        md = delta_to_markdown(delta)
        assert "> quoted" in md
        assert "```" in md
        assert "print(1)" in md

    def test_strike_underline(self):
        delta = {
            "ops": [
                {"insert": "gone", "attributes": {"strike": True}},
                {"insert": " under", "attributes": {"underline": True}},
                {"insert": "\n"},
            ]
        }
        md = delta_to_markdown(delta)
        assert "~~gone~~" in md
        assert "<u> under</u>" in md


class TestHtmlToMarkdown:
    def test_heading_and_bold(self):
        md = html_to_markdown("<h2>Sub</h2><p>Hello <strong>there</strong></p>")
        assert "## Sub" in md
        assert "**there**" in md

    def test_empty(self):
        assert html_to_markdown("") == ""
        assert html_to_markdown(None) == ""

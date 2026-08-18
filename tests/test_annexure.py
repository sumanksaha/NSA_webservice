"""Tests for the annexure management blueprint (Phase 4).

Covers:
  - List page + auth gating
  - Upload endpoint: success, metadata extraction, A/B/C letter assignment
  - Duplicate detection by content hash
  - Unsupported file types / missing parent records
  - Rename, reorder, delete, download
"""

import io
import os
from datetime import datetime

import pytest

os.environ.setdefault("SKIP_ANNEXURE_OCR", "1")

from app.extensions import db
from app.models import FSO, Annexure, CaseFile, User

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_client():
    """Test client with DB context, a case file, and a logged-in user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()

            user = User(
                username="annexureuser",
                password_hash="pbkdf2:sha256$test$dummy",
            )
            db.session.add(user)

            fso = FSO(fso_name="Test Officer")
            db.session.add(fso)

            case_file = CaseFile(
                case_number="ANNEX001",
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
                sample_code="ANNEX001",
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
            case_file_id = case_file.id

            yield client, case_file_id

            db.session.remove()
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def _txt_upload(filename="lab_report.txt", content="Laboratory test results heavy metals"):
    return {
        "file": (io.BytesIO(content.encode("utf-8")), filename),
    }


# ---------------------------------------------------------------------------
# Page + auth
# ---------------------------------------------------------------------------


class TestAnnexurePage:
    def test_page_requires_auth(self, test_client):
        client, _ = test_client
        resp = client.get("/annexure/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_page_returns_200(self, test_client):
        client, _ = test_client
        _login(client)
        resp = client.get("/annexure/", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Annexure" in html
        assert "annexureForm" in html


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class TestUpload:
    def test_upload_requires_auth(self, test_client):
        client, case_id = test_client
        resp = client.post(
            "/annexure/upload",
            data={"case_id": str(case_id), **_txt_upload()},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_upload_creates_annexure(self, test_client):
        client, case_id = test_client
        _login(client)
        resp = client.post(
            "/annexure/upload",
            data={"case_id": str(case_id), "caption": "Lab Report", **_txt_upload()},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["annexure_letter"] == "A"

        with client.application.app_context():
            ann = db.session.get(Annexure, data["annexure_id"])
            assert ann is not None
            assert ann.caption == "Lab Report"
            assert ann.case_id == case_id
            assert ann.annexure_letter == "A"
            assert ann.page_count == 1  # txt files are single-page
            assert ann.file_hash and len(ann.file_hash) == 64
            assert ann.mime_type == "text/plain"
            assert ann.ocr_text is None or "Laboratory" in ann.ocr_text

    def test_upload_assigns_next_letter(self, test_client):
        client, case_id = test_client
        _login(client)
        first_id = second_id = None
        for caption in ("First", "Second"):
            resp = client.post(
                "/annexure/upload",
                data={
                    "case_id": str(case_id),
                    "caption": caption,
                    **_txt_upload(filename=f"{caption.lower()}.txt", content=f"content {caption}"),
                },
                content_type="multipart/form-data",
            )
            assert resp.status_code == 201
            if caption == "First":
                first_id = resp.get_json()["annexure_id"]
            else:
                second_id = resp.get_json()["annexure_id"]

        with client.application.app_context():
            # Assert by id (not by uploaded_at ordering, which can tie).
            first = db.session.get(Annexure, first_id)
            second = db.session.get(Annexure, second_id)
            assert first.annexure_letter == "A"
            assert second.annexure_letter == "B"

    def test_upload_duplicate_detected(self, test_client):
        client, case_id = test_client
        _login(client)
        payload = {"case_id": str(case_id), "caption": "Original", **_txt_upload()}
        first = client.post("/annexure/upload", data=payload, content_type="multipart/form-data")
        assert first.status_code == 201

        second = client.post(
            "/annexure/upload",
            data={"case_id": str(case_id), "caption": "Duplicate", **_txt_upload()},
            content_type="multipart/form-data",
        )
        assert second.status_code == 409
        data = second.get_json()
        assert "Duplicate" in data["error"]
        assert data["duplicate_of"] == first.get_json()["annexure_id"]

    def test_upload_requires_parent(self, test_client):
        client, _ = test_client
        _login(client)
        resp = client.post("/annexure/upload", data=_txt_upload(), content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_upload_missing_case_404(self, test_client):
        client, _ = test_client
        _login(client)
        resp = client.post(
            "/annexure/upload",
            data={"case_id": "999999", **_txt_upload()},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 404

    def test_upload_unsupported_extension(self, test_client):
        client, case_id = test_client
        _login(client)
        resp = client.post(
            "/annexure/upload",
            data={
                "case_id": str(case_id),
                "file": (io.BytesIO(b"bad"), "virus.exe"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Replace
# ---------------------------------------------------------------------------


class TestReplace:
    def _upload_one(self, client, case_id, caption="Doc", content="sample text", filename="doc.txt"):
        resp = client.post(
            "/annexure/upload",
            data={
                "case_id": str(case_id),
                "caption": caption,
                **_txt_upload(filename=filename, content=content),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        return resp.get_json()["annexure_id"]

    def test_replace_requires_auth(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id)
        # Logout so the replace call is unauthenticated.
        client.get("/auth/logout", follow_redirects=False)
        resp = client.post(
            f"/annexure/{ann_id}/replace",
            data=_txt_upload(filename="new.txt", content="replacement"),
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_replace_updates_file_and_keeps_id_letter(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id, caption="Original", content="old content")

        resp = client.post(
            f"/annexure/{ann_id}/replace",
            data={
                "caption": "New Caption",
                **_txt_upload(filename="replacement.txt", content="new content here"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["annexure_id"] == ann_id
        assert data["annexure_letter"] == "A"  # letter preserved

        with client.application.app_context():
            ann = db.session.get(Annexure, ann_id)
            assert ann is not None
            assert ann.caption == "New Caption"
            assert ann.annexure_letter == "A"
            assert ann.file_hash and len(ann.file_hash) == 64
            assert ann.page_count == 1
            assert ann.filename == "replacement.txt"

        # Download outside app_context to avoid context-stack corruption.
        resp_dl = client.get(f"/annexure/{ann_id}/download")
        assert resp_dl.status_code == 200
        assert b"new content here" in resp_dl.data
        assert b"old content" not in resp_dl.data

    def test_replace_keeps_caption_when_omitted(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id, caption="Keep Me", content="v1")

        resp = client.post(
            f"/annexure/{ann_id}/replace",
            data=_txt_upload(filename="v2.txt", content="v2 content"),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        with client.application.app_context():
            assert db.session.get(Annexure, ann_id).caption == "Keep Me"

    def test_replace_reupload_same_content_not_duplicate(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id, content="same bytes")
        # Replacing with identical content to itself is allowed (no 409).
        resp = client.post(
            f"/annexure/{ann_id}/replace",
            data=_txt_upload(filename="same.txt", content="same bytes"),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200

    def test_replace_duplicate_of_other_annexure_rejected(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_a = self._upload_one(client, case_id, caption="A", content="unique a", filename="a.txt")
        ann_b = self._upload_one(client, case_id, caption="B", content="unique b", filename="b.txt")

        # Try to set B's file to A's content -> 409 duplicate.
        resp = client.post(
            f"/annexure/{ann_b}/replace",
            data=_txt_upload(filename="a.txt", content="unique a"),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert "Duplicate" in data["error"]
        assert data["duplicate_of"] == ann_a
        # B is unchanged after the rejected replace.
        with client.application.app_context():
            ann = db.session.get(Annexure, ann_b)
            assert ann.filename == "b.txt"

    def test_replace_unsupported_extension(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id)
        resp = client.post(
            f"/annexure/{ann_id}/replace",
            data={"file": (io.BytesIO(b"bad"), "virus.exe")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.get_json()["error"]

    def test_replace_missing_file_400(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id)
        resp = client.post(
            f"/annexure/{ann_id}/replace",
            data={"caption": "no file"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "No file provided" in resp.get_json()["error"]

    def test_replace_not_found(self, test_client):
        client, _ = test_client
        _login(client)
        resp = client.post(
            "/annexure/nope/replace",
            data=_txt_upload(),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Management actions
# ---------------------------------------------------------------------------


class TestManagement:
    def _upload_one(self, client, case_id, caption="Doc", content="sample text"):
        resp = client.post(
            "/annexure/upload",
            data={
                "case_id": str(case_id),
                "caption": caption,
                **_txt_upload(filename="doc.txt", content=content),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        return resp.get_json()["annexure_id"]

    def test_rename(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id)
        resp = client.post(
            f"/annexure/{ann_id}/rename",
            json={"caption": "Renamed Doc"},
        )
        assert resp.status_code == 200
        with client.application.app_context():
            assert db.session.get(Annexure, ann_id).caption == "Renamed Doc"

    def test_reorder(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id)
        resp = client.post(
            f"/annexure/{ann_id}/reorder",
            json={"annexure_letter": "C"},
        )
        assert resp.status_code == 200
        with client.application.app_context():
            assert db.session.get(Annexure, ann_id).annexure_letter == "C"

    def test_download(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id, content="download me now")
        resp = client.get(f"/annexure/{ann_id}/download")
        assert resp.status_code == 200
        assert b"download me now" in resp.data

    def test_delete(self, test_client):
        client, case_id = test_client
        _login(client)
        ann_id = self._upload_one(client, case_id)
        resp = client.post(f"/annexure/{ann_id}/delete")
        assert resp.status_code == 200
        with client.application.app_context():
            assert db.session.get(Annexure, ann_id) is None

    def test_not_found(self, test_client):
        client, _ = test_client
        _login(client)
        assert client.post("/annexure/nope/rename", json={"caption": "x"}).status_code == 404
        assert client.post("/annexure/nope/delete").status_code == 404
        assert client.get("/annexure/nope/download").status_code == 404

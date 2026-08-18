"""Phase 5 tests — evidence management blueprint + model unification.

Covers:
1. Evidence blueprint: index page, multi-file upload (photo compression +
   thumbnail), duplicate detection, download, thumbnail serving, update, delete.
2. Compression / thumbnail media helpers.
3. Unified model: legacy inspection photo endpoints now read/write Evidence.
"""

import io
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from app.extensions import db
from app.models import Evidence, User


@pytest.fixture
def test_client(tmp_path):
    """Test client on an isolated in-memory DB with a logged-in user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.instance_path = str(tmp_path)

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            user = User(username="tester", password_hash="x", is_admin=True)
            db.session.add(user)
            db.session.commit()
        yield client, app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def _png_bytes(color=(200, 30, 30), size=(600, 400)) -> bytes:
    """Render a small PNG in memory."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Blueprint routes
# ---------------------------------------------------------------------------


class TestEvidenceBlueprint:
    def test_index_page_renders(self, test_client):
        client, _ = test_client
        _login(client)
        resp = client.get("/evidence/")
        assert resp.status_code == 200
        assert "Evidence Library" in resp.data.decode("utf-8")

    def test_upload_photo_creates_row_and_thumbnail(self, test_client):
        client, app = test_client
        _login(client)

        resp = client.post(
            "/evidence/upload",
            data={
                "files": [(io.BytesIO(_png_bytes()), "shop.png")],
                "evidence_type": "photo",
                "caption": "Shop front",
                "tags": "inspection,premises",
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["results"][0]["status"] == "ok"
        evidence_id = data["results"][0]["evidence_id"]

        with app.app_context():
            ev = db.session.get(Evidence, evidence_id)
            assert ev is not None
            assert ev.evidence_type == "photo"
            assert ev.caption == "Shop front"
            assert ev.tags == "inspection,premises"
            assert ev.file_hash  # sha256 computed

            # Original PNG was re-encoded to optimized JPEG when smaller.
            assert Path(ev.filepath).exists()

        # Thumbnail route serves an image.
        resp = client.get(f"/evidence/{evidence_id}/thumbnail")
        assert resp.status_code == 200
        assert resp.mimetype == "image/jpeg"

    def test_upload_multiple_files_partial_errors(self, test_client):
        client, _ = test_client
        _login(client)

        resp = client.post(
            "/evidence/upload",
            data={
                "files": [
                    (io.BytesIO(_png_bytes((10, 200, 10))), "a.png"),
                    (io.BytesIO(b"definitely not a doc"), "bad.xyz"),
                ]
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 207
        data = resp.get_json()
        statuses = {r["filename"]: r["status"] for r in data["results"]}
        assert statuses["a.png"] == "ok"
        assert statuses["bad.xyz"] == "error"

    def test_upload_rejects_duplicate_by_hash(self, test_client):
        client, _ = test_client
        _login(client)
        png = _png_bytes()

        first = client.post(
            "/evidence/upload",
            data={"files": [(io.BytesIO(png), "one.png")]},
            content_type="multipart/form-data",
        )
        assert first.status_code == 201
        first_id = first.get_json()["results"][0]["evidence_id"]

        second = client.post(
            "/evidence/upload",
            data={"files": [(io.BytesIO(png), "two.png")]},
            content_type="multipart/form-data",
        )
        assert second.status_code == 400
        second_data = second.get_json()
        assert second_data["results"][0]["duplicate_of"] == first_id

    def test_upload_no_files(self, test_client):
        client, _ = test_client
        _login(client)
        resp = client.post("/evidence/upload", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_upload_unknown_parent_rejected(self, test_client):
        client, _ = test_client
        _login(client)
        resp = client.post(
            "/evidence/upload",
            data={"files": [(io.BytesIO(_png_bytes()), "p.png")], "case_id": "9999"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 404

    def test_download_and_delete(self, test_client):
        client, app = test_client
        _login(client)

        up = client.post(
            "/evidence/upload",
            data={"files": [(io.BytesIO(_png_bytes()), "dl.png")]},
            content_type="multipart/form-data",
        )
        evidence_id = up.get_json()["results"][0]["evidence_id"]

        resp = client.get(f"/evidence/{evidence_id}/download")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png" or resp.mimetype.startswith("image/")

        resp = client.post(f"/evidence/{evidence_id}/delete")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

        with app.app_context():
            assert db.session.get(Evidence, evidence_id) is None

    def test_update_metadata(self, test_client):
        client, app = test_client
        _login(client)

        up = client.post(
            "/evidence/upload",
            data={"files": [(io.BytesIO(_png_bytes()), "u.png")]},
            content_type="multipart/form-data",
        )
        evidence_id = up.get_json()["results"][0]["evidence_id"]

        resp = client.post(
            f"/evidence/{evidence_id}/update",
            json={"caption": "Renamed", "tags": "a,b", "evidence_type": "licence"},
        )
        assert resp.status_code == 200

        with app.app_context():
            ev = db.session.get(Evidence, evidence_id)
            assert ev.caption == "Renamed"
            assert ev.tags == "a,b"
            assert ev.evidence_type == "licence"

        # Invalid type -> 400.
        resp = client.post(
            f"/evidence/{evidence_id}/update",
            json={"evidence_type": "bogus"},
        )
        assert resp.status_code == 400

    def test_index_filters_by_type_and_tag(self, test_client):
        client, _ = test_client
        _login(client)

        client.post(
            "/evidence/upload",
            data={"files": [(io.BytesIO(_png_bytes((1, 2, 3))), "f1.png")], "tags": "kitchen"},
            content_type="multipart/form-data",
        )
        client.post(
            "/evidence/upload",
            data={"files": [(io.BytesIO(_png_bytes((4, 5, 6))), "f2.png")], "evidence_type": "video"},
            content_type="multipart/form-data",
        )

        html = client.get("/evidence/?evidence_type=video").data.decode("utf-8")
        assert "f2.png" in html
        assert "f1.png" not in html

        html = client.get("/evidence/?tag=kitchen").data.decode("utf-8")
        assert "f1.png" in html


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------


class TestMediaHelpers:
    def test_compress_image_downscales_large(self, tmp_path):
        from app.evidence.media import MAX_IMAGE_DIMENSION, compress_image

        large = tmp_path / "large.png"
        Image.new("RGB", (MAX_IMAGE_DIMENSION * 2, 100), (90, 90, 90)).save(large)

        result = compress_image(large)
        assert result is not None
        assert result.exists()
        assert not large.exists()  # replaced when smaller

    def test_compress_image_non_image_returns_none(self, tmp_path):
        from app.evidence.media import compress_image

        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not an image")
        assert compress_image(bad) is None

    def test_generate_thumbnail(self, tmp_path):
        from app.evidence.media import generate_thumbnail

        src = tmp_path / "t.png"
        Image.new("RGB", (800, 600), (5, 5, 5)).save(src)
        thumb = generate_thumbnail(src, tmp_path / "thumbs", "abc")
        assert thumb is not None and thumb.exists()
        with Image.open(thumb) as im:
            assert max(im.size) <= 320


# ---------------------------------------------------------------------------
# Unification: legacy inspection endpoints use Evidence
# ---------------------------------------------------------------------------


class TestUnifiedPhotoEndpoints:
    def test_adjudication_photo_upload_creates_evidence(self, test_client):
        client, app = test_client
        _login(client)

        with app.app_context():
            from app.models import Adjudication

            adj = Adjudication(
                case_number="ADJ1",
                food_safety_officer="Officer",
                fbo_owner="Owner",
                fbo_name="FBO",
                fbo_address="Addr",
                fssai_license="L-123",
                First_inspection_date=datetime(2026, 7, 1),
                compliance_deadline=datetime(2026, 7, 15),
                inspection_date=datetime(2026, 7, 1),
            )
            db.session.add(adj)
            db.session.commit()
            adj_id = adj.id

        # upload_photo uses app.utils.storage.upload_photo (Cloudinary); stub it.
        from unittest.mock import patch

        with patch("app.inspection.photo_service.upload_photo", return_value="https://cdn.example/x.jpg"):
            resp = client.post(
                f"/inspection/{adj_id}/photos",
                data={"photo": (io.BytesIO(_png_bytes()), "cam.jpg"), "caption": "Front"},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201
        photo_id = resp.get_json()["id"]

        with app.app_context():
            ev = db.session.get(Evidence, photo_id)
            assert ev is not None
            assert ev.adjudication_id == adj_id
            assert ev.evidence_type == "photo"
            assert ev.filepath == "https://cdn.example/x.jpg"
            assert ev.caption == "Front"

        # Listing returns it.
        resp = client.get(f"/inspection/{adj_id}/photos")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 1

        # Delete removes the row (and storage delete is stubbed).
        with patch("app.inspection.photo_service.delete_photo", return_value=True):
            resp = client.delete(f"/inspection/photos/{photo_id}")
        assert resp.status_code == 204

        with app.app_context():
            assert db.session.get(Evidence, photo_id) is None

    def test_inspection_photo_evidence_api_uses_evidence(self, test_client):
        client, app = test_client
        _login(client)

        with app.app_context():
            from app.models import FSO, Inspection

            db.session.add(FSO(fso_name="Officer"))
            db.session.commit()

            inspection = Inspection(
                inspection_code="INSP-EV-1",
                fso_name="Officer",
                inspection_date=datetime(2026, 7, 1),
                compliance_deadline=datetime(2026, 7, 15),
            )
            db.session.add(inspection)
            db.session.commit()
            insp_id = inspection.id

            ev = Evidence(
                id="uuid-1",
                inspection_id=insp_id,
                evidence_type="photo",
                filepath="/tmp/uuid-1.jpg",
                filename="uuid-1.jpg",
                raw_lat=22.5,
                raw_lng=88.3,
                accuracy=5.0,
                captured_at=datetime(2026, 7, 1),
                uploaded_at=datetime(2026, 7, 1),
                verification_status="PASS",
                stamped=True,
            )
            db.session.add(ev)
            db.session.commit()

        resp = client.get(f"/inspection/{insp_id}/photo-evidence")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["image_id"] == "uuid-1"
        assert data[0]["verification_status"] == "PASS"

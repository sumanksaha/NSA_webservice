"""Tests for OCR bulk upload (Phase E — operational modes).

A ZIP of PDFs is uploaded to ``POST /ocr/bulk-upload``; each PDF becomes its
own ``OCRDocument`` (sync fallback path in tests, since Celery is unconfigured).
"""

import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app import create_app
from app.extensions import db
from app.models import OCRDocument


@pytest.fixture(scope="module")
def app():
    import contextlib

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    # Manual push/pop (NOT `with`): conftest force-pops every app context after
    # each test, so a `with`-held context would raise LookupError at module
    # teardown — the source of phantom "ERROR" entries.
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    from app.models import User

    if not User.query.filter_by(username="ocrtester").first():
        db.session.add(User(username="ocrtester", password_hash="pbkdf2:sha256$test$dummy"))
        db.session.commit()
    db.session.remove()
    with contextlib.suppress(Exception):
        ctx.pop()
    yield app


@pytest.fixture()
def client(app):
    import contextlib

    from app.models import User

    c = app.test_client()
    ctx = app.app_context()
    ctx.push()
    try:
        user = User.query.filter_by(username="ocrtester").first()
        with c.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
    finally:
        with contextlib.suppress(Exception):
            ctx.pop()
    return c


@pytest.fixture(autouse=True)
def _no_real_broker(request, monkeypatch):
    """Force the synchronous fallback unless a test explicitly stubs the task.

    ``.delay()`` against the configured broker would enqueue REAL work on the
    production Redis instance (rediss://) — never acceptable from tests.
    """
    if "stub_task" in request.fixturenames:
        return
    monkeypatch.setattr("app.ocr_extraction.routes.process_ocr_document_async", None)


@pytest.fixture()
def stub_task(monkeypatch):
    """Stubbed Celery task recording .delay() calls instead of publishing."""

    class _FakeTask:
        def __init__(self):
            self.calls = []

        def delay(self, file_path, sample_id=None):
            self.calls.append({"file_path": file_path, "sample_id": sample_id})
            return {"task_id": f"fake-{len(self.calls)}"}

    fake = _FakeTask()
    monkeypatch.setattr("app.ocr_extraction.routes.process_ocr_document_async", fake)
    return fake


def _pdf_bytes(text: str) -> bytes:
    """Minimal single-page PDF built with PyMuPDF."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _zip(files: dict[str, bytes]) -> tuple[str, bytes]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for member_name, data in files.items():
            zf.writestr(member_name, data)
    return "bundle.zip", buf.getvalue()


def _upload(client, files: dict[str, bytes], filename="bundle.zip", **form):
    _name, data = _zip(files)
    return client.post(
        "/ocr/bulk-upload",
        data={"file": (io.BytesIO(data), filename if filename.endswith(".zip") else filename + ".zip"), **form},
        content_type="multipart/form-data",
    )


class TestBulkUpload:
    def test_async_dispatch_when_task_available(self, app, client, stub_task):
        resp = _upload(client, {"a.pdf": _pdf_bytes("Lab Report A")})
        assert resp.status_code == 202
        assert resp.json["status"] == "queued"
        assert resp.json["queued"] == ["a.pdf"]
        # No document persisted yet — the worker owns extraction.
        assert len(stub_task.calls) == 1
        with app.app_context():
            assert OCRDocument.query.count() == 0

    def test_processes_each_pdf_into_its_own_document(self, app, client):
        with app.app_context():
            before = OCRDocument.query.count()
        resp = _upload(client, {
            "a.pdf": _pdf_bytes("Lab Report A — Batch: B1"),
            "b.pdf": _pdf_bytes("Lab Report B — Batch: B2"),
        })
        assert resp.status_code == 200
        body = resp.json
        assert body["status"] == "completed"
        assert body["total_pdfs"] == 2
        assert len(body["processed"]) == 2
        assert all("document_id" in p for p in body["processed"])
        with app.app_context():
            assert OCRDocument.query.count() == before + 2

    def test_duplicate_files_are_skipped(self, app, client):
        same = _pdf_bytes("Identical Report — Batch: X")
        resp1 = _upload(client, {"one.pdf": same})
        assert resp1.status_code == 200
        with app.app_context():
            after_first = OCRDocument.query.count()

        resp2 = _upload(client, {"one.pdf": same})
        assert resp2.status_code == 200
        assert resp2.json["duplicates_skipped"] == ["one.pdf"]
        with app.app_context():
            assert OCRDocument.query.count() == after_first

    def test_one_bad_pdf_does_not_kill_the_batch(self, app, client):
        resp = _upload(client, {
            "good.pdf": _pdf_bytes("Valid Report"),
            "bad.pdf": b"this is not a pdf at all",
        })
        assert resp.status_code == 200
        results = {p["file"]: p for p in resp.json["processed"]}
        assert "document_id" in results["good.pdf"]
        assert "error" in results["bad.pdf"]

    def test_zip_without_pdfs_is_400(self, app, client):
        resp = _upload(client, {"notes.txt": b"no pdfs here"})
        assert resp.status_code == 400
        assert "No PDF files" in resp.json["error"]

    def test_non_zip_upload_is_400(self, app, client):
        resp = client.post(
            "/ocr/bulk-upload",
            data={"file": (io.BytesIO(b"plain"), "notazip.pdf")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_corrupt_zip_is_400(self, app, client):
        resp = client.post(
            "/ocr/bulk-upload",
            data={"file": (io.BytesIO(b"garbage-not-a-zip"), "broken.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "ZIP" in resp.json["error"]

    def test_missing_file_field_is_400(self, app, client):
        resp = client.post("/ocr/bulk-upload", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400


class TestExtractionPayloadIntegrity:
    def test_document_payload_has_expected_keys(self, app, client):
        resp = _upload(client, {"r.pdf": _pdf_bytes("Vitamin A: 120 IU/ml")})
        doc_id = resp.json["processed"][0]["document_id"]
        with app.app_context():
            doc = db.session.get(OCRDocument, doc_id)
        payload = json.loads(doc.extracted_json)
        assert {"fields", "lab_test_parameters", "extracted_text", "page_count"} <= set(payload)
        assert any(p["parameter_name"] for p in payload["lab_test_parameters"]) or payload[
            "extracted_text"
        ]

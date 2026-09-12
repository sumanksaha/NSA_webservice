"""Characterization tests for ``InspectionPhotoService.upload_evidence``.

The method had only helper-level coverage (coordinate fallback, EXIF,
validation) — the orchestration itself (persist → geo-verify → stamp →
update → OCR dispatch, and the rollback paths) was untested.  These tests
pin that orchestration before splitting the 177-line body (2026-09-12
review).  External seams are stubbed: geo-verification, image stamping,
and OCR dispatch.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest import mock

import pytest

from app.extensions import db
from app.inspection.photo_service import InspectionPhotoService, PhotoUploadResult

# Reuse the existing fixtures (test_app creates the in-memory DB + request
# context; inspection creates Inspection + Adjudication rows).
from tests.test_inspection_photo_service import inspection, test_app  # noqa: F401


class _FakeFile:
    """Minimal upload-file double (save + filename + supported ext)."""

    def __init__(self, name="photo.jpg"):
        self.filename = name
        self.saved_to: list[str] = []

    def save(self, path):
        self.saved_to.append(path)
        with open(path, "wb") as fh:
            fh.write(b"fake-image-bytes")


@pytest.fixture()
def photo_env(test_app, inspection):
    """App context + request context with form fields, service, inspection."""
    inspection_obj, _adj = inspection
    service = InspectionPhotoService()
    file_obj = _FakeFile()

    with test_app.test_request_context(
        "/upload",
        method="POST",
        data={"lat": "22.57", "lng": "88.36", "accuracy": "12", "captured_at": "2026-01-15T10:00:00"},
    ):
        yield test_app, service, inspection_obj, file_obj


def _stub_verify(monkeypatch, status="PASS", locality="Test Locality"):
    def _verify(lat, lng, acc, actor, inspection):
        return {
            "verification_status": status,
            "locality": locality,
            "ip_match": True,
            "distance_to_fbo_m": 42.0,
        }

    monkeypatch.setattr("app.inspection.photo_service.verify_photo_location", _verify)


def _stub_stamp(monkeypatch):
    monkeypatch.setattr(
        "app.inspection.photo_service.process_and_stamp_image",
        lambda f, locality, ts, status, image_id, insp_id: f"/tmp/stamped_{image_id}.jpg",
    )


class TestUploadEvidenceOrchestration:
    def test_happy_path_persists_and_returns_result(self, photo_env, monkeypatch):
        test_app, service, inspection, file_obj = photo_env
        _stub_verify(monkeypatch)
        _stub_stamp(monkeypatch)
        monkeypatch.setattr("app.inspection.photo_service._OCR_AVAILABLE", False)

        result = service.upload_evidence(inspection.id, file_obj, captured_at="2026-01-15T10:00:00")

        assert isinstance(result, PhotoUploadResult)
        assert result.photo_id  # uuid assigned
        assert result.stamped is True
        assert result.verification["verification_status"] == "PASS"
        assert result.raw_lat == 22.57  # form value wins over EXIF
        assert result.ocr_task_id is None and result.ocr_result is None

        from app.models import Evidence

        row = db.session.get(Evidence, result.photo_id)
        assert row is not None
        assert row.verification_status == "PASS"
        assert row.stamped is True
        assert row.filepath == result.filepath

    def test_db_failure_rolls_back_and_cleans_temp_file(self, photo_env, monkeypatch):
        test_app, service, inspection, file_obj = photo_env
        _stub_verify(monkeypatch)

        # Fail the very first commit (Evidence insert).
        with mock.patch.object(db.session, "commit", side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError, match="Failed to save photo evidence"):
                service.upload_evidence(inspection.id, file_obj, captured_at="2026-01-15T10:00:00")

        from app.models import Evidence

        assert Evidence.query.count() == 0  # rolled back
        assert file_obj.saved_to and not os.path.exists(file_obj.saved_to[0])  # temp cleaned

    def test_stamp_failure_deletes_pending_row(self, photo_env, monkeypatch):
        test_app, service, inspection, file_obj = photo_env
        _stub_verify(monkeypatch)

        def _boom(*a, **k):
            raise ValueError("not an image")

        monkeypatch.setattr("app.inspection.photo_service.process_and_stamp_image", _boom)

        with pytest.raises(ValueError, match="not an image"):
            service.upload_evidence(inspection.id, file_obj, captured_at="2026-01-15T10:00:00")

        from app.models import Evidence

        assert Evidence.query.count() == 0  # PENDING row removed

    def test_ocr_dispatch_async_and_sync_modes(self, photo_env, monkeypatch):
        test_app, service, inspection, file_obj = photo_env
        _stub_verify(monkeypatch)
        _stub_stamp(monkeypatch)
        monkeypatch.setattr("app.inspection.photo_service._OCR_AVAILABLE", True)

        import app.inspection.photo_service as ps_mod

        # Async mode → task id surfaced.
        monkeypatch.setattr(
            "app.utils.qstash_client.publish_task",
            lambda *a, **k: {"mode": "async", "message_id": "msg-123"},
        )
        result = service.upload_evidence(inspection.id, file_obj, captured_at="2026-01-15T10:00:00")
        assert result.ocr_task_id == "msg-123"

        # Sync mode → result surfaced.
        monkeypatch.setattr(
            "app.utils.qstash_client.publish_task",
            lambda *a, **k: {"mode": "sync", "result": {"text": "ok"}},
        )
        result2 = service.upload_evidence(inspection.id, file_obj, captured_at="2026-01-15T10:00:00")
        assert result2.ocr_result == {"text": "ok"}

    def test_ocr_dispatch_failure_is_best_effort(self, photo_env, monkeypatch):
        test_app, service, inspection, file_obj = photo_env
        _stub_verify(monkeypatch)
        _stub_stamp(monkeypatch)
        monkeypatch.setattr("app.inspection.photo_service._OCR_AVAILABLE", True)

        def _boom(*a, **k):
            raise RuntimeError("qstash down")

        monkeypatch.setattr("app.utils.qstash_client.publish_task", _boom)
        result = service.upload_evidence(inspection.id, file_obj, captured_at="2026-01-15T10:00:00")
        assert result.ocr_task_id is None and result.ocr_result is None  # upload still succeeds

    def test_missing_inspection_raises(self, test_app):
        service = InspectionPhotoService()
        with test_app.test_request_context(method="POST", data={"captured_at": "2026-01-15T10:00:00"}):
            with pytest.raises(FileNotFoundError):
                service.upload_evidence(999999, _FakeFile())

    def test_bad_captured_at_raises_value_error(self, photo_env, monkeypatch):
        test_app, service, inspection, file_obj = photo_env
        with test_app.test_request_context(
            method="POST",
            data={"lat": "1", "lng": "2", "captured_at": "not-a-date"},
        ):
            with pytest.raises(ValueError, match="captured_at"):
                service.upload_evidence(inspection.id, file_obj)

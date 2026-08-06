"""Tests for the OCR extraction pipeline services (plan.md Phase A).

Covers:
- Page splitting of multi-page PDFs (split_pdf_bundle)
- OCR extraction output structure (process_document_ocr)
- Lab-test parameter regex extraction (_extract_lab_test_parameters)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ocr_extraction import (
    LEGAL_AUTOFIELDS,
    SAMPLE_AUTOFIELDS,
    _extract_lab_test_parameters,
    process_document_ocr,
)
from app.services.page_splitter import split_pdf_bundle

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def single_page_pdf(tmp_path):
    """Create a minimal single-page PDF using PyMuPDF."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "Lab Report — Batch: BATCH-001")
    page.insert_text((72, 100), "Mfg: 01-01-2024 Exp: 31-12-2024")
    page.insert_text((72, 120), "Vitamin A: 120 IU/ml")

    pdf_path = tmp_path / "single_report.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture()
def multi_page_pdf(tmp_path):
    """Create a 3-page PDF simulating a multi-sample lab report bundle."""
    import fitz

    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Page {i + 1} — Sample Report")
        page.insert_text((72, 100), f"Batch: BATCH-00{i + 1}")
        page.insert_text((72, 120), "Lead: 0.3 mg/kg")

    pdf_path = tmp_path / "multi_bundle.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


# --------------------------------------------------------------------------- #
# Page Splitter
# --------------------------------------------------------------------------- #


class TestPageSplitter:
    def test_split_multi_page_pdf(self, multi_page_pdf):
        """A 3-page PDF should produce 3 page-PDFs."""
        pages = split_pdf_bundle(multi_page_pdf)
        assert len(pages) == 3
        for p in pages:
            assert p.exists()
            assert p.suffix == ".pdf"

    def test_split_single_page_pdf_returns_original(self, single_page_pdf):
        """A single-page PDF should return the original path unchanged."""
        pages = split_pdf_bundle(single_page_pdf)
        assert len(pages) == 1
        assert pages[0] == single_page_pdf

    def test_split_nonexistent_file_returns_empty(self, tmp_path):
        """A path that doesn't exist should return an empty list."""
        pages = split_pdf_bundle(tmp_path / "does_not_exist.pdf")
        assert pages == []

    def test_split_output_pages_are_single_page(self, multi_page_pdf):
        """Each output PDF should contain exactly 1 page."""
        import fitz

        pages = split_pdf_bundle(multi_page_pdf)
        for p in pages:
            doc = fitz.open(str(p))
            assert len(doc) == 1
            doc.close()


# --------------------------------------------------------------------------- #
# Lab-Test Parameter Extraction (regex — no OCR dependency)
# --------------------------------------------------------------------------- #


class TestLabTestParameterExtraction:
    def test_extract_parameters_with_units(self):
        """Regex should parse 'Parameter: Value Unit' patterns."""
        text = "Vitamin A: 120 IU/ml\nLead: 0.3 mg/kg"
        params = _extract_lab_test_parameters(text)
        assert len(params) >= 2
        names = {p["parameter_name"] for p in params}
        assert "Vitamin A" in names
        assert "Lead" in names

    def test_extract_parameters_returns_dict_structure(self):
        """Each parameter dict must have the required keys."""
        params = _extract_lab_test_parameters("Glucose: 95 mg/dL")
        assert len(params) >= 1
        p = params[0]
        assert "parameter_name" in p
        assert "observed_value" in p
        assert "unit" in p
        assert "confidence" in p

    def test_extract_parameters_empty_text(self):
        """Empty or non-matching text should return an empty list."""
        assert _extract_lab_test_parameters("") == []
        assert _extract_lab_test_parameters("No numbers here") == []


# --------------------------------------------------------------------------- #
# OCR Extraction (process_document_ocr)
# --------------------------------------------------------------------------- #


class TestOcrExtraction:
    def test_process_document_ocr_returns_expected_structure(self, single_page_pdf):
        """The extraction result must have all required keys."""
        result = process_document_ocr(single_page_pdf)

        assert result["file_name"] == "single_report.pdf"
        assert "file_hash" in result
        assert len(result["file_hash"]) == 64  # SHA-256 hex
        assert result["file_size"] > 0
        assert result["page_count"] == 1
        assert isinstance(result["fields"], dict)
        assert isinstance(result["confidence_scores"], dict)
        assert isinstance(result["extracted_text"], str)
        assert isinstance(result["lab_test_parameters"], list)
        assert result["sample_id"] is None

    def test_process_document_ocr_includes_sample_id(self, single_page_pdf):
        """When sample_id is provided, it should appear in the result."""
        result = process_document_ocr(single_page_pdf, sample_id=42)
        assert result["sample_id"] == 42

    def test_process_document_ocr_file_hash_is_deterministic(self, single_page_pdf):
        """Re-running on the same file should yield the same hash."""
        r1 = process_document_ocr(single_page_pdf)
        r2 = process_document_ocr(single_page_pdf)
        assert r1["file_hash"] == r2["file_hash"]

    def test_autofield_constants_are_defined(self):
        """Module-level constants should list the fields Phase C will consume."""
        assert "nature_of_food" in SAMPLE_AUTOFIELDS
        assert "batch_no" in SAMPLE_AUTOFIELDS
        assert "mfd" in SAMPLE_AUTOFIELDS
        assert "exp" in SAMPLE_AUTOFIELDS
        assert "case_number" in LEGAL_AUTOFIELDS
        assert "sample_code" in LEGAL_AUTOFIELDS


# --------------------------------------------------------------------------- #
# Celery Task Persistence (process_ocr_document_async)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def ocr_task_db():
    """In-memory app + DB for Celery-task persistence tests."""
    from app import create_app
    from app.extensions import db

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    ctx = app.app_context()
    ctx.push()

    db.drop_all()
    db.create_all()
    yield ctx
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _run_task_sync(file_path: Path, sample_id: int | None = None) -> str:
    """Invoke ``process_ocr_document_async`` synchronously, offline.

    ``update_state`` normally writes to the Celery result backend (Redis),
    which is unavailable in the test env — patch it to a no-op so the task
    body runs against the in-memory DB without touching the broker.
    """
    from unittest.mock import patch

    from app.ocr_pipeline.tasks import process_ocr_document_async

    with patch.object(process_ocr_document_async, "update_state", lambda *a, **k: None):
        return process_ocr_document_async.run(str(file_path), sample_id=sample_id)


class TestOcrTaskPersistence:
    def test_task_persists_ocr_document_row(self, single_page_pdf, ocr_task_db):
        """The Celery task must write an OCRDocument row with extraction payload."""
        from app.extensions import db
        from app.models import LabTestParameter, OCRDocument

        doc_id = _run_task_sync(single_page_pdf)
        assert doc_id, "task should return the persisted OCRDocument.id"

        doc = db.session.get(OCRDocument, doc_id)
        assert doc is not None
        assert doc.file_name == "single_report.pdf"
        assert doc.status == "completed"
        assert doc.page_count == 1
        assert len(doc.file_hash) == 64  # SHA-256
        assert doc.sample_id is None
        assert doc.extracted_json  # JSON payload written

        params = LabTestParameter.query.filter_by(ocr_document_id=doc_id).all()
        assert isinstance(params, list)
        for p in params:
            assert p.parameter_name
            assert p.source_authority == "zonal_ocr"

    def test_task_links_sample_id(self, single_page_pdf, ocr_task_db):
        """Passing sample_id links the OCRDocument to the Sample row."""
        from app.extensions import db
        from app.models import OCRDocument

        doc_id = _run_task_sync(single_page_pdf, sample_id=7)
        assert doc_id

        doc = db.session.get(OCRDocument, doc_id)
        assert doc is not None
        assert doc.sample_id == 7

    def test_task_returns_empty_on_missing_file(self, ocr_task_db):
        """A nonexistent PDF should fail gracefully (no DB row, empty id)."""
        from app.extensions import db
        from app.models import OCRDocument

        before = db.session.query(OCRDocument).count()
        doc_id = _run_task_sync(Path("does_not_exist.pdf"))
        assert doc_id == ""
        assert db.session.query(OCRDocument).count() == before

"""Celery tasks for the OCR extraction pipeline (plan.md Phase A).

Wires together :func:`split_pdf_bundle`, :func:`process_document_ocr`, and
the ``OCRDocument`` model into a single async entry point that persists
extraction results to the database.
"""

from __future__ import annotations

import json
import logging

# Lazy import to avoid ModuleNotFoundError in deployment environments
try:
    from celery_app import celery
except ImportError:
    celery = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def process_ocr_document_async(self, file_path: str, sample_id: int | None = None) -> str:
    """Process a PDF document through OCR extraction and persist results.

    1. Splits multi-page PDFs into per-page PDFs (via ``split_pdf_bundle``).
    2. Runs OCR + field extraction on each page (via ``process_document_ocr``).
    3. Writes results to the ``ocr_document`` table and per-field
       ``lab_test_parameter`` rows.

    Args:
        file_path: Path to the source PDF (lab report, photo, etc.).
        sample_id: Optional FK linking the extraction to a Sample.

    Returns:
        The ``OCRDocument.id`` of the persisted extraction record ("" on failure).
    """
    # Lazy imports inside the function so the module can load without
    # the full app stack initialized.
    from app.extensions import db
    from app.models import LabTestParameter, OCRDocument
    from app.services.ocr_extraction import process_document_ocr
    from app.services.page_splitter import split_pdf_bundle

    self.update_state(state="STARTED", meta={"status": "splitting PDF"})

    try:
        # Step 1: Split multi-page PDF into individual page-PDFs
        page_pdfs = split_pdf_bundle(file_path)
        if not page_pdfs:
            logger.error("process_ocr_document_async: no pages extracted from %s", file_path)
            return ""

        self.update_state(state="STARTED", meta={"status": "running OCR extraction", "pages": len(page_pdfs)})

        # Step 2: Run OCR extraction on each page
        all_fields: dict = {}
        all_lab_params: list[dict] = []
        combined_text = ""

        for i, page_pdf in enumerate(page_pdfs):
            result = process_document_ocr(str(page_pdf), sample_id=sample_id)
            if result:
                all_fields.update(result.get("fields", {}))
                all_lab_params.extend(result.get("lab_test_parameters", []))
                combined_text += "\n" + result.get("extracted_text", "")
                logger.debug("Page %d/%d: extracted %d fields", i + 1, len(page_pdfs), len(result.get("fields", {})))

        # Compute aggregate file hash from the original PDF
        import hashlib

        with open(file_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()

        self.update_state(state="STARTED", meta={"status": "persisting to DB"})

        # Step 3: Persist to OCRDocument
        import os

        ocr_doc = OCRDocument(
            file_name=os.path.basename(file_path),
            file_hash=file_hash,
            file_size=os.path.getsize(file_path),
            extracted_json=json.dumps({
                "fields": all_fields,
                "lab_test_parameters": all_lab_params,
                "extracted_text": combined_text,
                "page_count": len(page_pdfs),
            }),
            status="completed",
            page_count=len(page_pdfs),
            sample_id=sample_id,
        )
        db.session.add(ocr_doc)
        db.session.commit()

        # Persist lab test parameters
        for param in all_lab_params:
            lab_param = LabTestParameter(
                ocr_document_id=ocr_doc.id,
                sample_id=sample_id,
                parameter_name=param.get("parameter_name", ""),
                observed_value=param.get("observed_value", ""),
                unit=param.get("unit", ""),
                source_authority="zonal_ocr",
                confidence=param.get("confidence", 0.75),
            )
            db.session.add(lab_param)

        db.session.commit()
        logger.info(
            "process_ocr_document_async: persisted OCRDocument %s (%d fields, %d params)",
            ocr_doc.id,
            len(all_fields),
            len(all_lab_params),
        )
        return ocr_doc.id

    except Exception as exc:
        db.session.rollback()
        logger.error("process_ocr_document_async: failed for %s — %s", file_path, exc)
        self.update_state(state="FAILURE", meta={"status": "failed", "error": str(exc)})
        raise


# Register as Celery task if celery is available
if celery is not None:
    process_ocr_document_async = celery.task(bind=True, max_retries=3)(process_ocr_document_async)

"""Shared persistence for OCR extraction results.

Used by both the Celery task (:mod:`app.ocr_pipeline.tasks`) and the bulk-upload
endpoint (Phase E) so document/parameter persistence cannot drift between the
sync and async paths.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def persist_ocr_result(
    file_path: str | Path,
    *,
    fields: dict,
    lab_params: list[dict],
    extracted_text: str,
    page_count: int,
    file_hash: str,
    sample_id: int | None = None,
):
    """Persist one extraction result as an ``OCRDocument`` + parameter rows.

    Returns the persisted :class:`OCRDocument`.
    """
    from app.extensions import db
    from app.models import LabTestParameter, OCRDocument

    ocr_doc = OCRDocument(
        file_name=os.path.basename(str(file_path)),
        file_hash=file_hash,
        file_size=os.path.getsize(str(file_path)),
        extracted_json=json.dumps({
            "fields": fields,
            "lab_test_parameters": lab_params,
            "extracted_text": extracted_text,
            "page_count": page_count,
        }),
        status="completed",
        page_count=page_count,
        sample_id=sample_id,
    )
    db.session.add(ocr_doc)
    db.session.commit()

    for param in lab_params:
        db.session.add(
            LabTestParameter(
                ocr_document_id=ocr_doc.id,
                sample_id=sample_id,
                parameter_name=param.get("parameter_name", ""),
                observed_value=param.get("observed_value", ""),
                unit=param.get("unit", ""),
                source_authority="zonal_ocr",
                confidence=param.get("confidence", 0.75),
            )
        )
    db.session.commit()
    logger.info(
        "persist_ocr_result: OCRDocument %s (%d fields, %d params)",
        ocr_doc.id,
        len(fields),
        len(lab_params),
    )
    return ocr_doc


def run_ocr_pipeline(file_path: str | Path, sample_id: int | None = None):
    """Split → extract → persist for one PDF. Returns the ``OCRDocument``.

    Single-page files pass through unchanged (matching ``split_pdf_bundle``);
    multi-page bundles produce ONE OCRDocument whose fields/text are merged
    across pages.
    """
    import hashlib

    from app.services.ocr_extraction import process_document_ocr
    from app.services.page_splitter import split_pdf_bundle

    file_path = Path(file_path)
    page_pdfs = split_pdf_bundle(str(file_path))
    if not page_pdfs:
        raise ValueError(f"no pages extracted from {file_path.name}")

    all_fields: dict = {}
    all_lab_params: list[dict] = []
    combined_text = ""
    for page_pdf in page_pdfs:
        result = process_document_ocr(str(page_pdf), sample_id=sample_id)
        if result:
            all_fields.update(result.get("fields", {}))
            all_lab_params.extend(result.get("lab_test_parameters", []))
            combined_text += "\n" + result.get("extracted_text", "")

    with open(file_path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()

    return persist_ocr_result(
        file_path,
        fields=all_fields,
        lab_params=all_lab_params,
        extracted_text=combined_text,
        page_count=len(page_pdfs),
        file_hash=file_hash,
        sample_id=sample_id,
    )

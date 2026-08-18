"""OCR extraction service for legal documents.

Takes a single-page PDF (or image), runs the existing :class:`OCRPipeline`
to extract text, then applies regex-based field extraction
(:mod:`app.metadata_extractor`) and the suggester's section logic to
produce a structured ``dict`` of lab-test parameters and case fields.

The extracted payload is stored as JSON in ``OCRDocument.extracted_json``
by the Celery task in ``app/ocr_pipeline/tasks.py``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.metadata_extractor import LegalMetadataEngine

logger = logging.getLogger(__name__)

# Fields that the autopopulation engine (Phase C) maps into the Sample model.
SAMPLE_AUTOFIELDS = (
    "nature_of_food",
    "batch_no",
    "mfd",
    "exp",
    "manufacturer_details",
)

# Fields that map to CaseFile / Adjudication legal-document fields.
LEGAL_AUTOFIELDS = (
    "case_number",
    "sample_code",
    "lab_registration_no",
    "analyst_report_no",
    "batch_no",
    "mfg_date",
    "expiry_date",
)


def _extract_text_from_page(pdf_path: str | Path, page_number: int = 1) -> str:
    """Run the OCR pipeline on a single page and return cleaned text.

    Uses the PluginRegistry to resolve the active OCR provider (Phase 20).
    The provider's lazy import means OCRPipeline (and its EasyOCR/torch
    dependency) is only loaded when extraction actually runs.
    """
    from app.plugins.registry import PluginRegistry

    ocr_provider = PluginRegistry.get_instance().get_active("ocr")
    result = ocr_provider.extract_text(str(pdf_path))
    if not result.page_results:
        return result.text  # single-page or empty → return full text
    idx = min(page_number - 1, len(result.page_results) - 1)
    return result.page_results[idx].get("text", "") or ""


def process_document_ocr(file_path: str | Path, sample_id: int | None = None) -> dict:
    """Extract structured fields from a single-page PDF.

    Args:
        file_path: Path to a single-page PDF (use :func:`split_pdf_bundle`
            first if the source has multiple pages).
        sample_id: Optional FK to link the extraction result to a Sample.

    Returns:
        Dict with the following keys::

            {
                "file_name": str,
                "file_hash": str,            # SHA-256 of the raw file
                "file_size": int,
                "page_count": int,
                "fields": {field_name: value, ...},
                "confidence_scores": {field_name: float, ...},
                "extracted_text": str,       # concatenated page text
                "sample_id": int | None,
            }
    """
    file_path = Path(file_path)
    raw_bytes = file_path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    file_size = len(raw_bytes)

    # Count pages
    try:
        import fitz

        doc = fitz.open(str(file_path))
        page_count = len(doc)
        doc.close()
    except Exception:
        page_count = 1

    # Run OCR pipeline
    try:
        extracted_text = _extract_text_from_page(file_path)
    except Exception as exc:
        logger.error("process_document_ocr: OCR pipeline failed for %s — %s", file_path, exc)
        extracted_text = ""

    # Apply legal document metadata extraction (regex + NER from app.metadata_extractor)
    _metadata_engine = LegalMetadataEngine()
    legal_metadata = _metadata_engine.extract(extracted_text) if extracted_text else None
    fields: dict = {}
    confidence_scores: dict = {}
    if legal_metadata is not None:
        fields = legal_metadata.to_flat_dict()
        for field_name in fields:
            fc = getattr(legal_metadata, field_name, None)
            if fc is not None and hasattr(fc, "score"):
                confidence_scores[field_name] = fc.score

    # Also extract lab-test parameters (standard vs observed values)
    lab_params = _extract_lab_test_parameters(extracted_text)

    result = {
        "file_name": file_path.name,
        "file_hash": file_hash,
        "file_size": file_size,
        "page_count": page_count,
        "fields": fields,
        "confidence_scores": confidence_scores,
        "extracted_text": extracted_text,
        "lab_test_parameters": lab_params,
        "sample_id": sample_id,
    }

    logger.info(
        "process_document_ocr: extracted %d fields + %d lab params from %s",
        len(fields),
        len(lab_params),
        file_path.name,
    )
    return result


def _extract_lab_test_parameters(text: str) -> list[dict]:
    """Extract lab-test parameter name/value/unit triples from raw OCR text.

    Uses simple regex patterns for common lab-report fields. Returns a list
    of dicts with ``parameter_name``, ``observed_value``, ``unit``, and
    ``confidence`` keys.
    """
    import re

    # Pattern: parameter name, optional colon, value, optional unit
    # Handles formats like "Vitamin A: 120 IU/ml" or "Lead 0.3 mg/kg"
    pattern = re.compile(
        r"([A-Za-z][A-Za-z\s]{2,40}?)\s*:?\s*(\d+\.?\d*)\s*([A-Za-z/]{1,10})?",
        re.MULTILINE,
    )

    params: list[dict] = []
    for match in pattern.finditer(text):
        name = match.group(1).strip()
        value = match.group(2).strip()
        unit = (match.group(3) or "").strip()
        if name and value:
            params.append({
                "parameter_name": name,
                "observed_value": value,
                "unit": unit,
                "confidence": 0.75,  # placeholder — real confidence from OCR engine
            })

    return params

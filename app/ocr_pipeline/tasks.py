"""OCR extraction tasks (plan.md Phase A).

Wires together :func:`split_pdf_bundle`, :func:`process_document_ocr`, and
the ``OCRDocument`` model into a single entry point that persists
extraction results to the database. Dispatched via QStash with
synchronous inline fallback.
"""

from __future__ import annotations

import logging

from app.ocr_pipeline.persistence import run_ocr_pipeline

logger = logging.getLogger(__name__)


def process_ocr_document_async(file_path: str, sample_id: int | None = None) -> str:
    """Process a PDF document through OCR extraction and persist results.

    Delegates to :func:`app.ocr_pipeline.persistence.run_ocr_pipeline`
    (split → extract → persist) so the async path and the Phase-E bulk
    upload share one implementation.

    Args:
        file_path: Path to the source PDF (lab report, photo, etc.).
        sample_id: Optional FK linking the extraction to a Sample.

    Returns:
        The ``OCRDocument.id`` of the persisted extraction record ("" on failure).
    """
    logger.info("process_ocr_document_async: starting for %s", file_path)

    try:
        ocr_doc = run_ocr_pipeline(file_path, sample_id=sample_id)
        return ocr_doc.id

    except ValueError as exc:
        # Missing/unreadable file → no pages extracted. Phase A contract:
        # degrade gracefully with an empty id (never raise for absent input).
        logger.error("process_ocr_document_async: %s — returning empty id", exc)
        return ""

    except Exception:
        from app.extensions import db

        db.session.rollback()
        raise


def refresh_few_shot_examples(limit: int = 50) -> dict:
    """Phase D feedback loop (Celery task + direct-callable).

    Delegates to :func:`app.ocr_pipeline.feedback.refresh_few_shot_examples_sync`
    — rebuilds ``instance/ocr/few_shot_examples.json`` from recent human
    corrections so Vision-LLM extraction prompts improve continuously.

    Returns ``{"examples": <n>, "fields": <n>, "path": <str>}``.
    """
    from app.ocr_pipeline.feedback import refresh_few_shot_examples_sync

    return refresh_few_shot_examples_sync(limit=limit)


__all__ = ["process_ocr_document_async", "refresh_few_shot_examples"]

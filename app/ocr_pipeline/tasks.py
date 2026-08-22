"""Celery tasks for the OCR extraction pipeline (plan.md Phase A).

Wires together :func:`split_pdf_bundle`, :func:`process_document_ocr`, and
the ``OCRDocument`` model into a single async entry point that persists
extraction results to the database.
"""

from __future__ import annotations

import logging

# Lazy import to avoid ModuleNotFoundError in deployment environments
try:
    from celery_app import celery
except ImportError:
    celery = None  # type: ignore[assignment]

from app.ocr_pipeline.persistence import run_ocr_pipeline

logger = logging.getLogger(__name__)


def process_ocr_document_async(self, file_path: str, sample_id: int | None = None) -> str:
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
    self.update_state(state="STARTED", meta={"status": "splitting PDF"})

    try:
        self.update_state(state="STARTED", meta={"status": "running OCR extraction"})
        ocr_doc = run_ocr_pipeline(file_path, sample_id=sample_id)
        return ocr_doc.id

    except ValueError as exc:
        # Missing/unreadable file → no pages extracted. Phase A contract:
        # degrade gracefully with an empty id (never raise for absent input).
        logger.error("process_ocr_document_async: %s — returning empty id", exc)
        return ""

    except Exception as exc:
        from app.extensions import db

        db.session.rollback()
        logger.error("process_ocr_document_async: failed for %s — %s", file_path, exc)
        self.update_state(state="FAILURE", meta={"status": "failed", "error": str(exc)})
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


# Register as Celery tasks if celery is available
if celery is not None:
    process_ocr_document_async = celery.task(bind=True, max_retries=3)(process_ocr_document_async)
    refresh_few_shot_examples = celery.task(refresh_few_shot_examples)

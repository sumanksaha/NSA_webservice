"""OCR task dispatch with retries and deduplication."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_OCR_AVAILABLE = False

try:
    from app.inspection.tasks import run_ocr_extraction as _run_ocr_extraction

    if callable(_run_ocr_extraction):
        _OCR_AVAILABLE = True
except ImportError:
    pass


class OCRDispatcher:
    """OCR task management with retries and deduplication.

    Single interface: dispatch(filepath, image_id) -> dict with task_id/result.
    """

    def dispatch(self, filepath: str, image_id: str, **kwargs):
        """Dispatch OCR extraction for a processed photo.

        Returns dict with:
        - task_id: async job ID if dispatched async
        - result: sync result if executed inline
        - mode: 'async' or 'sync'
        """
        if not _OCR_AVAILABLE:
            logger.debug("OCR not available, skipping dispatch")
            return {"task_id": None, "result": None, "mode": "unavailable"}

        from app.utils.qstash_client import make_dedup_key, publish_task

        payload = {"file_path": filepath}
        dedup_key = make_dedup_key("run_ocr_extraction", image_id, payload)

        try:
            dispatched = publish_task(
                "run_ocr_extraction",
                payload=payload,
                dedup_key=dedup_key,
            )
        except Exception as exc:
            logger.warning("OCR dispatch failed: %s", exc)
            return {"task_id": None, "result": None, "mode": "failed"}

        if dispatched["mode"] == "async":
            return {"task_id": dispatched["message_id"], "result": None, "mode": "async"}

        result = dispatched["result"]
        if isinstance(result, Exception):
            logger.warning("OCR extraction returned exception: %s", result)
            return {"task_id": None, "result": None, "mode": "failed"}

        return {"task_id": None, "result": result, "mode": "sync"}

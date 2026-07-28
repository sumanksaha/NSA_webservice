"""
PDF-generation tasks for the Bill Generator blueprint.

Produces a bill PDF via WeasyPrint, saves it to disk, and returns
metadata (file path, record ID, timestamp) — never raw PDF bytes.
"""

import io
import logging
import os
from datetime import datetime

# Lazy import to avoid ModuleNotFoundError in deployment environments
try:
    from celery_app import celery
except ImportError:
    celery = None

logger = logging.getLogger(__name__)


def generate_bill_pdf(self, bill_id: int, template_vars: dict) -> dict:
    """
    Render a bill PDF from a Jinja2 template and save it to disk.

    Returns a metadata dict (not the PDF bytes):

        {
            "bill_id": int,
            "file_path": str | None,
            "generated_at": str (ISO-8601),
            "status": "ok" | "error",
            "error": str | None,
        }

    Transient failures (file I/O) are retried; template/data failures
    are not retried (they will fail permanently).
    """
    from flask import render_template
    from weasyprint import HTML

    generated_at = datetime.utcnow()

    # ---- Render HTML (permanent failure on error) ----
    try:
        rendered_html = render_template("bill_generator/template.html", **template_vars)
    except Exception as exc:
        logger.error("Template render failed for bill %s: %s", bill_id, exc)
        return _metadata(
            bill_id,
            None,
            generated_at,
            "error",
            f"Template render failed: {exc}",
        )

    # ---- Compile PDF (permanent failure on WeasyPrint errors) ----
    try:
        pdf_buffer = io.BytesIO()
        HTML(string=rendered_html).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        pdf_bytes = pdf_buffer.getvalue()
    except Exception as exc:
        logger.error("WeasyPrint failed for bill %s: %s", bill_id, exc)
        return _metadata(
            bill_id,
            None,
            generated_at,
            "error",
            f"WeasyPrint failed: {exc}",
        )

    # ---- Write to disk (transient I/O → retry) ----
    try:
        date_prefix = generated_at.strftime("%Y/%m")
        rel_dir = os.path.join("pdfs", "bills", date_prefix)
        os.makedirs(rel_dir, exist_ok=True)

        file_path = os.path.join(rel_dir, f"bill_{bill_id}.pdf")
        with open(file_path, "wb") as f:
            f.write(pdf_bytes)

        logger.info("Bill PDF saved: %s", file_path)
    except OSError as exc:
        logger.warning("I/O error saving bill PDF: %s", exc)
        raise self.retry(exc=exc, countdown=60)
    except Exception as exc:
        logger.warning("Transient error saving bill PDF: %s", exc)
        raise self.retry(exc=exc, countdown=60)

    return _metadata(bill_id, file_path, generated_at, "ok", None)


def _metadata(bill_id, file_path, generated_at, status, error):
    return {
        "bill_id": bill_id,
        "file_path": file_path,
        "generated_at": generated_at.isoformat(),
        "status": status,
        "error": error,
    }


# Register as Celery task if celery is available
if celery is not None:
    generate_bill_pdf = celery.task(bind=True, max_retries=3)(generate_bill_pdf)

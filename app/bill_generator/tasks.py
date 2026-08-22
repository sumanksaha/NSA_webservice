"""PDF-generation tasks for the Bill Generator blueprint.

Produces a bill PDF via WeasyPrint, saves it to disk, and returns
metadata (file path, record ID, timestamp) — never raw PDF bytes.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

# Lazy import to avoid ModuleNotFoundError in deployment environments
try:
    from celery_app import celery
except ImportError:
    celery = None

logger = logging.getLogger(__name__)


def generate_bill_pdf(self, bill_id: int, template_vars: dict) -> dict:
    """Render a bill PDF from a Jinja2 template and save it to disk.

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

    from app.utils.pdf_utils import generate_pdf_from_html

    generated_at = datetime.now(UTC)

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

    # ---- Compile PDF via the centralized PDF path (AGENTS.md §3.3) ----
    # ``generate_pdf_from_html`` returns ``(pdf_bytes | None, error | None)``
    # and never raises — failures come back as data. Permanent failure either way.
    try:
        pdf_bytes, pdf_error = generate_pdf_from_html(rendered_html)
    except Exception as exc:  # defensive — the central path is documented non-raising
        logger.error("WeasyPrint failed for bill %s: %s", bill_id, exc)
        return _metadata(
            bill_id,
            None,
            generated_at,
            "error",
            f"WeasyPrint failed: {exc}",
        )
    if pdf_bytes is None:
        logger.error("WeasyPrint failed for bill %s: %s", bill_id, pdf_error)
        return _metadata(
            bill_id,
            None,
            generated_at,
            "error",
            f"WeasyPrint failed: {pdf_error}",
        )

    # ---- Write to disk (transient I/O → retry) ----
    try:
        date_prefix = generated_at.strftime("%Y/%m")
        rel_dir = Path("pdfs") / "bills" / date_prefix
        os.makedirs(str(rel_dir), exist_ok=True)

        file_path = rel_dir / f"bill_{bill_id}.pdf"
        with open(str(file_path), "wb") as f:
            f.write(pdf_bytes)

        logger.info("Bill PDF saved: %s", file_path)
    except OSError as exc:
        logger.warning("I/O error saving bill PDF: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc
    except Exception as exc:
        logger.warning("Transient error saving bill PDF: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc

    return _metadata(bill_id, str(file_path), generated_at, "ok", None)


def _metadata(
    bill_id: int,
    file_path: str | None,
    generated_at: datetime,
    status: str,
    error: str | None,
) -> dict[str, str | int | None]:
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

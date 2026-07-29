"""PDF-generation tasks for the Case File Generator blueprint.

Produces a Petition PDF and a Permission Letter PDF for a single case
file, packages them as a ZIP archive on disk, and returns metadata
(file path, record ID, timestamp) — never raw PDF/ZIP bytes.
"""

import io
import logging
import os
import zipfile
from datetime import datetime

# Lazy import to avoid ModuleNotFoundError in deployment environments
try:
    from celery_app import celery
except ImportError:
    celery = None

logger = logging.getLogger(__name__)


def generate_case_file_pdf(self, case_file_id: int, case_data: dict) -> dict:
    """Render Petition + Permission Letter PDFs from Jinja2 templates and
    save them as a ZIP archive on disk.

    Returns a metadata dict (not the ZIP bytes):

        {
            "case_file_id": int,
            "file_path": str | None,
            "generated_at": str (ISO-8601),
            "status": "ok" | "error",
            "error": str | None,
        }

    Transient failures (file I/O) are retried; template/data failures
    are not (they will fail permanently).
    """
    from flask import render_template
    from weasyprint import HTML

    generated_at = datetime.utcnow()

    # ---- Render both templates (permanent failure on error) ----
    try:
        petition_html = render_template("case_file_generator/petition.html", **case_data)
        permission_html = render_template("case_file_generator/permission_letter.html", **case_data)
    except Exception as exc:
        logger.error(
            "Template render failed for case_file %s: %s",
            case_file_id,
            exc,
        )
        return _metadata(
            case_file_id,
            None,
            generated_at,
            "error",
            f"Template render failed: {exc}",
        )

    # ---- Compile PDFs (permanent failure on WeasyPrint errors) ----
    try:
        petition_pdf = io.BytesIO()
        HTML(string=petition_html).write_pdf(petition_pdf)
        petition_pdf.seek(0)

        permission_pdf = io.BytesIO()
        HTML(string=permission_html).write_pdf(permission_pdf)
        permission_pdf.seek(0)
    except Exception as exc:
        logger.error("WeasyPrint failed for case_file %s: %s", case_file_id, exc)
        return _metadata(
            case_file_id,
            None,
            generated_at,
            "error",
            f"WeasyPrint failed: {exc}",
        )

    # ---- Write ZIP to disk (transient I/O → retry) ----
    try:
        case_number = case_data.get("case_number", str(case_file_id)).replace("/", "_")
        date_prefix = generated_at.strftime("%Y/%m")
        rel_dir = os.path.join("pdfs", "case_files", date_prefix)
        os.makedirs(rel_dir, exist_ok=True)

        zip_path = os.path.join(rel_dir, f"case_{case_file_id}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"Petition_{case_number}.pdf", petition_pdf.getvalue())
            zf.writestr(
                f"Permission_Letter_{case_number}.pdf",
                permission_pdf.getvalue(),
            )

        logger.info("Case file ZIP saved: %s", zip_path)
    except OSError as exc:
        logger.warning("I/O error saving case file ZIP: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc
    except Exception as exc:
        logger.warning("Transient error saving case file ZIP: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc

    return _metadata(case_file_id, zip_path, generated_at, "ok", None)


def _metadata(case_file_id, file_path, generated_at, status, error):
    return {
        "case_file_id": case_file_id,
        "file_path": file_path,
        "generated_at": generated_at.isoformat(),
        "status": status,
        "error": error,
    }


# Register as Celery task if celery is available
if celery is not None:
    generate_case_file_pdf = celery.task(bind=True, max_retries=3)(generate_case_file_pdf)

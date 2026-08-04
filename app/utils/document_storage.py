"""Shared file-storage helpers for edited/saved documents (Phase 9).

Both the document-viewer save/autosave routes and the version-control
restore path persist HTML (+ optional Quill Delta) snapshots under
``instance/saved/``. Keeping the naming convention in one place guarantees
the document-viewer session-restore endpoint and restore-to-version agree
on what "the latest saved document" is.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def save_saved_document(
    instance_path,
    label,
    doc_type,
    html_content,
    delta_content=None,
) -> str:
    """Persist HTML (+ optional Delta) under ``instance_path/saved/``.

    Files are named ``<label>_<doc_type>_<timestamp>.html`` (plus a matching
    ``.delta`` file when delta content is provided), matching the convention
    consumed by ``document_viewer.get_saved_document``.

    Args:
        instance_path: The Flask instance path (``current_app.instance_path``).
        label: Case or adjudication ID used in the filename.
        doc_type: 'petition' or 'permission'.
        html_content: The HTML to persist.
        delta_content: Optional Quill Delta (dict) or raw JSON string.

    Returns:
        The timestamp string used in the filenames.
    """
    timestamp_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    saved_dir = Path(instance_path) / "saved"
    saved_dir.mkdir(parents=True, exist_ok=True)

    html_path = saved_dir / f"{label}_{doc_type}_{timestamp_str}.html"
    html_path.write_text(html_content or "", encoding="utf-8")

    if delta_content is not None:
        delta_path = saved_dir / f"{label}_{doc_type}_{timestamp_str}.delta"
        try:
            delta_text = delta_content if isinstance(delta_content, str) else json.dumps(delta_content)
            delta_path.write_text(delta_text, encoding="utf-8")
        except (TypeError, ValueError, OSError) as exc:
            logger.warning("Failed to save delta for %s %s: %s", label, doc_type, exc)

    return timestamp_str

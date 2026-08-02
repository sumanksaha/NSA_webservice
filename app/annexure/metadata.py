"""Metadata extraction for uploaded annexures.

Computes:

  - SHA-256 file hash (used for duplicate detection)
  - page count (PDF/DOCX via the document-loading pipeline; images/TXT = 1)
  - OCR / full text (PDF/DOCX/TXT via document loaders; images via the OCR
    pipeline)

All extractions are **best-effort**: failures are logged and return ``None``
so an upload is never blocked by a missing optional dependency (e.g. an OCR
engine not installed in a minimal deployment).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.document_loader.loader import DocumentLoaderFactory

logger = logging.getLogger(__name__)

# MIME types by file extension — the canonical supported set for annexures.
_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}

ALLOWED_EXTENSIONS = frozenset(_MIME_TYPES)
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

# Characters of OCR/full-text content kept per annexure.
_MAX_TEXT_CHARS = 20_000


def allowed_extension(filename: str) -> bool:
    """Return True when the file extension is in the supported set."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def mime_type(filename: str) -> str:
    """Return the MIME type for a file name (octet-stream as fallback)."""
    return _MIME_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def compute_sha256(file_path: Path) -> str:
    """Streaming SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_page_count(file_path: Path) -> int | None:
    """Best-effort page count for a document file (None on failure)."""
    ext = file_path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".txt"}:
        return 1
    try:
        result = DocumentLoaderFactory.load(file_path)
        return result.total_pages
    except Exception as exc:
        logger.warning("Page-count extraction failed for %s: %s", file_path, exc)
        return None


def extract_text(file_path: Path, max_chars: int = _MAX_TEXT_CHARS) -> str | None:
    """Best-effort full-text extraction for PDF/DOCX/TXT (None on failure)."""
    try:
        result = DocumentLoaderFactory.load(file_path)
        text = result.text.strip()
        return text[:max_chars] if text else None
    except Exception as exc:
        logger.warning("Text extraction failed for %s: %s", file_path, exc)
        return None


def extract_image_text(file_path: Path, max_chars: int = _MAX_TEXT_CHARS) -> str | None:
    """Best-effort OCR of an image via the OCR pipeline (None on failure).

    Requires an installed OCR backend (PaddleOCR or Tesseract). When neither
    is available the extraction returns ``None`` without raising.
    """
    try:
        import numpy as np
        from PIL import Image

        from app.ocr_pipeline.ocr_engine import OCREngine

        with Image.open(file_path) as image:
            array = np.array(image.convert("RGB"))
        engine = OCREngine(languages=["english"], use_gpu=False)
        text, _confidence, _engine_name, _language = engine.recognize(array)
        text = (text or "").strip()
        return text[:max_chars] if text else None
    except Exception as exc:
        logger.warning("Image OCR failed for %s: %s", file_path, exc)
        return None

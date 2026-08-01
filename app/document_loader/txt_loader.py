"""Plain text (.txt) document loader.

Detects encoding using chardet (or cchardet if available), falls back
to UTF-8/ISO-8859-1, and presents the entire file as a single "page".
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.document_loader.base import BaseLoader
from app.document_loader.models import FileMetadata, PageResult

logger = logging.getLogger(__name__)

__all__ = [
    "TXTLoader",
]

# Encodings to try in order when charset detection is unavailable
_FALLBACK_ENCODINGS = ["utf-8", "iso-8859-1", "cp1252", "latin-1"]


class TXTLoader(BaseLoader):
    """Load and extract text from plain text files.

    The entire file content is treated as a single page.
    """

    FILE_TYPE = "txt"

    def __init__(self, file_path: str | Path, max_page_chars: int | None = None) -> None:
        super().__init__(file_path)
        self._max_page_chars = max_page_chars

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_pages(self) -> list[PageResult]:
        raw_bytes = self._read_bytes()
        encoding = self._detect_encoding(raw_bytes)
        text = self._decode(raw_bytes, encoding)
        text = self._clean_text(text)
        text = self._truncate_page(text, self._max_page_chars)

        if not text.strip():
            logger.warning("Empty text file: %s", self._path)

        return [PageResult(page=1, text=text)]

    def _read_bytes(self) -> bytes:
        """Read raw file bytes with OS-level error handling."""
        try:
            return self._path.read_bytes()
        except OSError as exc:
            logger.error("Failed to read %s: %s", self._path, exc)
            raise

    def _detect_encoding(self, raw: bytes) -> str:
        """Detect text encoding, falling back gracefully."""
        try:
            import chardet

            result = chardet.detect(raw)
            encoding = result.get("encoding")
            confidence = result.get("confidence", 0)
            if encoding and confidence > 0.5:
                logger.debug("Detected encoding %s (confidence=%.2f)", encoding, confidence)
                return encoding
        except ImportError:
            logger.debug("chardet not available — trying fallback encodings")

        return "utf-8"  # safe default

    def _decode(self, raw: bytes, preferred: str) -> str:
        """Decode bytes to string, trying fallback encodings on failure."""
        try:
            return raw.decode(preferred)
        except (UnicodeDecodeError, LookupError):
            logger.warning("Failed to decode with %s — trying fallbacks", preferred)
            for enc in _FALLBACK_ENCODINGS:
                if enc == preferred:
                    continue
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            # Last resort: replace invalid sequences
            logger.warning("All decodings failed for %s — using lossy replacement", self._path)
            return raw.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _extract_metadata(self) -> FileMetadata:
        stat = self._path.stat()
        raw_bytes = self._read_bytes()
        encoding = self._detect_encoding(raw_bytes)

        return FileMetadata(
            file_size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_ctime) if hasattr(stat, "st_ctime") else None,
            modified_at=datetime.fromtimestamp(stat.st_mtime) if hasattr(stat, "st_mtime") else None,
            encoding=encoding,
            page_count=1,
        )

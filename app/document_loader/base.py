"""Abstract base class for all document loaders.

Every concrete loader (PDF, DOCX, TXT) implements the ``load()`` method
which returns a :class:`DocumentResult`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from pathlib import Path

from app.document_loader.models import DocumentResult, FileMetadata, PageResult

logger = logging.getLogger(__name__)

__all__ = [
    "BaseLoader",
]


class BaseLoader(ABC):
    """Abstract base class for document loaders.

    Subclasses must implement :meth:`_extract_pages` and :meth:`_extract_metadata`.
    The public :meth:`load` method orchestrates both and assembles the final
    :class:`DocumentResult`.
    """

    # Mapping from file extension to human-readable type — override in subclasses.
    FILE_TYPE: str = ""

    def __init__(self, file_path: str | Path, max_page_chars: int | None = None) -> None:
        self._path = Path(file_path)
        self._max_page_chars = max_page_chars
        if not self._path.is_file():
            raise FileNotFoundError(f"Document not found: {self._path}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> DocumentResult:
        """Load the document and return a validated :class:`DocumentResult`."""
        pages = self._extract_pages()
        metadata = self._extract_metadata()
        return DocumentResult(
            file_name=self._path.name,
            file_type=self.FILE_TYPE,
            pages=pages,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Subclass responsibilities
    # ------------------------------------------------------------------

    @abstractmethod
    def _extract_pages(self) -> list[PageResult]:
        """Extract text on a per-page basis.

        Returns:
            A list of :class:`PageResult` objects ordered by page number.

        """

    @abstractmethod
    def _extract_metadata(self) -> FileMetadata:
        """Extract file-level metadata.

        Returns:
            A :class:`FileMetadata` instance.

        """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text(raw: str) -> str:
        """Normalise whitespace and strip control characters from extracted text."""
        # Replace non-breaking spaces and other special spaces with regular space
        text = raw.replace("\u00a0", " ").replace("\u3000", " ")
        # Normalise Unicode form
        text = unicodedata.normalize("NFKC", text)
        # Collapse multiple blank lines into at most two
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Strip leading/trailing whitespace per line while keeping page structure
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)
        return text.strip()

    @staticmethod
    def _truncate_page(text: str, max_chars: int | None = None) -> str:
        """Optionally truncate a page's text to *max_chars* characters."""
        if max_chars is not None and len(text) > max_chars:
            logger.warning("Truncating page from %d to %d characters", len(text), max_chars)
            return text[:max_chars] + "\n… [TRUNCATED]"
        return text

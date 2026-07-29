"""
Document loader factory — dispatches to the correct loader implementation
based on the source file extension.

Extend by adding new entries to ``EXTENSION_MAP``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.document_loader.base import BaseLoader
from app.document_loader.docx_loader import DOCXLoader
from app.document_loader.models import DocumentResult
from app.document_loader.pdf_loader import PDFLoader
from app.document_loader.txt_loader import TXTLoader

logger = logging.getLogger(__name__)

__all__ = [
    "EXTENSION_MAP",
    "DocumentLoaderFactory",
]

# ---------------------------------------------------------------------------
# Extension → Loader mapping
# ---------------------------------------------------------------------------
# Add new entries here when supporting additional file types.
# The values are loader *classes* (not instances) so the factory can pass
# optional parameters through on every call.
# ---------------------------------------------------------------------------
EXTENSION_MAP: dict[str, type[BaseLoader]] = {
    ".pdf": PDFLoader,
    ".docx": DOCXLoader,
    ".txt": TXTLoader,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DocumentLoaderFactory:
    """Factory for creating and running the appropriate document loader.

    Usage::

        doc = DocumentLoaderFactory.load("invoice.pdf")
        print(doc.model_dump_json(indent=2))
    """

    @classmethod
    def load(
        cls,
        file_path: str | Path,
        max_page_chars: int | None = None,
    ) -> DocumentResult:
        """Detect file type, instantiate the correct loader, and load the document.

        Args:
            file_path: Path to the document file.
            max_page_chars: Optional per-page character limit for truncation.

        Returns:
            A validated :class:`DocumentResult`.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file extension is unsupported.
        """
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        loader_cls = cls._resolve_loader(path)
        loader = loader_cls(path, max_page_chars=max_page_chars)
        return loader.load()

    @classmethod
    def supported_extensions(cls) -> frozenset[str]:
        """Return the set of supported file extensions (e.g. ``{'.pdf', '.docx'}``)."""
        return frozenset(EXTENSION_MAP.keys())

    @classmethod
    def is_supported(cls, file_path: str | Path) -> bool:
        """Check whether a given file path has a supported extension."""
        return Path(file_path).suffix.lower() in EXTENSION_MAP

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_loader(cls, path: Path) -> type[BaseLoader]:
        ext = path.suffix.lower()
        loader_cls = EXTENSION_MAP.get(ext)
        if loader_cls is None:
            raise ValueError(
                f"Unsupported file extension '{ext}' for '{path.name}'. Supported: {', '.join(sorted(EXTENSION_MAP))}"
            )
        return loader_cls

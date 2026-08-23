"""Abstract base provider classes for the Phase 20 Plugin Architecture.

Each provider interface follows the existing ``BaseLoader`` / ``BaseExtractor``
/ ``BaseRule`` ABC pattern (abc.ABC + @abstractmethod).  Concrete plugins live
in ``app.plugins.{ocr_plugins,ai_plugins,rule_plugins,pdf_plugins}`` and wrap
the project's existing service classes via lazy imports.

The dataclasses here (OCRResult, AIResponse) give plugins a typed return
shape so callers can depend on a stable contract regardless of which provider
implementation is active.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data classes for provider return values
# --------------------------------------------------------------------------- #


@dataclass
class PageText:
    """Per-page extraction entry — typed replacement for raw page dicts.

    Attributes:
        page: 1-based page number.
        text: Extracted text for this page (may be empty).
        confidence: Engine confidence for this page (0.0–1.0).
        engine: Name of the engine that produced this page's text.
    """

    page: int = 1
    text: str = ""
    confidence: float = 0.0
    engine: str = ""


@dataclass
class OCRResult:
    """Structured result from an OCR provider.

    This is THE canonical result shape at the OCR extraction seam — every
    caller consumes this type regardless of which engine ran inside the
    provider implementation.

    Attributes:
        text: Extracted text (concatenated across pages).
        confidence: Aggregate confidence score (0.0–1.0).
        ocr_engine_used: Name of the engine that produced the result.
        page_count: Number of pages processed.
        page_results: Typed per-page detail (:class:`PageText`).
    """

    text: str = ""
    confidence: float = 0.0
    ocr_engine_used: str = ""
    page_count: int = 0
    page_results: list[PageText] = field(default_factory=list)


@dataclass
class AIResponse:
    """Structured response from an AI provider.

    Attributes:
        content: The generated text.
        tokens_used: Total tokens consumed (for monitoring).
        model: Model identifier used.
    """

    content: str = ""
    tokens_used: int = 0
    model: str = ""


# --------------------------------------------------------------------------- #
# Provider interfaces
# --------------------------------------------------------------------------- #


class OCRProvider(ABC):
    """Abstract base for OCR engines.

    Implementations must lazily import their backend (EasyOCR, PaddleOCR,
    Tesseract) so that ``import app.plugins`` never triggers a hard dependency.
    ``extract_text`` accepts both document paths (PDF) and image paths
    (JPG/PNG/TIFF/…); the document-vs-image dispatch is implementation
    detail behind this seam.
    """

    @abstractmethod
    def extract_text(self, file_path: str | Path) -> OCRResult:
        """Extract structured text from a file.

        Args:
            file_path: Path to a PDF or image file.

        Returns:
            An :class:`OCRResult` with the extracted text and metadata.
        """
        ...

    def available(self) -> bool:
        """Whether the backend can actually run (deps installed/configured).

        Default is optimistic (True); providers with cheap dependency probes
        should override so callers can degrade gracefully without attempting
        an extraction.
        """
        return True

    @staticmethod
    def _safe_path(file_path: str | Path) -> str:
        return str(file_path)


class AIProvider(ABC):
    """Abstract base for AI/LLM providers."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a response from a prompt.

        Args:
            prompt: The text prompt to send to the provider.
            **kwargs: Provider-specific options (model, max_tokens, etc.).

        Returns:
            The generated text string.
        """
        ...

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True when the provider is properly configured (API key, etc.)."""
        ...


class RuleProvider(ABC):
    """Abstract base for rule/suggestion engines."""

    @abstractmethod
    def suggest_sections(self, case_data: dict[str, Any]) -> dict[str, Any]:
        """Suggest applicable legal sections based on case data.

        Args:
            case_data: A dict of case fields (e.g. checklist values).

        Returns:
            A dict with ``sections`` (list[str]) and ``reasoning`` (dict) keys.
        """
        ...


class PDFProvider(ABC):
    """Abstract base for PDF rendering engines."""

    @abstractmethod
    def render_pdf(self, html_content: str, **kwargs: Any) -> bytes:
        """Render HTML to PDF bytes.

        Raises:
            RuntimeError: When the rendering backend is unavailable.
        """
        ...

    @abstractmethod
    def render_pdf_safe(self, html_content: str, **kwargs: Any) -> tuple[bytes | None, str | None]:
        """Render HTML to PDF, returning (pdf_bytes, error) instead of raising.

        This is the preferred entry point for callers that must not crash
        when WeasyPrint is unavailable (e.g. Render free tier without GTK libs).
        """
        ...


__all__ = [
    "AIProvider",
    "AIResponse",
    "OCRProvider",
    "OCRResult",
    "PDFProvider",
    "PageText",
    "RuleProvider",
]

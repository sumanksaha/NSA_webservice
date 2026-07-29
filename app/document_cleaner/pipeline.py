"""
Pipeline orchestrator for the Legal Document Cleaning Pipeline.

Chains together removal and normalization operations in a config-driven
sequence. Produces a ``CleanedDocument`` with cleaned text and report.
"""

from __future__ import annotations

import logging

from app.document_cleaner.config import PRESETS
from app.document_cleaner.models import CleanedDocument, CleaningConfig, CleaningReport
from app.document_cleaner.normalizers import NORMALIZER_REGISTRY, normalize_linebreaks
from app.document_cleaner.removers import (
    remove_blank_pages,
    remove_duplicate_lines,
    remove_headers_footers,
    remove_ocr_artifacts,
    remove_page_numbers,
    remove_running_titles,
    remove_watermark_text,
)

logger = logging.getLogger(__name__)


class DocumentCleaner:
    """High-performance legal document text cleaner.

    Usage::

        cleaner = DocumentCleaner()
        result = cleaner.clean(raw_text)
        print(result.clean_text[:500])

    Thread-safe — one ``DocumentCleaner`` instance can be shared across threads.
    All instance state is read-only after construction.
    """

    def __init__(self, config: CleaningConfig | str | None = None) -> None:
        """Initialise the cleaner with an optional config dict or preset name.

        Args:
            config: A ``CleaningConfig`` instance, a preset string name
                (``"aggressive"``, ``"conservative"``, ``"ocr"``), or ``None``
                to use the aggressive preset.
        """
        if config is None:
            self.config = PRESETS["aggressive"]
        elif isinstance(config, str):
            self.config = PRESETS.get(config.lower())
            if self.config is None:
                msg = f"Unknown preset: {config}. Available: {list(PRESETS)}"
                raise ValueError(msg)
        else:
            self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clean(self, text: str) -> CleanedDocument:
        """Run the full cleaning pipeline on a document text.

        Args:
            text: Raw document text to clean.

        Returns:
            A ``CleanedDocument`` with the cleaned text and a cleaning report.
        """
        if not text:
            return CleanedDocument(
                clean_text="",
                report=CleaningReport(original_length=0, clean_length=0, total_chars_removed=0, total_items_removed=0),
            )

        original_length = len(text)
        all_removed: list[RemovedItem] = []
        lines = text.splitlines()

        # --- Phase 1: Line-level removal operations ---
        lines, items = self._run_removers(lines)
        all_removed.extend(items)

        # --- Phase 2: Rejoin lines and remove full-text OCR artifacts first ---
        cleaned = "\n".join(lines)
        if self.config.remove_ocr_artifacts:
            cleaned, items = remove_ocr_artifacts(cleaned)
            all_removed.extend(items)

        # --- Phase 3: Normalization (runs after OCR cleanup) ---
        cleaned = self._run_normalizers(cleaned)

        # --- Phase 4: Final line-break collapse ---
        if self.config.normalize_linebreaks:
            cleaned = normalize_linebreaks(cleaned)

        # Build report
        total_chars_removed = original_length - len(cleaned)
        total_items = sum(item.count for item in all_removed)

        report = CleaningReport(
            original_length=original_length,
            clean_length=len(cleaned),
            total_chars_removed=total_chars_removed,
            total_items_removed=total_items,
            removed_items=all_removed,
        )

        return CleanedDocument(clean_text=cleaned, report=report)

    # ------------------------------------------------------------------
    # Internal pipeline execution
    # ------------------------------------------------------------------

    def _run_removers(self, lines: list[str]) -> tuple[list[str], list[RemovedItem]]:
        """Run all configured removal operations on the line list."""
        all_removed: list[RemovedItem] = []

        # Order matters: blank removal first simplifies frequency analysis
        if self.config.remove_blank_pages:
            lines, items = remove_blank_pages(lines)
            all_removed.extend(items)

        if self.config.remove_headers or self.config.remove_footers:
            lines, items = remove_headers_footers(lines)
            all_removed.extend(items)

        if self.config.remove_running_titles:
            lines, items = remove_running_titles(lines)
            all_removed.extend(items)

        if self.config.remove_page_numbers:
            lines, items = remove_page_numbers(lines)
            all_removed.extend(items)

        if self.config.remove_watermark_text:
            lines, items = remove_watermark_text(lines)
            all_removed.extend(items)

        if self.config.remove_duplicate_lines:
            lines, items = remove_duplicate_lines(lines)
            all_removed.extend(items)

        return lines, all_removed

    def _run_normalizers(self, text: str) -> str:
        """Run all configured normalization operations on the text.

        Normalizer function names in ``NORMALIZER_REGISTRY`` use the naming
        convention ``normalize_<field_name>`` so they match ``CleaningConfig``
        field names automatically (e.g., ``normalize_spaces`` ↔ ``normalize_spaces``).
        """
        for name, func in NORMALIZER_REGISTRY:
            # The config field name matches the normalizer function name
            # (e.g., normalize_unicode -> config.normalize_unicode)
            config_field = f"normalize_{name}" if not name.startswith("normalize_") else name
            # Special cases: trailing_whitespace shares config with linebreaks
            if name == "trailing_whitespace":
                config_field = "normalize_linebreaks"
            if getattr(self.config, config_field, True):
                try:
                    text = func(text)
                except Exception as exc:
                    logger.warning("Normalizer '%s' failed: %s", name, exc)
        return text

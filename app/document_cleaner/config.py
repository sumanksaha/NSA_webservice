"""
Configuration presets for the Legal Document Cleaning Pipeline.

Provides pre-built ``CleaningConfig`` instances for common use cases:
- ``AGGRESSIVE`` — strip everything non-essential
- ``CONSERVATIVE`` — preserve formatting, only remove clearly noisy artifacts
- ``OCR`` — aggressive cleaning tuned for OCR-extracted text
"""

from __future__ import annotations

from app.document_cleaner.models import CleaningConfig

PRESETS = {
    "aggressive": CleaningConfig(
        remove_headers=True,
        remove_footers=True,
        remove_page_numbers=True,
        remove_blank_pages=True,
        remove_watermark_text=True,
        remove_duplicate_lines=True,
        remove_running_titles=True,
        remove_ocr_artifacts=True,
        normalize_unicode=True,
        normalize_spaces=True,
        normalize_tabs=True,
        normalize_linebreaks=True,
        normalize_hyphens=True,
        normalize_quotes=True,
        normalize_bullets=True,
        normalize_encoding=True,
    ),
    "conservative": CleaningConfig(
        remove_headers=True,
        remove_footers=True,
        remove_page_numbers=True,
        remove_blank_pages=True,
        remove_watermark_text=True,
        remove_duplicate_lines=False,
        remove_running_titles=True,
        remove_ocr_artifacts=True,
        normalize_unicode=True,
        normalize_spaces=True,
        normalize_tabs=True,
        normalize_linebreaks=False,
        normalize_hyphens=False,
        normalize_quotes=False,
        normalize_bullets=False,
        normalize_encoding=True,
    ),
    "ocr": CleaningConfig(
        remove_headers=True,
        remove_footers=True,
        remove_page_numbers=True,
        remove_blank_pages=True,
        remove_watermark_text=True,
        remove_duplicate_lines=True,
        remove_running_titles=True,
        remove_ocr_artifacts=True,
        normalize_unicode=True,
        normalize_spaces=True,
        normalize_tabs=True,
        normalize_linebreaks=True,
        normalize_hyphens=True,
        normalize_quotes=True,
        normalize_bullets=True,
        normalize_encoding=True,
    ),
}

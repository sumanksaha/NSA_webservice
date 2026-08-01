"""Comprehensive tests for the Legal Document Cleaning Pipeline.

Covers:
- Model validation
- Removers (page numbers, watermarks, blanks, duplicates, headers/footers, running titles, OCR artifacts)
- Normalizers (unicode, spaces, tabs, line breaks, hyphens, quotes, bullets, encoding)
- Pipeline integration with config presets
- Edge cases (empty text, whitespace only, very short text)
- Diff report generation
"""

from __future__ import annotations

import pytest

from app.document_cleaner import CleanedDocument, CleaningConfig, DocumentCleaner
from app.document_cleaner.differ import DocumentDiffer
from app.document_cleaner.models import CleaningReport, RemovedItem
from app.document_cleaner.normalizers import (
    normalize_bullets,
    normalize_encoding,
    normalize_hyphens,
    normalize_linebreaks,
    normalize_quotes,
    normalize_spaces,
    normalize_tabs,
    normalize_unicode,
)
from app.document_cleaner.removers import (
    _should_preserve,
    remove_blank_pages,
    remove_duplicate_lines,
    remove_headers_footers,
    remove_ocr_artifacts,
    remove_page_numbers,
    remove_running_titles,
    remove_watermark_text,
)

# ============================================================================
# Model tests
# ============================================================================


class TestModels:
    def test_cleaning_config_defaults(self):
        cfg = CleaningConfig()
        assert cfg.remove_headers is True
        assert cfg.preserve_citations is True

    def test_cleaning_config_custom(self):
        cfg = CleaningConfig(remove_headers=False, normalize_quotes=False)
        assert cfg.remove_headers is False
        assert cfg.remove_footers is True  # default
        assert cfg.normalize_quotes is False

    def test_removed_item(self):
        item = RemovedItem(category="header", snippet="Page 1 of 10", count=5, chars_saved=80)
        assert item.category == "header"
        assert item.count == 5

    def test_cleaning_report(self):
        report = CleaningReport(
            original_length=1000,
            clean_length=700,
            total_chars_removed=300,
            total_items_removed=10,
            removed_items=[
                RemovedItem(category="page_number", snippet="1", count=5, chars_saved=20),
            ],
        )
        assert report.compression_ratio == pytest.approx(1.4286, rel=1e-3)

    def test_cleaned_document(self):
        report = CleaningReport(
            original_length=100,
            clean_length=80,
            total_chars_removed=20,
            total_items_removed=2,
        )
        doc = CleanedDocument(clean_text="clean text", report=report)
        assert doc.clean_text == "clean text"
        assert doc.report.clean_length == 80


# ============================================================================
# Remover tests
# ============================================================================


class TestRemovers:
    def test_remove_page_numbers_standalone(self):
        lines = [
            "Some text here",
            "Page 1",
            "More content",
            "- 2 -",
            "Final paragraph",
            "3 of 10",
        ]
        kept, items = remove_page_numbers(lines)
        assert len(items) == 1
        assert items[0].count == 3  # three page number lines removed
        assert "Page 1" not in kept
        assert "- 2 -" not in kept
        assert "Some text here" in kept
        assert "More content" in kept
        assert "Final paragraph" in kept

    def test_remove_page_numbers_none(self):
        lines = ["No numbers here", "Just text", "Page something not matching"]
        kept, items = remove_page_numbers(lines)
        assert len(items) == 0
        assert kept == lines

    def test_remove_watermark_text(self):
        lines = [
            "CONFIDENTIAL",
            "Regular text here",
            "DRAFT",
            "More text",
            "DO NOT COPY",
            "Final line",
        ]
        kept, items = remove_watermark_text(lines)
        assert len(items) >= 1
        assert "CONFIDENTIAL" not in kept
        assert "DRAFT" not in kept
        assert "Regular text here" in kept

    def test_remove_blank_pages(self):
        lines = ["Line one", "", "   ", "", "Line two", ""]
        kept, items = remove_blank_pages(lines)
        assert len(kept) == 2  # non-blank lines only
        assert items[0].count == 4  # 4 blank lines removed

    def test_remove_duplicate_lines(self):
        lines = ["A", "A", "B", "C", "C", "D"]
        kept, items = remove_duplicate_lines(lines)
        assert len(items) >= 1
        # Consecutive duplicates removed
        assert kept == ["A", "B", "C", "D"]

    def test_remove_duplicate_lines_preserves_nonconsecutive(self):
        lines = ["A", "B", "A", "C"]
        kept, _items = remove_duplicate_lines(lines)
        assert kept == lines  # no consecutive duplicates

    def test_remove_ocr_artifacts(self):
        text = "Hello\x00World\u0000Test\nNormal text here."
        cleaned, items = remove_ocr_artifacts(text)
        assert "\x00" not in cleaned
        assert "HelloWorld" in cleaned
        assert items[0].count > 0

    def test_remove_headers_footers_too_short_returns_early(self):
        # Fewer than 20 lines returns early
        lines = ["Header"] * 5
        kept, items = remove_headers_footers(lines)
        assert len(items) == 0
        assert len(kept) == 5

    def test_remove_headers_footers_too_short(self):
        lines = ["Only", "a", "few", "lines"]
        _kept, items = remove_headers_footers(lines)
        assert len(items) == 0

    def test_remove_running_titles_protects_preserved_lines(self):
        # A line like "SCHEDULE I" matches both the running title pattern
        # AND a preservation pattern -- should NOT be removed
        lines = ["SCHEDULE I", "Content", "SCHEDULE I", "More content"]
        # _should_preserve should catch it
        assert _should_preserve("SCHEDULE I") is True
        kept, items = remove_running_titles(lines)
        # It appears twice but is preserved
        assert len(items) == 0
        assert "SCHEDULE I" in kept

    def test_remove_running_titles(self):
        lines = [
            "CHAPTER OVERVIEW",
            "Some content here",
            "CHAPTER OVERVIEW",
            "More content",
            "CHAPTER OVERVIEW",
            "Final content",
        ]
        kept, items = remove_running_titles(lines)
        assert len(items) >= 1
        assert "CHAPTER OVERVIEW" not in kept

    def test_remove_running_titles_unique(self):
        lines = ["Unique Title", "Content", "Different Title"]
        _kept, items = remove_running_titles(lines)
        assert len(items) == 0


# ============================================================================
# Normalizer tests
# ============================================================================


class TestNormalizers:
    def test_normalize_unicode(self):
        # Decomposed é -> combined é
        decomposed = "e\u0301"  # e + combining accent
        result = normalize_unicode(decomposed)
        assert result == "\u00e9"  # single codepoint é

    def test_normalize_spaces(self):
        result = normalize_spaces("Hello    world   foo")
        assert result == "Hello world foo"

    def test_normalize_spaces_keeps_newlines(self):
        result = normalize_spaces("Hello   world\nfoo   bar")
        assert result == "Hello world\nfoo bar"

    def test_normalize_tabs(self):
        result = normalize_tabs("Hello\tworld\t\tfoo")
        assert result == "Hello world foo"

    def test_normalize_linebreaks(self):
        result = normalize_linebreaks("a\n\n\n\n\nb\n\nc")
        assert result == "a\n\nb\n\nc"

    def test_normalize_hyphens(self):
        result = normalize_hyphens("This is a compu-\nter system.")
        assert "computer" in result

    def test_normalize_hyphens_no_match(self):
        text = "Normal text without hyphenation."
        assert normalize_hyphens(text) == text

    def test_normalize_quotes(self):
        text = "\u201cHello\u201d and \u2018world\u2019"
        result = normalize_quotes(text)
        assert result == "\"Hello\" and 'world'"

    def test_normalize_bullets(self):
        text = "\u2022 Item one\n\u2022 Item two"
        result = normalize_bullets(text)
        assert result == "* Item one\n* Item two"

    def test_normalize_encoding(self):
        text = "Hello\u00a0world\u200btest"
        result = normalize_encoding(text)
        assert "\u00a0" not in result
        assert "\u200b" not in result
        assert "Hello world" in result  # nbsp -> space


# ============================================================================
# Pipeline integration tests
# ============================================================================


class TestDocumentCleaner:
    def test_empty_text(self):
        cleaner = DocumentCleaner()
        result = cleaner.clean("")
        assert result.clean_text == ""
        assert result.report.original_length == 0

    def test_whitespace_only(self):
        cleaner = DocumentCleaner()
        result = cleaner.clean("   \n  \n  ")
        # Should be collapsed to empty or near-empty
        assert result.report.clean_length <= result.report.original_length

    def test_clean_text_basic(self):
        cleaner = DocumentCleaner()
        text = "Hello   world\n\n\n\nThis is a  test."
        result = cleaner.clean(text)
        # Normalization should collapse spaces and line breaks
        assert "  " not in result.clean_text
        assert "\n\n\n" not in result.clean_text

    def test_clean_removes_page_numbers(self):
        cleaner = DocumentCleaner()
        text = "Some text\nPage 1\nMore text\n- 2 -\nFinal"
        result = cleaner.clean(text)
        assert "Page 1" not in result.clean_text
        assert "- 2 -" not in result.clean_text

    def test_clean_removes_watermarks(self):
        cleaner = DocumentCleaner()
        text = "CONFIDENTIAL\nReal content\nDRAFT\nMore real content"
        result = cleaner.clean(text)
        assert "CONFIDENTIAL" not in result.clean_text
        assert "DRAFT" not in result.clean_text
        assert "Real content" in result.clean_text

    def test_clean_normalizes_quotes(self):
        cleaner = DocumentCleaner()
        text = "\u201cQuoted text\u201d"
        result = cleaner.clean(text)
        assert '"' in result.clean_text
        assert "\u201c" not in result.clean_text

    def test_clean_preset_aggressive(self):
        cleaner = DocumentCleaner("aggressive")
        text = "Page 1\nCONFIDENTIAL\nHello world\nDRAFT\n"
        result = cleaner.clean(text)
        assert "Page 1" not in result.clean_text
        assert "CONFIDENTIAL" not in result.clean_text

    def test_clean_preset_conservative(self):
        cleaner = DocumentCleaner("conservative")
        text = "Page 1\nHello   world\n\n\n\nFinal"
        result = cleaner.clean(text)
        # Conservative normalizes spaces but not line breaks
        # Actually it does normalize linebreaks=False so excess newlines kept
        assert "Hello   world" in result.clean_text or "Hello world" in result.clean_text

    def test_clean_preset_ocr(self):
        cleaner = DocumentCleaner("ocr")
        text = "Page 1\n\x00Hello world\x00\nDRAFT"
        result = cleaner.clean(text)
        assert "\x00" not in result.clean_text
        assert "Hello world" in result.clean_text

    def test_clean_with_custom_config(self):
        cfg = CleaningConfig(remove_page_numbers=False, normalize_unicode=True)
        cleaner = DocumentCleaner(cfg)
        text = "Page 1\nHello world"
        result = cleaner.clean(text)
        # Page numbers should NOT be removed
        assert "Page 1" in result.clean_text

    def test_report_is_populated(self):
        cleaner = DocumentCleaner()
        text = "Hello world\n" * 50
        result = cleaner.clean(text)
        assert result.report.original_length > 0
        assert result.report.clean_length > 0
        assert result.report.total_chars_removed >= 0
        assert isinstance(result.report.removed_items, list)

    def test_invalid_preset(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            DocumentCleaner("nonexistent_preset")

    def test_long_text_performance(self):
        """Basic sanity: processing 10k+ chars should not hang."""
        cleaner = DocumentCleaner()
        text = "Paragraph one.\n\n" * 500
        result = cleaner.clean(text)
        assert len(result.clean_text) < len(text)  # should reduce size


# ============================================================================
# Differ tests
# ============================================================================


class TestDocumentDiffer:
    def test_diff_no_changes(self):
        differ = DocumentDiffer()
        text = "Line one\nLine two\nLine three"
        result = differ.diff(text, text)
        assert result["lines_unchanged"] == 3
        assert result["lines_removed"] == 0

    def test_diff_with_removals(self):
        differ = DocumentDiffer()
        original = "Line one\nREMOVED\nLine three\nREMOVED2"
        cleaned = "Line one\nLine three"
        result = differ.diff(original, cleaned)
        assert result["lines_removed"] == 2
        assert result["lines_unchanged"] == 2

    def test_diff_with_changes(self):
        differ = DocumentDiffer()
        original = "Hello world\nLine two"
        cleaned = "Hello there\nLine two"
        result = differ.diff(original, cleaned)
        assert result["lines_changed"] > 0

    def test_summary_text(self):
        differ = DocumentDiffer()
        result = differ.diff("A\nB\nC", "A\nC")
        summary = differ.summary_text(result)
        assert "Cleaning Diff Summary" in summary
        assert "removed" in summary.lower()

    def test_unified_diff(self):
        differ = DocumentDiffer()
        diff_str = differ.unified_diff("A\nB\nC\n", "A\nC\n")
        assert diff_str  # non-empty
        assert "-B" in diff_str or "+" in diff_str

"""
Comprehensive unit tests for the Legal Document Loader module.

Tests cover:
- Model validation (DocumentResult, PageResult, FileMetadata)
- Factory dispatch (PDF, DOCX, TXT)
- Edge cases: empty files, missing files, unsupported types
- Text cleaning helpers
- Batch processor file discovery
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.document_loader import (
    BaseLoader,
    BatchProcessor,
    DocumentLoaderFactory,
    DocumentResult,
    FileMetadata,
    PageResult,
)
from app.document_loader.txt_loader import TXTLoader

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    """Create a sample plain-text file."""
    path = tmp_path / "sample.txt"
    path.write_text("Hello, world!\nThis is page 1 content.", encoding="utf-8")
    return path


@pytest.fixture
def multi_page_txt(tmp_path: Path) -> Path:
    """Create a long text file (still single page for TXT)."""
    lines = [f"This is line {i} of a longer document." for i in range(1, 101)]
    path = tmp_path / "long.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture
def empty_txt(tmp_path: Path) -> Path:
    """Create an empty text file."""
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def non_utf8_txt(tmp_path: Path) -> Path:
    """Create a Latin-1 encoded text file to test encoding detection."""
    path = tmp_path / "latin1.txt"
    # "café" in Latin-1
    path.write_bytes(b"caf\xe9 cr\xe8me")
    return path


@pytest.fixture
def unsupported_file(tmp_path: Path) -> Path:
    """Create a file with an unsupported extension."""
    path = tmp_path / "notes.csv"
    path.write_text("a,b,c\n1,2,3", encoding="utf-8")
    return path


@pytest.fixture
def batch_dir(tmp_path: Path) -> Path:
    """Create a directory with multiple TXT files for batch testing."""
    d = tmp_path / "batch_input"
    d.mkdir()
    for i in range(5):
        (d / f"doc_{i}.txt").write_text(f"Content of document {i}.", encoding="utf-8")
    # Add one unsupported file (should be skipped)
    (d / "notes.csv").write_text("a,b,c", encoding="utf-8")
    # Add one empty file
    (d / "empty.txt").write_text("", encoding="utf-8")
    return d


# ============================================================================
# Model tests
# ============================================================================


class TestDocumentResult:
    """Tests for the DocumentResult model."""

    def test_minimal_document(self):
        """A DocumentResult can be created with just file_name and file_type."""
        meta = FileMetadata(file_size_bytes=100)
        doc = DocumentResult(file_name="test.pdf", file_type="pdf", metadata=meta)
        assert doc.document_id is not None
        assert doc.file_name == "test.pdf"
        assert doc.file_type == "pdf"
        assert doc.pages == []
        assert doc.text == ""

    def test_file_type_normalised(self):
        """File type is normalised (strips dots, lowercases)."""
        meta = FileMetadata(file_size_bytes=50)
        doc = DocumentResult(file_name="test.PDF", file_type=".PDF", metadata=meta)
        assert doc.file_type == "pdf"

    def test_invalid_file_type_raises(self):
        """An unsupported file type raises a ValueError."""
        meta = FileMetadata(file_size_bytes=50)
        with pytest.raises(ValueError, match="Unsupported file type"):
            DocumentResult(file_name="test.xls", file_type="xls", metadata=meta)

    def test_text_property_concatenates_pages(self):
        """The ``text`` property joins all pages with double newline."""
        meta = FileMetadata(file_size_bytes=100)
        doc = DocumentResult(
            file_name="test.pdf",
            file_type="pdf",
            metadata=meta,
            pages=[
                PageResult(page=1, text="Page one"),
                PageResult(page=2, text="Page two"),
            ],
        )
        assert doc.text == "Page one\n\nPage two"
        assert doc.total_pages == 2

    def test_serialises_to_json(self):
        """DocumentResult can be serialised to JSON via model_dump_json."""
        meta = FileMetadata(file_size_bytes=100, page_count=2)
        doc = DocumentResult(
            file_name="test.txt",
            file_type="txt",
            metadata=meta,
            pages=[PageResult(page=1, text="Hello")],
        )
        raw = doc.model_dump_json(indent=2)
        parsed = json.loads(raw)
        assert parsed["file_name"] == "test.txt"
        assert parsed["file_type"] == "txt"
        assert len(parsed["pages"]) == 1
        assert parsed["pages"][0]["text"] == "Hello"
        assert parsed["metadata"]["page_count"] == 2


class TestPageResult:
    """Tests for the PageResult model."""

    def test_minimal_page(self):
        page = PageResult(page=1, text="Some text")
        assert page.page == 1
        assert page.text == "Some text"

    def test_page_number_must_be_positive(self):
        with pytest.raises(ValueError):
            PageResult(page=0, text="invalid")


class TestFileMetadata:
    """Tests for the FileMetadata model."""

    def test_minimal_metadata(self):
        meta = FileMetadata(file_size_bytes=1024)
        assert meta.file_size_bytes == 1024
        assert meta.created_at is None
        assert meta.page_count is None


# ============================================================================
# TXT loader tests
# ============================================================================


class TestTXTLoader:
    """Tests for the plain-text loader."""

    def test_loads_text_file(self, sample_txt: Path):
        doc = DocumentLoaderFactory.load(sample_txt)
        assert doc.file_type == "txt"
        assert doc.file_name == "sample.txt"
        assert doc.total_pages == 1
        assert "Hello, world!" in doc.text

    def test_text_cleaning_normalises_whitespace(self, sample_txt: Path):
        doc = DocumentLoaderFactory.load(sample_txt)
        # Text should be stripped of leading/trailing whitespace
        assert doc.text == doc.text.strip()

    def test_empty_file(self, empty_txt: Path):
        doc = DocumentLoaderFactory.load(empty_txt)
        assert doc.total_pages == 1
        assert doc.text == ""

    def test_non_utf8_file(self, non_utf8_txt: Path):
        doc = DocumentLoaderFactory.load(non_utf8_txt)
        assert "café" in doc.text
        assert doc.metadata.encoding is not None

    def test_long_file_truncation(self, multi_page_txt: Path):
        doc = DocumentLoaderFactory.load(multi_page_txt, max_page_chars=50)
        assert len(doc.text) <= 50 + len("\n… [TRUNCATED]")
        assert doc.text.endswith("[TRUNCATED]")

    def test_metadata_includes_file_size(self, sample_txt: Path):
        doc = DocumentLoaderFactory.load(sample_txt)
        assert doc.metadata.file_size_bytes > 0
        assert doc.metadata.page_count == 1
        assert doc.metadata.created_at is not None
        assert doc.metadata.modified_at is not None


# ============================================================================
# Factory tests
# ============================================================================


class TestDocumentLoaderFactory:
    """Tests for the factory/dispatcher."""

    def test_resolves_txt_loader(self, sample_txt: Path):
        loader = DocumentLoaderFactory._resolve_loader(sample_txt)
        assert loader is TXTLoader

    def test_unsupported_extension_raises(self, unsupported_file: Path):
        with pytest.raises(ValueError, match="Unsupported file extension"):
            DocumentLoaderFactory.load(unsupported_file)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            DocumentLoaderFactory.load("/nonexistent/file.pdf")

    def test_supported_extensions(self):
        exts = DocumentLoaderFactory.supported_extensions()
        assert ".pdf" in exts
        assert ".docx" in exts
        assert ".txt" in exts

    def test_is_supported(self):
        assert DocumentLoaderFactory.is_supported("doc.pdf")
        assert DocumentLoaderFactory.is_supported("doc.docx")
        assert DocumentLoaderFactory.is_supported("doc.txt")
        assert not DocumentLoaderFactory.is_supported("doc.csv")
        assert not DocumentLoaderFactory.is_supported("doc")


# ============================================================================
# Base loader text-cleaning tests
# ============================================================================


class TestBaseLoaderHelpers:
    """Tests for static helper methods on BaseLoader."""

    def test_clean_text_removes_non_breaking_spaces(self):
        result = BaseLoader._clean_text("hello\u00a0world")
        assert result == "hello world"

    def test_clean_text_normalises_unicode(self):
        # "café" in composed (NFC) vs decomposed (NFD) form
        composed = "caf\u00e9"
        decomposed = "cafe\u0301"
        result = BaseLoader._clean_text(decomposed)
        assert result == composed  # NFKC normalisation

    def test_clean_text_collapses_excessive_newlines(self):
        result = BaseLoader._clean_text("a\n\n\n\n\nb")
        assert result == "a\n\nb"

    def test_clean_text_strips_trailing_spaces(self):
        result = BaseLoader._clean_text("  hello world  \n  second line  ")
        assert result == "hello world\nsecond line"

    def test_truncate_page_short_text(self):
        result = BaseLoader._truncate_page("Hello", max_chars=100)
        assert result == "Hello"

    def test_truncate_page_long_text(self):
        text = "A" * 1000
        result = BaseLoader._truncate_page(text, max_chars=100)
        assert len(result) == 100 + len("\n… [TRUNCATED]")
        assert "[TRUNCATED]" in result


# ============================================================================
# Batch processor tests
# ============================================================================


class TestBatchProcessor:
    """Tests for the batch processor."""

    def test_collects_supported_files(self, batch_dir: Path):
        bp = BatchProcessor(batch_dir, batch_dir.parent / "output", workers=2)
        files = bp._collect_files()
        # 5 doc_*.txt + 1 empty.txt = 6 supported files
        # notes.csv is excluded
        assert len(files) == 6
        assert all(f.suffix in {".txt"} for f in files)

    def test_non_recursive_collection(self, batch_dir: Path):
        # Create a subdirectory
        sub = batch_dir / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("deep", encoding="utf-8")
        bp = BatchProcessor(batch_dir, batch_dir.parent / "output", recursive=False)
        files = bp._collect_files()
        # Without recursive, should NOT include deep.txt
        assert all("deep.txt" not in f.name for f in files)

    def test_batch_summary_counts(self, batch_dir: Path):
        bp = BatchProcessor(
            batch_dir,
            batch_dir.parent / "output",
            workers=2,
            jsonl_batch_size=100,
        )
        summary = bp.run()
        assert summary.total_files == 6
        # 5 doc_*.txt should succeed, empty.txt should succeed (empty page)
        assert summary.success_count == 6
        assert summary.fail_count == 0
        assert summary.total_pages == 6
        assert summary.total_duration_seconds > 0
        assert summary.throughput_per_second > 0

    def test_batch_output_jsonl_exists(self, batch_dir: Path):
        output_dir = batch_dir.parent / "output"
        bp = BatchProcessor(batch_dir, output_dir, workers=2)
        bp.run()
        # Verify output file was created
        jsonl_files = list(output_dir.glob("documents_*.jsonl"))
        assert len(jsonl_files) >= 1
        # Verify it has content
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert len(content.strip().split("\n")) == 6  # one line per doc

    def test_batch_empty_directory(self, tmp_path: Path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        bp = BatchProcessor(empty_dir, tmp_path / "out")
        summary = bp.run()
        assert summary.total_files == 0
        assert summary.success_count == 0

    def test_batch_invalid_input_dir(self):
        with pytest.raises(NotADirectoryError):
            BatchProcessor("/nonexistent/path", "/tmp/out")


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_missing_pdf_graceful_error(self, tmp_path: Path):
        """Loading a non-existent PDF raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            DocumentLoaderFactory.load(tmp_path / "ghost.pdf")

    def test_binary_file_as_txt(self, tmp_path: Path):
        """Loading a binary file as .txt should not crash."""
        path = tmp_path / "binary.txt"
        path.write_bytes(bytes(range(256)))
        doc = DocumentLoaderFactory.load(path)
        assert doc.total_pages == 1
        # Should have some text content (decoded with replacement)
        assert isinstance(doc.text, str)

    def test_unicode_file(self, tmp_path: Path):
        """Text with Unicode characters like Hindi/Chinese."""
        path = tmp_path / "unicode.txt"
        path.write_text("हिन्दी नमस्ते\n你好世界\n日本語", encoding="utf-8")
        doc = DocumentLoaderFactory.load(path)
        assert "हिन्दी" in doc.text
        assert "你好" in doc.text
        assert "日本語" in doc.text

    def test_file_with_long_lines(self, tmp_path: Path):
        """A file with a very long single line."""
        long_line = "word " * 10000
        path = tmp_path / "long_line.txt"
        path.write_text(long_line, encoding="utf-8")
        doc = DocumentLoaderFactory.load(path, max_page_chars=500)
        assert len(doc.text) <= 500 + len("\n… [TRUNCATED]")

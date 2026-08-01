"""Tests for the Enterprise OCR Pipeline module.

Tests cover:
- Model validation (OCRResult, DetectedObject, PageDetectionResult)
- OCR decision engine (selectable text detection)
- Image preprocessing (individual steps)
- Pipeline orchestration
- Integration test with generated test PDF
- Batch processor
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.ocr_pipeline import (
    DetectedObject,
    ObjectType,
    OCRBatchProcessor,
    OCRDecisionEngine,
    OCRPipeline,
    OCRResult,
    PageDetectionResult,
)
from app.ocr_pipeline.preprocessing import ImagePreprocessor, PreprocessingStep

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_image() -> np.ndarray:
    """Create a synthetic grayscale image with text-like patterns."""
    img = np.ones((400, 800), dtype=np.uint8) * 255  # white background
    # Add some dark blocks (simulating text)
    for i in range(5):
        x = 50 + i * 150
        y = 100 + i * 30
        img[y : y + 15, x : x + 80] = 0  # black rectangle
    return img


@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    """Create a minimal PDF with selectable text for testing."""
    path = tmp_path / "text_page.pdf"
    try:
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 100), "This is a test document with selectable text.", fontsize=12)
        page.insert_text((50, 130), "Page 1 content for testing purposes.", fontsize=12)
        doc.save(str(path))
        doc.close()
    except ImportError:
        # Create a simple placeholder if fitz not available
        path.write_bytes(b"%PDF-1.4 placeholder")
    return path


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    """Create a minimal PDF with no text (simulates scanned image-only PDF)."""
    path = tmp_path / "scanned.pdf"
    try:
        import fitz

        doc = fitz.open()
        doc.new_page()  # Blank page — no text inserted
        doc.save(str(path))
        doc.close()
    except ImportError:
        path.write_bytes(b"%PDF-1.4 placeholder")
    return path


@pytest.fixture
def batch_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create a directory with test PDFs for batch processing."""
    d = tmp_path / "batch_input"
    d.mkdir()
    output = tmp_path / "batch_output"
    output.mkdir(exist_ok=True)

    try:
        import fitz

        for i in range(3):
            pdf_path = d / f"doc_{i}.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((50, 100), f"Document {i} - Page 1", fontsize=12)
            if i == 0:
                # Add a second page to one document
                page2 = doc.new_page()
                page2.insert_text((50, 100), "Document 0 - Page 2", fontsize=12)
            doc.save(str(pdf_path))
            doc.close()
    except ImportError:
        for i in range(3):
            (d / f"doc_{i}.pdf").write_bytes(b"%PDF-1.4 placeholder")

    return d, output


# ============================================================================
# Model tests
# ============================================================================


class TestModels:
    """Tests for OCR pipeline Pydantic models."""

    def test_ocr_result_minimal(self):
        result = OCRResult(page=1, ocr_used=False)
        assert result.page == 1
        assert result.ocr_used is False
        assert result.confidence == 0.0
        assert result.text == ""
        assert result.language == "english"

    def test_ocr_result_with_text(self):
        result = OCRResult(
            page=1,
            ocr_used=True,
            confidence=0.95,
            language="hindi",
            text="नमस्ते",
            ocr_engine="paddle",
        )
        assert result.ocr_engine == "paddle"
        assert result.confidence == 0.95

    def test_ocr_result_error(self):
        result = OCRResult(page=1, ocr_used=True, error="Failed to open file")
        assert result.error is not None
        assert "Failed" in result.error

    def test_detected_object(self):
        obj = DetectedObject(type=ObjectType.TABLE, confidence=0.85, bbox=[10, 20, 100, 200])
        assert obj.type == ObjectType.TABLE
        assert obj.confidence == 0.85
        assert len(obj.bbox) == 4

    def test_page_detection_result(self):
        det = PageDetectionResult(
            objects=[
                DetectedObject(type=ObjectType.STAMP, confidence=0.9, bbox=[0, 0, 50, 50]),
            ],
            has_stamp=True,
            table_count=2,
        )
        assert det.has_stamp is True
        assert det.table_count == 2
        assert len(det.objects) == 1
        assert det.has_table is False


# ============================================================================
# Decision Engine tests
# ============================================================================


class TestDecisionEngine:
    """Tests for OCR decision engine."""

    def test_decision_on_text_pdf(self, text_pdf: Path):
        needs_ocr, text, result = OCRDecisionEngine.evaluate(text_pdf, 1)
        # If PyMuPDF is available, this PDF has text
        try:
            import fitz  # noqa: F401

            # Should detect text and NOT need OCR
            assert needs_ocr is False
            assert "test document" in text
            assert result.ocr_used is False
        except ImportError:
            # Without fitz, always needs OCR
            assert needs_ocr is True

    def test_decision_on_empty_pdf(self, empty_pdf: Path):
        needs_ocr, text, _result = OCRDecisionEngine.evaluate(empty_pdf, 1)
        # Empty/blank page should need OCR
        assert needs_ocr is True
        assert text == ""

    def test_decision_out_of_range_page(self, text_pdf: Path):
        needs_ocr, _text, result = OCRDecisionEngine.evaluate(text_pdf, 999)
        assert needs_ocr is True
        assert "out of range" in (result.error or "")

    def test_decision_missing_file(self):
        needs_ocr, _text, result = OCRDecisionEngine.evaluate("/nonexistent.pdf", 1)
        assert needs_ocr is True
        assert result.error is not None


# ============================================================================
# Image Preprocessing tests
# ============================================================================


class TestImagePreprocessor:
    """Tests for image preprocessing pipeline."""

    def test_grayscale_conversion(self, sample_image: np.ndarray):
        preprocessor = ImagePreprocessor()
        processed, steps = preprocessor.process(sample_image, steps=[PreprocessingStep.GRAYSCALE])
        assert len(processed.shape) == 2  # should be 2D after grayscale
        assert "grayscale" in steps

    def test_denoising(self, sample_image: np.ndarray):
        preprocessor = ImagePreprocessor()
        processed, steps = preprocessor.process(sample_image, steps=[PreprocessingStep.DENOISE])
        assert processed is not None
        assert "denoise" in steps

    def test_adaptive_threshold(self, sample_image: np.ndarray):
        preprocessor = ImagePreprocessor()
        processed, steps = preprocessor.process(sample_image, steps=[PreprocessingStep.THRESHOLD])
        assert processed is not None
        assert "adaptive_threshold" in steps

    def test_contrast_enhancement(self, sample_image: np.ndarray):
        preprocessor = ImagePreprocessor()
        processed, steps = preprocessor.process(sample_image, steps=[PreprocessingStep.CONTRAST])
        assert processed is not None
        assert "contrast_enhancement" in steps

    def test_deskew_no_skew(self, sample_image: np.ndarray):
        preprocessor = ImagePreprocessor()
        processed, _steps = preprocessor.process(sample_image, steps=[PreprocessingStep.DESKEW])
        assert processed is not None
        # No skew in synthetic image, so step may or may not be applied

    def test_full_pipeline(self, sample_image: np.ndarray):
        preprocessor = ImagePreprocessor()
        processed, steps = preprocessor.process(sample_image)
        assert processed is not None
        assert len(steps) > 0


# ============================================================================
# Pipeline integration tests
# ============================================================================


class TestOCRPipeline:
    """Integration tests for the full OCR pipeline."""

    def test_process_text_page(self, text_pdf: Path):
        pipeline = OCRPipeline(use_gpu=False)
        result = pipeline.process_page(text_pdf, 1)
        try:
            import fitz  # noqa: F401

            # With fitz, text should be extractable directly
            assert result.ocr_used is False
            assert result.confidence == 1.0
        except ImportError:
            # Without fitz, OCR will fail but should return gracefully
            assert result.error is not None or result.ocr_used is True

    def test_process_empty_page(self, empty_pdf: Path):
        pipeline = OCRPipeline(use_gpu=False)
        result = pipeline.process_page(empty_pdf, 1)
        # Empty page should require OCR
        assert result.ocr_used is True
        assert result.error is not None or result.text == ""

    def test_process_invalid_page(self, text_pdf: Path):
        pipeline = OCRPipeline(use_gpu=False)
        result = pipeline.process_page(text_pdf, 999)
        assert result.error is not None

    def test_process_document(self, text_pdf: Path):
        pipeline = OCRPipeline(use_gpu=False)
        results = pipeline.process_document(text_pdf)
        assert len(results) > 0
        for r in results:
            assert r.page >= 1


# ============================================================================
# Batch processor tests
# ============================================================================


class TestOCRBatchProcessor:
    """Tests for the batch OCR processor."""

    def test_batch_collects_pdfs(self, batch_dir: tuple[Path, Path]):
        input_dir, output_dir = batch_dir
        bp = OCRBatchProcessor(input_dir, output_dir, workers=2, use_gpu=False)
        pdfs = bp._collect_pdfs()
        assert len(pdfs) == 3  # 3 doc_*.pdf files

    def test_batch_processes_all_pages(self, batch_dir: tuple[Path, Path]):
        input_dir, output_dir = batch_dir
        bp = OCRBatchProcessor(input_dir, output_dir, workers=2, use_gpu=False)
        summary = bp.run()
        # 3 docs: doc_0 has 2 pages, doc_1 has 1, doc_2 has 1 = 4 total
        if summary.total_pages > 0:
            assert summary.total_pages == 4
            assert summary.success_count > 0
            assert summary.total_duration_seconds > 0

    def test_batch_output_jsonl(self, batch_dir: tuple[Path, Path]):
        input_dir, output_dir = batch_dir
        bp = OCRBatchProcessor(input_dir, output_dir, workers=2, use_gpu=False)
        bp.run()
        jsonl_files = list(output_dir.glob("ocr_*.jsonl"))
        assert len(jsonl_files) >= 1
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert len(content.strip().split("\n")) > 0

    def test_batch_invalid_input_dir(self, tmp_path: Path):
        with pytest.raises(NotADirectoryError):
            OCRBatchProcessor("/nonexistent", tmp_path / "out")


# ============================================================================
# Detector tests
# ============================================================================


class TestDetectors:
    """Tests for page object detectors."""

    def test_empty_image_detection(self, sample_image: np.ndarray):
        from app.ocr_pipeline.detectors import PageDetector

        # 2D image won't have color-based detections
        result = PageDetector.detect_all(sample_image)
        assert result is not None
        # Synthetic image might trigger some detections
        assert isinstance(result.has_table, bool)

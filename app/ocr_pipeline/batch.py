"""Batch OCR Processor — designed for large-scale legal document OCR.

Features:
- Processes PDFs page-by-page with automatic OCR decision making.
- GPU acceleration with automatic CPU fallback.
- Per-page results in JSONL format (streaming, memory-safe).
- Progress bar via tqdm.
- Graceful per-page error handling (never aborts the batch).
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ocr_pipeline.pipeline import OCRPipeline

logger = logging.getLogger(__name__)

# Default number of parallel workers (I/O-bound for page rendering)
_DEFAULT_WORKERS = 4

# How often (in seconds) to log progress
_PROGRESS_LOG_INTERVAL = 30.0


# ---------------------------------------------------------------------------
# Fast JSON serialization (reuse from batch module or stdlib)
# ---------------------------------------------------------------------------
try:
    import orjson

    def _fast_dumps(obj: dict) -> str:
        return str(orjson.dumps(obj).decode("utf-8"))

except ImportError:
    import json

    def _fast_dumps(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class OCRPageResult:
    """Result of OCR processing for a single page."""

    file: str
    page: int
    success: bool
    ocr_used: bool = True
    confidence: float = 0.0
    language: str = "english"
    char_count: int = 0
    error: str | None = None
    duration_seconds: float = 0.0


@dataclass
class OCRBatchSummary:
    """Aggregated summary of a batch OCR run."""

    total_pages: int = 0
    success_count: int = 0
    fail_count: int = 0
    ocr_pages: int = 0
    direct_pages: int = 0
    total_chars: int = 0
    total_duration_seconds: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def throughput_per_second(self) -> float:
        if self.total_duration_seconds > 0:
            return self.total_pages / self.total_duration_seconds
        return 0.0

    @property
    def ocr_percentage(self) -> float:
        if self.total_pages > 0:
            return (self.ocr_pages / self.total_pages) * 100.0
        return 0.0


# ---------------------------------------------------------------------------
# Batch Processor
# ---------------------------------------------------------------------------


class OCRBatchProcessor:
    """Process a directory of legal PDFs through the OCR pipeline.

    Args:
        input_dir: Directory containing PDF files (recursive scan).
        output_dir: Directory for JSONL output files.
        workers: Number of parallel workers.
        languages: List of languages for OCR.
        use_gpu: Whether to attempt GPU acceleration.
        dpi: DPI for PDF page rendering.
        enable_detection: Whether to run object detection.

    """

    def __init__(
        self,
        input_dir: str | Path,
        output_dir: str | Path,
        workers: int = _DEFAULT_WORKERS,
        languages: list[str] | None = None,
        use_gpu: bool = True,
        dpi: int = 300,
        enable_detection: bool = True,
    ) -> None:
        self._input_dir = Path(input_dir)
        self._output_dir = Path(output_dir)
        self._workers = workers
        self._languages = languages or ["english"]
        self._use_gpu = use_gpu
        self._dpi = dpi
        self._enable_detection = enable_detection

        if not self._input_dir.is_dir():
            raise NotADirectoryError(f"Input directory not found: {self._input_dir}")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize pipeline once (PaddleOCR model loading is expensive)
        logger.info(
            "Initializing OCR pipeline (languages=%s, gpu=%s, dpi=%d, workers=%d)",
            self._languages,
            self._use_gpu,
            self._dpi,
            self._workers,
        )
        self._pipeline = OCRPipeline(
            languages=self._languages,
            use_gpu=self._use_gpu,
            dpi=self._dpi,
            enable_detection=self._enable_detection,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> OCRBatchSummary:
        """Execute the batch OCR process."""
        pdf_files = self._collect_pdfs()
        if not pdf_files:
            logger.warning("No PDF files found in %s", self._input_dir)
            return OCRBatchSummary()

        logger.info("Batch OCR: %d PDFs found in %s", len(pdf_files), self._input_dir)
        return self._process_files(pdf_files)

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _collect_pdfs(self) -> list[Path]:
        """Recursively discover all PDF files."""
        pdfs: list[Path] = []
        for path in sorted(self._input_dir.rglob("*.pdf")):
            if path.is_file():
                pdfs.append(path)
        return pdfs

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _process_files(self, pdf_files: list[Path]) -> OCRBatchSummary:
        from tqdm import tqdm

        # Build page-level work units
        work_units: list[tuple[Path, int]] = []
        for pdf_path in pdf_files:
            try:
                import fitz

                doc = fitz.open(str(pdf_path))
                num_pages = len(doc)
                doc.close()
                for p in range(1, num_pages + 1):
                    work_units.append((pdf_path, p))
            except Exception as exc:
                logger.warning("Could not open %s: %s", pdf_path, exc)

        if not work_units:
            logger.warning("No processable pages found")
            return OCRBatchSummary()

        summary = OCRBatchSummary(total_pages=len(work_units))
        output_path = self._output_dir / f"ocr_{int(time.time())}.jsonl"

        progress = tqdm(total=len(work_units), unit="page", desc="OCR Pipeline")
        batch_buffer: list[str] = []
        last_log = time.monotonic()
        start_time = time.monotonic()
        processed_count = 0

        with ThreadPoolExecutor(max_workers=self._workers) as executor:
            future_map = {executor.submit(self._process_single, pdf, page): (pdf, page) for pdf, page in work_units}

            for future in as_completed(future_map):
                result: OCRPageResult = future.result()
                progress.update(1)
                processed_count += 1

                if result.success:
                    summary.success_count += 1
                    summary.total_chars += result.char_count
                    if result.ocr_used:
                        summary.ocr_pages += 1
                    else:
                        summary.direct_pages += 1
                else:
                    summary.fail_count += 1
                    summary.errors.append(
                        {
                            "file": result.file,
                            "page": result.page,
                            "error": result.error,
                        }
                    )

                line = _fast_dumps(
                    {
                        "file": result.file,
                        "page": result.page,
                        "success": result.success,
                        "ocr_used": result.ocr_used,
                        "confidence": round(result.confidence, 4),
                        "language": result.language,
                        "char_count": result.char_count,
                        "error": result.error,
                    }
                )
                batch_buffer.append(line)

                # Periodic flush (every 10k pages)
                if len(batch_buffer) >= 10_000:
                    self._flush_jsonl(output_path, batch_buffer)
                    batch_buffer.clear()

                # Periodic log
                elapsed = time.monotonic() - last_log
                if elapsed >= _PROGRESS_LOG_INTERVAL:
                    elapsed_total = time.monotonic() - start_time
                    rate = processed_count / elapsed_total if elapsed_total > 0 else 0
                    logger.info(
                        "Progress: %d/%d pages (%d fails, %d OCR) — %.1f pages/s",
                        processed_count,
                        summary.total_pages,
                        summary.fail_count,
                        summary.ocr_pages,
                        rate,
                    )
                    last_log = time.monotonic()

        # Final flush
        if batch_buffer:
            self._flush_jsonl(output_path, batch_buffer)

        progress.close()
        summary.total_duration_seconds = time.monotonic() - start_time
        logger.info(
            "OCR batch complete: %s (%d pages, %.1f/s)",
            output_path,
            summary.total_pages,
            summary.throughput_per_second,
        )
        return summary

    # ------------------------------------------------------------------
    # Single-page worker
    # ------------------------------------------------------------------

    def _process_single(self, pdf_path: Path, page: int) -> OCRPageResult:
        """Process a single page through the OCR pipeline."""
        start = time.monotonic()
        try:
            result = self._pipeline.process_page(pdf_path, page)
            elapsed = time.monotonic() - start

            if result.error:
                return OCRPageResult(
                    file=str(pdf_path),
                    page=page,
                    success=False,
                    error=result.error,
                    duration_seconds=elapsed,
                )

            return OCRPageResult(
                file=str(pdf_path),
                page=page,
                success=True,
                ocr_used=result.ocr_used,
                confidence=result.confidence,
                language=result.language,
                char_count=len(result.text),
                duration_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.debug("Failed page %d of %s: %s", page, pdf_path.name, exc)
            return OCRPageResult(
                file=str(pdf_path),
                page=page,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_seconds=elapsed,
            )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    @staticmethod
    def _flush_jsonl(path: Path, lines: list[str]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

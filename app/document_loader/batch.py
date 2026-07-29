"""
Batch document processor — designed for 100,000+ documents.

Features:
- Scans an input directory recursively for supported file types.
- Processes files in parallel using a configurable thread pool.
- Displays a real-time progress bar via ``tqdm``.
- Writes results as **JSON Lines** (one JSON object per line) for
  memory-safe streaming — no single giant JSON blob.
- Captures per-file errors without aborting the batch.
- Returns a summary dict with counts and timing.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.document_loader.loader import DocumentLoaderFactory

logger = logging.getLogger(__name__)

__all__ = [
    "BatchProcessor",
    "BatchResult",
    "BatchSummary",
]

# Default number of worker threads — limited by I/O, not CPU, for PDF/TXT/docx
_DEFAULT_WORKERS = 8

# How often (in seconds) to log a progress summary during batch processing
_PROGRESS_LOG_INTERVAL = 30.0


# ---------------------------------------------------------------------------
# Fast JSON serialization — prefer orjson (4-5x faster) over stdlib json
# ---------------------------------------------------------------------------
try:
    import orjson

    def _fast_dumps(obj: dict) -> str:
        return orjson.dumps(obj).decode("utf-8")

except ImportError:
    import json

    def _fast_dumps(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BatchResult:
    """Result of processing a single document in a batch."""

    file_path: str
    success: bool
    document_id: str | None = None
    file_type: str | None = None
    pages: int = 0
    chars: int = 0
    error: str | None = None
    duration_seconds: float = 0.0


@dataclass
class BatchSummary:
    """Aggregated summary of an entire batch run."""

    total_files: int = 0
    success_count: int = 0
    fail_count: int = 0
    total_pages: int = 0
    total_chars: int = 0
    total_duration_seconds: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)
    supported_extensions: set[str] = field(default_factory=set)

    @property
    def throughput_per_second(self) -> float:
        if self.total_duration_seconds > 0:
            return self.total_files / self.total_duration_seconds
        return 0.0


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------


class BatchProcessor:
    """Process a directory of legal documents in batch.

    Args:
        input_dir: Root directory to scan for documents (recursive).
        output_dir: Directory where ``.jsonl`` results will be written.
        workers: Number of parallel worker threads.
        recursive: Whether to scan subdirectories recursively.
        extensions: Optional set of extensions to include (e.g. ``{'.pdf'}``).
            Defaults to all supported extensions.
        max_page_chars: Optional character limit per page.
        jsonl_batch_size: Flush output file after this many documents.
    """

    def __init__(
        self,
        input_dir: str | Path,
        output_dir: str | Path,
        workers: int = _DEFAULT_WORKERS,
        recursive: bool = True,
        extensions: set[str] | None = None,
        max_page_chars: int | None = None,
        jsonl_batch_size: int = 10_000,
    ) -> None:
        self._input_dir = Path(input_dir)
        self._output_dir = Path(output_dir)
        self._workers = workers
        self._recursive = recursive
        self._extensions = extensions or DocumentLoaderFactory.supported_extensions()
        self._max_page_chars = max_page_chars
        self._jsonl_batch_size = jsonl_batch_size

        if not self._input_dir.is_dir():
            raise NotADirectoryError(f"Input directory not found: {self._input_dir}")

        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> BatchSummary:
        """Execute the batch process.

        Returns:
            A :class:`BatchSummary` with aggregate statistics.
        """
        files = self._collect_files()
        if not files:
            logger.warning("No supported documents found in %s", self._input_dir)
            return BatchSummary()

        logger.info(
            "Batch processing %d documents from %s (%d workers)",
            len(files),
            self._input_dir,
            self._workers,
        )

        start_time = time.monotonic()
        summary = self._process_files(files, start_time)
        summary.total_duration_seconds = time.monotonic() - start_time
        summary.supported_extensions = self._extensions

        logger.info(
            "Batch complete: %d success, %d failed, %d files in %.1fs (%.1f docs/s)",
            summary.success_count,
            summary.fail_count,
            summary.total_files,
            summary.total_duration_seconds,
            summary.throughput_per_second,
        )

        return summary

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _collect_files(self) -> list[Path]:
        """Recursively discover all supported files in the input directory."""
        pattern = "**/*" if self._recursive else "*"
        files: list[Path] = []
        for path in sorted(self._input_dir.glob(pattern)):
            if path.is_file() and path.suffix.lower() in self._extensions:
                files.append(path)
        return files

    # ------------------------------------------------------------------
    # Parallel processing
    # ------------------------------------------------------------------

    def _process_files(self, files: list[Path], start_time: float) -> BatchSummary:
        """Process files in parallel and write results to JSONL output."""
        from tqdm import tqdm

        summary = BatchSummary(total_files=len(files))
        output_path = self._output_dir / f"documents_{int(time.time())}.jsonl"

        last_log = time.monotonic()
        batch_buffer: list[str] = []
        progress = tqdm(total=len(files), unit="doc", desc="Loading documents")

        with ThreadPoolExecutor(max_workers=self._workers) as executor:
            # Submit all tasks
            future_map = {executor.submit(self._load_single, path): path for path in files}

            for future in as_completed(future_map):
                result: BatchResult = future.result()
                progress.update(1)

                if result.success:
                    summary.success_count += 1
                    summary.total_pages += result.pages
                    summary.total_chars += result.chars
                    line = _fast_dumps(
                        {
                            "document_id": result.document_id,
                            "file_name": Path(result.file_path).name,
                            "file_type": result.file_type,
                            "pages": result.pages,
                            "chars": result.chars,
                            "error": None,
                        },
                    )
                else:
                    summary.fail_count += 1
                    summary.errors.append({
                        "file": result.file_path,
                        "error": result.error,
                    })
                    line = _fast_dumps(
                        {
                            "file_name": Path(result.file_path).name,
                            "error": result.error,
                            "success": False,
                        },
                    )

                batch_buffer.append(line)

                # Flush periodically
                if len(batch_buffer) >= self._jsonl_batch_size:
                    self._flush_jsonl(output_path, batch_buffer)
                    batch_buffer.clear()

                # Periodic log
                elapsed = time.monotonic() - last_log
                if elapsed >= _PROGRESS_LOG_INTERVAL:
                    elapsed_total = time.monotonic() - start_time
                    logger.info(
                        "Progress: %d/%d (%d errors) — %.1f docs/s",
                        summary.success_count + summary.fail_count,
                        summary.total_files,
                        summary.fail_count,
                        summary.success_count / elapsed_total if elapsed_total > 0 else 0.0,
                    )
                    last_log = time.monotonic()

        # Final flush
        if batch_buffer:
            self._flush_jsonl(output_path, batch_buffer)

        progress.close()
        logger.info("Output written to %s", output_path)
        return summary

    # ------------------------------------------------------------------
    # Single-document worker
    # ------------------------------------------------------------------

    def _load_single(self, path: Path) -> BatchResult:
        """Load a single document and return a BatchResult."""
        start = time.monotonic()
        try:
            doc = DocumentLoaderFactory.load(path, max_page_chars=self._max_page_chars)
            elapsed = time.monotonic() - start
            return BatchResult(
                file_path=str(path),
                success=True,
                document_id=doc.document_id,
                file_type=doc.file_type,
                pages=doc.total_pages,
                chars=len(doc.text),
                duration_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.debug("Failed to load %s: %s", path.name, exc)
            return BatchResult(
                file_path=str(path),
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_seconds=elapsed,
            )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    @staticmethod
    def _flush_jsonl(path: Path, lines: list[str]) -> None:
        """Append lines to a JSONL file."""
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

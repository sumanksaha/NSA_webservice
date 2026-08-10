"""OCR Pipeline Orchestrator — the main entry point that:
1. Evaluates whether a PDF page has selectable text.
2. If YES: extracts text directly (no OCR).
3. If NO: runs the full OCR pipeline (preprocessing → detection → OCR).
4. Returns cleaned text with full metadata (confidence, language, detected objects).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.ocr_pipeline.decision import OCRDecisionEngine
from app.ocr_pipeline.detectors import PageDetector
from app.ocr_pipeline.models import OCRResult, PageDetectionResult
from app.ocr_pipeline.ocr_engine import OCREngine
from app.ocr_pipeline.preprocessing import ImagePreprocessor

logger = logging.getLogger(__name__)


class OCRPipeline:
    """End-to-end OCR pipeline for legal documents.

    Usage::

        pipeline = OCRPipeline(languages=["english", "hindi"])
        result = pipeline.process_page("document.pdf", page_number=1)
        print(result.model_dump_json(indent=2))

        # Process entire document
        results = pipeline.process_document("document.pdf")
    """

    def __init__(
        self,
        languages: list[str] | None = None,
        use_gpu: bool = True,
        dpi: int = 300,
        enable_detection: bool = True,
        engine: OCREngine | None = None,
    ) -> None:
        self._languages = languages or ["english"]
        self._use_gpu = use_gpu
        self._dpi = dpi
        self._enable_detection = enable_detection

        self._preprocessor = ImagePreprocessor(dpi=dpi)
        # Injectable engine (mock-injection pattern): tests substitute a fake
        # so unit runs never pay for the real model stack — especially
        # important now that EasyOCR is installed by default (a real CPU OCR
        # pass on a 300-DPI page takes minutes).
        self._ocr_engine = engine or OCREngine(
            languages=self._languages,
            use_gpu=self._use_gpu,
            preprocessor=self._preprocessor,
        )
        self._decision_engine = OCRDecisionEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_page(self, pdf_path: str | Path, page_number: int) -> OCRResult:
        """Process a single page through the OCR pipeline.

        Args:
            pdf_path: Path to the PDF file.
            page_number: 1-based page number.

        Returns:
            An :class:`OCRResult` with extracted text and metadata.

        """
        try:
            # Step 1: Evaluate whether OCR is needed
            needs_ocr, direct_text, partial = self._decision_engine.evaluate(pdf_path, page_number)

            if not needs_ocr:
                # Direct text extraction — no OCR needed
                logger.debug("Page %d: direct text extraction (%d chars)", page_number, len(direct_text))
                return partial

            # Step 2: OCR is needed — render page to image
            logger.info("Page %d: OCR required — running full pipeline", page_number)
            image = self._preprocessor.pdf_page_to_image(pdf_path, page_number)

            if image is None:
                return OCRResult(
                    page=page_number,
                    ocr_used=True,
                    error="Failed to render PDF page to image",
                )

            # Step 3: Preprocess the image
            processed_image, applied_steps = self._preprocessor.process(image)

            # Step 4: Object detection (tables, stamps, signatures, watermarks)
            # Step 4: Object detection (tables, stamps, signatures, watermarks)
            detection = PageDetector.detect_all(image) if self._enable_detection else PageDetectionResult()  # type: ignore[call-arg]

            # Step 5: Run OCR
            text, confidence, engine_name, language = self._ocr_engine.recognize(processed_image)

            return OCRResult(
                page=page_number,
                ocr_used=True,
                confidence=confidence,
                language=language,
                text=text,
                ocr_engine=engine_name,
                preprocessing_steps=applied_steps,
                detection=detection,
            )

        except Exception as exc:
            logger.exception("Failed to process page %d of %s", page_number, pdf_path)
            return OCRResult(
                page=page_number,
                ocr_used=True,
                error=f"Pipeline failed: {exc}",
            )

    def process_document(self, pdf_path: str | Path) -> list[OCRResult]:
        """Process all pages of a PDF document.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            A list of :class:`OCRResult` objects, one per page.

        """
        try:
            import fitz

            doc = fitz.open(str(pdf_path))
            total_pages = len(doc)
            doc.close()
        except Exception as exc:
            logger.error("Failed to open PDF %s: %s", pdf_path, exc)
            return []

        results: list[OCRResult] = []
        for page_num in range(1, total_pages + 1):
            result = self.process_page(pdf_path, page_num)
            results.append(result)

        return results

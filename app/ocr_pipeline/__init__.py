"""Enterprise-grade OCR Pipeline for Legal Documents.

Automatically detects whether a PDF page has selectable text and routes
to direct extraction or the full OCR pipeline (PaddleOCR → Tesseract).

Usage::

    from app.ocr_pipeline import OCRPipeline, OCRBatchProcessor

    # Single page
    result = OCRPipeline.process_page(pdf_path, page_number=1)
    print(result.model_dump_json(indent=2))

    # Full document
    results = OCRPipeline.process_document(pdf_path)
    for r in results:
        print(f"Page {r.page}: OCR={r.ocr_used}, conf={r.confidence:.2f}")

    # Batch
    bp = OCRBatchProcessor(input_dir="/data/pdfs", output_dir="/data/ocr")
    summary = bp.run()
"""

from app.ocr_pipeline.batch import OCRBatchProcessor
from app.ocr_pipeline.decision import OCRDecisionEngine
from app.ocr_pipeline.models import DetectedObject, ObjectType, OCRResult, PageDetectionResult
from app.ocr_pipeline.pipeline import OCRPipeline

__version__ = "0.1.0"

__all__ = [
    "DetectedObject",
    "OCRBatchProcessor",
    "OCRDecisionEngine",
    "OCRPipeline",
    "OCRResult",
    "ObjectType",
    "PageDetectionResult",
]

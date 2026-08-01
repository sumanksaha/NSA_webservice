# Module Memory: OCR Pipeline

## Purpose
Detect whether images/PDF pages contain machine text or require OCR (Tesseract),
then run OCR to extract text for downstream document ingestion.

## Responsibilities
- `detectors.py` — heuristic detectors (text density, image analysis) to decide
  OCR-needed vs. copy-from-PDF-text.
- `preprocessing.py` — image pre-processing (binarisation, deskew, noise reduce)
  for OCR quality.
- `ocr_engine.py` — pytesseract wrapper with lazy binary discovery.
- `decision.py` — routing logic (OCR vs. extract).
- `pipeline.py` — end-to-end OCR pipeline orchestration.
- `batch.py` — batch OCR over document collections.
- `models.py` — OCR-result dataclasses.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/ocr_pipeline/__init__.py` | 1 KB | Public exports |
| `app/ocr_pipeline/detectors.py` | 12 KB | OCR-needed detection |
| `app/ocr_pipeline/preprocessing.py` | 9 KB | Image prep |
| `app/ocr_pipeline/ocr_engine.py` | 8 KB | pytesseract wrapper |
| `app/ocr_pipeline/decision.py` | 5 KB | OCR vs extract decision |
| `app/ocr_pipeline/pipeline.py` | 5 KB | Orchestration |
| `app/ocr_pipeline/batch.py` | 11 KB | Batch OCR |
| `app/ocr_pipeline/models.py` | 3 KB | Result dataclasses |

## Public Interfaces
- `OCRPipeline`, `run_ocr(image_or_pdf)` → `OCRResult`.

## Dependencies
pytesseract, pdf2image, Pillow, pdfplumber/PyMuPDF, numpy.

## Configuration Files
- Tesseract binary must be on PATH (system dependency).

## Known Issues
- `detectors.py` & `preprocessing.py` have ruff `S110` ignore (swallow-except).
- Requires system Tesseract + poppler (pdf2image) binaries.

## Future Improvements
- Onnx/multilingual model fallback when Tesseract unavailable.

## Current TODOs
- OCR quality metrics integration.

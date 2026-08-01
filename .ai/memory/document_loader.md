# Module Memory: Document Loader

## Purpose
Ingest legal documents from PDF, DOCX, or plain-text sources, normalising them
into a uniform `LoadedDocument` representation for downstream cleaning/OCR.

## Responsibilities
- Abstract loader via `app/document_loader/base.py` (interface).
- Format-specific loaders: `pdf_loader` (pdfplumber + PyMuPDF), `docx_loader`
  (python-docx), `txt_loader` (chardet encoding detection).
- `batch.py` — orchestrates loading many documents with progress + result
  aggregation.
- `models.py` — `LoadedDocument` dataclass / result types.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/document_loader/__init__.py` | 1 KB | Public exports |
| `app/document_loader/base.py` | 3 KB | Abstract loader |
| `app/document_loader/loader.py` | 3 KB | Dispatch by extension |
| `app/document_loader/pdf_loader.py` | 5 KB | pdfplumber + fitz |
| `app/document_loader/docx_loader.py` | 4 KB | python-docx |
| `app/document_loader/txt_loader.py` | 4 KB | chardet |
| `app/document_loader/batch.py` | 11 KB | Batch orchestration |
| `app/document_loader/models.py` | 3 KB | Dataclasses |

## Public Interfaces
- `DocumentLoader` (abstract), `PDFLoader`, `DocxLoader`, `TxtLoader`.
- `load_document(path)`, `load_documents(paths)` (batch).

## Dependencies
pdfplumber, PyMuPDF (fitz), python-docx, chardet, tqdm, pydantic, orjson,
pathlib.

## Configuration Files
- `docs/DOCUMENT_LOADER_PERFORMANCE.md` — loader performance notes.
- `app/document_cleaner/config.py` — cleaner config (related pipeline).

## Known Issues
- Large PDFs can be memory-heavy; batch processing recommended.
- Encoding detection relies on chardet heuristics.

## Future Improvements
- Streaming PDF extraction for very large files.
- Parallel batch loading with process pool.

## Current TODOs
- Performance benchmarks (see docs/DOCUMENT_LOADER_PERFORMANCE.md).

# Legal Document Processing Pipeline — Technical Report

> **Audience:** Developers, reviewers, auditors, and maintainers working on the NSA Webservice legal document processing system.
> **Scope:** This report documents _how the system processes a corpus of legal documents_ — from ingestion (loading, OCR) through cleaning, extraction, analysis, versioning, to PDF export. It covers the data structures, algorithmic strategies, and integration points at each stage. It does **not** cover user management, authentication, deployment, or non-document-processing features unless directly relevant.
> **Last updated:** 2026-08-08

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Stage 1: Document Ingestion](#2-stage-1-document-ingestion)
3. [Stage 2: Document Cleaning](#3-stage-2-document-cleaning)
4. [Stage 3: OCR Pipeline (Image-Backed Documents)](#4-stage-3-ocr-pipeline-image-backed-documents)
5. [Stage 4: Metadata & Field Extraction](#5-stage-4-metadata--field-extraction)
6. [Stage 5: Legal Analysis & Paragraph Detection](#6-stage-5-legal-analysis--paragraph-detection)
7. [Stage 6: Cross-Reference Detection & Renumbering](#7-stage-6-cross-reference-detection--renumbering)
8. [Stage 7: Table of Contents Generation](#8-stage-7-table-of-contents-generation)
9. [Stage 8: Document Versioning](#9-stage-8-document-versioning)
10. [Stage 9: PDF Assembly](#10-stage-9-pdf-assembly)
11. [Stage 10: Markdown Export](#11-stage-10-markdown-export)
12. [Stage 11: AI Assistant Enhancement](#12-stage-11-ai-assistant-enhancement)
13. [Stage 12: Persistence Layer (Models)](#13-stage-12-persistence-layer-models)
14. [Stage 13: Search & Indexing](#14-stage-13-search--indexing)
15. [Document Flow Diagram](#15-document-flow-diagram)
16. [Configuration & Environment](#16-configuration--environment)
17. [Testing](#17-testing)
18. [Key Design Patterns](#18-key-design-patterns)

---

## 1. Architecture Overview

The legal document processing system is built as a pipeline of **specialized, composable stages**, each implemented as a Python module with a focused responsibility. The pipeline operates on two distinct document lifecycles:

1. **Static legal texts** (Acts, Notifications, Rules) — processed through the Legal Analysis workbench for paragraph detection, citation extraction, and metadata analysis.
2. **Case documents** (petitions, permission letters, adjudications) — authored in the Quill editor, saved with versioning, and rendered to PDF with post-processing (cross-references, TOC, bookmarks).

```
┌─────────────────────────────────────────────────────────────────┐
│                    DOCUMENT PROCESSING PIPELINE                   │
├─────────────────────────────────────────────────────────────────┤
│  Input: PDF / DOCX / TXT / Editor content (HTML/Delta)          │
│                                                                 │
│  [1] INGEST  →  [2] CLEAN  →  [3] OCR?  →  [4] EXTRACT          │
│                                                                 │
│  [5] ANALYZE (legal paragraph detection)                        │
│  [6] CROSS-REFERENCE (link, renumber)                           │
│  [7] TABLE OF CONTENTS                                          │
│  [8] VERSION (snapshot + diff)                                  │
│  [9] PDF ASSEMBLY (render + bookmarks + photos)                 │
│  [10] MARKDOWN EXPORT                                           │
│                                                                 │
│  Output: Cleaned text, structured metadata, versioned snapshots,│
│          PDF documents, Markdown                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Core Technologies

| Layer            | Technology                                 | Why                                                  |
| ---------------- | ------------------------------------------ | ---------------------------------------------------- |
| Web framework    | Flask 2.x                                  | Minimal, battle-tested, server-rendered              |
| ORM              | SQLAlchemy 2.x                             | Rich relationship support, Alembic migrations        |
| Database         | PostgreSQL (primary) / SQLite (dev/test)   | ACID compliance for legal records                    |
| Frontend editor  | Quill 2.x                                  | Rich text with Delta format for versioning           |
| Document loading | pdfplumber + PyMuPDF (fitz)                | Best text-layout preservation for legal PDFs         |
| OCR              | PaddleOCR (GPU) + Tesseract (CPU fallback) | Multilingual (English, Hindi, Bengali)               |
| Image processing | OpenCV + NumPy                             | Contour analysis for object detection                |
| PDF generation   | WeasyPrint                                 | CSS-based HTML→PDF with bookmarks                    |
| Search           | SQLite FTS5                                | Embedded full-text search without external service   |
| Task queue       | Celery + Redis                             | Async PDF generation, OCR, sync                      |
| Webhooks         | QStash                                     | Serverless async task triggering on Render free tier |

### Dependency Stack (Document Processing)

From `pyproject.toml`:

```toml
dependencies = [
    "flask", "flask-sqlalchemy", "flask-migrate", "flask-login",
    "flask-wtf", "flask-talisman", "sqlalchemy", "alembic",
    "weasyprint", "pdfplumber", "pymupdf", "pytesseract",
    "pillow", "reportlab", "rapidfuzz", "numpy", "opencv-python-headless",
    "httpx", "redis", "celery",
    "gspread", "pyairtable", "msal", "requests",
    "docx2txt", "markdown", "jinja2",
    "pydantic",
    "paddleocr",  # optional — installed in GPU environments
]
```

---

## 2. Stage 1: Document Ingestion

### Purpose

Load legal documents from various file formats (PDF, DOCX, TXT) into a unified in-memory representation (`DocumentResult`) that downstream stages can process.

### Module

`app/document_loader/`

### Architecture

The loader uses a **factory pattern** with a pluggable extension-to-loader mapping:

```python
# app/document_loader/loader.py
EXTENSION_MAP = {
    ".pdf": PDFLoader,
    ".docx": DOCXLoader,
    ".txt": TXTLoader,
}
```

New file type support is added by creating a new `BaseLoader` subclass and registering it in `EXTENSION_MAP`.

### Data Model

All loaders return a `DocumentResult` (Pydantic model):

```
DocumentResult
├── document_id    : UUID4 hex string (unique per document)
├── file_name      : str (original filename)
├── file_type      : str (normalized: "pdf", "docx", "txt")
├── pages          : list[PageResult]
│   └── PageResult
│       ├── page    : int (1-based)
│       └── text    : str (extracted text)
└── metadata       : FileMetadata
    ├── file_size_bytes : int
    ├── created_at      : datetime | None
    ├── modified_at     : datetime | None
    ├── encoding        : str | None (text files)
    └── page_count      : int | None
```

### PDF Loading Strategy

`PDFLoader` uses a **two-tier fallback** approach:

1. **pdfplumber** (primary): Best text-layout preservation. Extracts per-page text via `page.extract_text()`.
2. **PyMuPDF (fitz)** (fallback): Handles encrypted, damaged, or structurally complex PDFs that pdfplumber rejects.
3. **Error page**: If both fail, returns a single-page result with an error message.

Both engines produce `PageResult` objects. The `_clean_text()` helper in `BaseLoader` performs basic whitespace normalization (NFKC normalization, non-breaking space replacement, blank-line collapsing).

### DOCX Loading

`DOCXLoader` wraps `docx2txt` to extract text, then splits on `\n\n` to approximate page boundaries (DOCX has no native page concept — page count is inferred from paragraph count heuristics).

### TXT Loading

`TXTLoader` reads the file with encoding detection (chardet if available, UTF-8 fallback), splits on `\f` (form feed) for page boundaries.

### Batch Processing

`app/document_loader/batch.py` provides a `DocumentBatchProcessor` that loads multiple documents and aggregates results. Used by the OCR pipeline's multi-page handler.

### Entry Points

- `GET /legal/` — Legal analysis workbench page (uploads paste text; the loader is used for file-based documents via the OCR pipeline)
- OCR pipeline: `process_ocr_document_async` task calls `split_pdf_bundle()` first, then `process_document_ocr()` per page

---

## 3. Stage 2: Document Cleaning

### Purpose

Clean raw legal document text (especially OCR-extracted text) by removing noise, normalizing formatting, and preserving legal structural elements (section numbers, citations, tables).

### Module

`app/document_cleaner/`

### Architecture

The cleaner is implemented as a **config-driven pipeline** of removal and normalization operations:

```
DocumentCleaner.clean(raw_text)
  ├── Phase 1: Line-level removal operations
  │   ├── remove_blank_pages()
  │   ├── remove_headers_footers()  [frequency analysis]
  │   ├── remove_running_titles()   [uppercase repeat detection]
  │   ├── remove_page_numbers()
  │   ├── remove_watermark_text()
  │   └── remove_duplicate_lines()
  ├── Phase 2: Full-text OCR artifact removal
  │   └── remove_ocr_artifacts()    [non-printable/non-allowed chars]
  ├── Phase 3: Normalization
  │   ├── normalize_unicode()       [NFKC]
  │   ├── normalize_encoding()      [NBSP, zero-width, control chars]
  │   ├── normalize_bullets()       [various unicode bullets → *]
  │   ├── normalize_quotes()        [curly → straight]
  │   ├── normalize_tabs()          [tab → space]
  │   ├── normalize_hyphens()       [join split words, rapidfuzz-validated]
  │   ├── normalize_spaces()        [collapse multi-spaces]
  │   ├── normalize_trailing_whitespace()
  │   └── normalize_linebreaks()    [3+ newlines → 2]
  └── Phase 4: Final line-break collapse
```

### Configuration Presets

Three presets are available via `CleaningConfig`:

| Preset         | Description                             | Key Differences                                                                               |
| -------------- | --------------------------------------- | --------------------------------------------------------------------------------------------- |
| `aggressive`   | Default; strip everything non-essential | All operations enabled                                                                        |
| `conservative` | Preserve formatting                     | Disables duplicate-line removal, hyphen joining, quote/bullet normalization; keeps linebreaks |
| `ocr`          | Tuned for OCR-extracted text            | Identical to aggressive (OCR output is the dirtiest)                                          |

### Removal Operations

#### `remove_page_numbers(lines)`

- Removes lines matching: `Page 5`, `- 3 -`, `3 of 10`, `3/10`
- Regex: `^\s*(?:Page\s+\d+|-\s*\d+\s*-|\d+\s*of\s*\d+|\d+\s*/\s*\d+)\s*$`

#### `remove_watermark_text(lines)`

- Removes lines matching: `CONFIDENTIAL`, `DRAFT`, `DO NOT COPY`, `PRIVILEGED`, `ATTORNEY WORK PRODUCT`, `INTERNAL USE ONLY`, `GENERATED ON 2024-01-15`, etc.
- Case-insensitive regex match on the full line.

#### `remove_blank_pages(lines)`

- Removes lines that are empty or whitespace-only.

#### `remove_duplicate_lines(lines)`

- Removes consecutive duplicate lines (after stripping).

#### `remove_headers_footers(lines)`

- **Frequency analysis**: Divides lines into position "buckets" (≈40 lines per page). A line appearing ≥ `max(3, len(lines)//(bucket_size*4))` times in the same bucket position is flagged as a header/footer and removed from all positions.
- Minimum page count: 20 lines (too few for reliable detection).

#### `remove_running_titles(lines)`

- Finds lines matching `^[A-Z][A-Z\s&,.]{3,60}$` that appear more than once (running titles are uppercase, short, repeated per page).

#### `remove_ocr_artifacts(text)`

- Operates on the full rejoined text (not line-by-line).
- Removes any character NOT in the allowed set: ASCII printable + Devanagari/Bengali/Gurmukhi/Oriya/Tamil/Telugu/Malayalam Unicode ranges + common Unicode punctuation (en-dash, em-dash, smart quotes, primes, Spanish punctuation).
- This is the most aggressive cleaning step — it strips all "garbage" OCR characters.

### Preservation Patterns

The cleaner includes a `_should_preserve(line)` guard that protects legal structural elements from being removed:

| Pattern                                           | What it protects                                 |
| ------------------------------------------------- | ------------------------------------------------ |
| `Section\s+\d+[A-Za-z]?`                          | Section headers: "Section 55", "Section 3(1)(a)" |
| `^\d+\.\s`                                        | Numbered list items: "1. First ground..."        |
| `^\(\w\)\s`                                       | Sub-clause markers: "(a) sub-clause text"        |
| `^Clause\s+\d+`                                   | Clause markers                                   |
| `Schedule\s+[IVXLCDM\d]`                          | Schedules                                        |
| `^\s*\|.*\|\s*$`                                  | Markdown tables                                  |
| `^\s*[+                                           | =\-]{5,}\s*$`                                    | Table separators |
| Tabular: `^\s*\S{1,15}(?:\s{2,}\S{1,15}){3,}\s*$` | 4+ column tabular data                           |
| `(2020)\s+\d+\s+SCC\s+\d+`                        | Legal citations: "(2020) 12 SCC 345"             |
| `AIR\s+\d{4}\s+...`                               | AIR citations                                    |
| `(JT\|SCR\|CrLJ\|PLJR)\s+\(?\d+\)?`               | Journal citations                                |
| `(See\|Refer\|Vide\|Cf\.)`                        | Cross-references                                 |
| `(as referred to\|hereinafter\|aforesaid)`        | Legal connective phrases                         |

### Hyphen Joining (`normalize_hyphens`)

Uses `rapidfuzz` for fuzzy word validation:

1. Matches `(\w{2,})-\s*\n\s*(\w{2,})` — hyphenated words split by newline.
2. Joins the two parts.
3. If the joined word has ≥85% fuzzy similarity to any word in a curated legal-word list (`_COMMON_WORDS` — 80+ terms like "notwithstanding", "hereinafter", "adjudication", etc.), the join is accepted.
4. Otherwise, if the joined word is >3 chars and contains vowels, the join is still accepted (likely a real compound word).
5. If neither, the original hyphenated form is preserved.

### Reporting

Each `DocumentCleaner.clean()` call returns a `CleanedDocument`:

```
CleanedDocument
├── clean_text     : str (the cleaned text)
└── report         : CleaningReport
    ├── original_length       : int
    ├── clean_length          : int
    ├── total_chars_removed   : int
    ├── total_items_removed   : int
    └── removed_items         : list[RemovedItem]
        ├── category    : str (page_number / watermark_text / blank_page / etc.)
        ├── snippet     : str (first 120 chars of removed text)
        ├── count       : int
        └── chars_saved   : int
```

The `DocumentDiffer` (`app/document_cleaner/differ.py`) provides:

- `diff(original, cleaned)` — `difflib.SequenceMatcher` line-level diff with opcodes
- `summary_text(diff_result)` — human-readable summary
- `unified_diff(original, cleaned, n=3)` — unified diff format

---

## 4. Stage 3: OCR Pipeline (Image-Backed Documents)

### Purpose

Process PDF pages that lack selectable text (scanned documents, photos of documents) through OCR to extract text. Includes intelligent decision-making to skip OCR when text is already selectable.

### Module

`app/ocr_pipeline/`

### Architecture

```
OCRPipeline.process_page(pdf_path, page_number)
  ├── Step 1: OCRDecisionEngine.evaluate()
  │   ├── Uses PyMuPDF to check for selectable text
  │   ├── Decision: char_count >= 20 AND has_text_blocks → skip OCR
  │   └── If skip → return direct text as OCRResult
  ├── Step 2: ImagePreprocessor.pdf_page_to_image()
  │   └── Renders page to high-DPI (300) RGB NumPy array
  ├── Step 3: ImagePreprocessor.process()
  │   ├── Grayscale conversion
  │   ├── Denoising (Non-local Means)
  │   ├── Deskew (correct rotation via minAreaRect)
  │   ├── Adaptive thresholding
  │   ├── Contrast enhancement (CLAHE)
  │   ├── Orientation correction (pytesseract OSD)
  │   └── Resolution enhancement (up to 4000px max)
  ├── Step 4: PageDetector.detect_all()
  │   ├── Table detection (horizontal/vertical line morphology)
  │   ├── Stamp detection (color segmentation + HoughCircles)
  │   ├── Signature detection (stroke-width filtering)
  │   └── Watermark detection (brightness + edge analysis)
  ├── Step 5: OCREngine.recognize()
  │   ├── PaddleOCR (GPU, multilingual) — primary
  │   └── Tesseract (CPU, pytesseract) — fallback
  └── Return: OCRResult (text, confidence, engine, language, detection, preprocessing_steps)
```

### OCRDecisionEngine

`app/ocr_pipeline/decision.py`

- Uses PyMuPDF's `page.get_text("dict")` to count extractable characters.
- **Decision criteria**: `char_count >= 20` AND `has_text_blocks` (any block type 0) → text is selectable, skip OCR.
- **Fallback**: `char_count >= 100` (20 × 5) even without reliable block detection.
- If neither threshold is met, the page is flagged for full OCR processing.

### ImagePreprocessor

`app/ocr_pipeline/preprocessing.py`

| Step        | Method                                               | OpenCV Operations                  |
| ----------- | ---------------------------------------------------- | ---------------------------------- |
| Grayscale   | `cv2.cvtColor(COLOR_RGB2GRAY)`                       | Color space conversion             |
| Denoise     | `cv2.fastNlMeansDenoising[Colored]`                  | Noise reduction                    |
| Deskew      | `cv2.minAreaRect` + `cv2.warpAffine`                 | Rotation correction via text angle |
| Threshold   | `cv2.adaptiveThreshold(ADAPTIVE_THRESH_GAUSSIAN_C)`  | Binarization for OCR               |
| Contrast    | `cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))` | Local contrast enhancement         |
| Orientation | `pytesseract.image_to_osd` + rotation                | Auto-rotation via OSD              |
| Resolution  | `cv2.resize(INTER_CUBIC)`                            | Upscale to 4000px max              |

Each step is wrapped in try/except — a failure in one step doesn't block others. Only applied steps are recorded in `preprocessing_steps`.

### PageDetector

`app/ocr_pipeline/detectors.py`

Uses OpenCV contour analysis with multiple strategies per object type:

| Object        | Detection Strategy                                                                                                                       |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **Table**     | Morphological line detection (horizontal + vertical kernels), contour finding on grid, filter by area > 5000px² and exclude full-page    |
| **Stamp**     | Color segmentation (red HSV range 0-10/170-180, blue 100-130) + HoughCircles, aspect ratio 0.7-1.4 (roughly circular)                    |
| **Signature** | Morphological opening (large kernel to remove text blocks), dilate remaining strokes, filter by aspect 1.5-8.0 and height < 15% of page  |
| **Watermark** | Gaussian blur + threshold at 200 (captures near-white text), Canny edge detection, brightness check (180-240 range for faint watermarks) |

Returns `PageDetectionResult` with all detected objects, boolean flags, and counts.

### OCREngine

`app/ocr_pipeline/ocr_engine.py`

**Primary: PaddleOCR**

- Supports English, Hindi, Bengali
- `use_angle_cls=True` for text orientation
- Returns `(text, confidence, engine_name, language)` tuple
- Confidence: average of per-line confidences, filtered by `_MIN_CONFIDENCE = 0.3`

**Fallback: Tesseract**

- Uses `--psm 6` (assumed uniform block) and `--oem 3` (default LSTM)
- Language combined via `+` (e.g., `"eng+hin"`)
- Confidence from `pytesseract.Output.DICT` normalized to 0-1

**Language auto-detection**: After OCR, `_detect_language()` counts Devanagari (0x0900-0x097F) vs Bengali (0x0980-0x09FF) characters in the output text.

### OCRResult Model

```
OCRResult
├── page             : int (1-based)
├── ocr_used         : bool (True = OCR ran, False = direct extraction)
├── confidence       : float (0.0-1.0)
├── language         : str ("english", "hindi", "bengali")
├── text             : str (extracted text)
├── ocr_engine       : str | None ("paddle", "tesseract", "none")
├── preprocessing_steps : list[str] (which steps were applied)
├── detection        : PageDetectionResult (tables, stamps, signatures, watermarks)
└── error            : str | None
```

### Batch Processing

`app/ocr_pipeline/batch.py` — orchestrates processing of multi-page documents. Used by the Celery task `process_ocr_document_async` which:

1. Splits multi-page PDFs via `split_pdf_bundle()`
2. Runs `process_document_ocr()` per page
3. Aggregates fields and lab parameters
4. Persists `OCRDocument` + `LabTestParameter` rows

### Service Layer

`app/services/ocr_extraction.py` — bridges the OCR pipeline to the metadata extraction:

- Runs `OCRPipeline.process_document()` to get per-page text
- Feeds text to `LegalMetadataEngine.extract()` for regex-based field extraction
- Extracts lab-test parameters via `_extract_lab_test_parameters()` (regex pattern: `([A-Za-z][A-Za-z\s]{2,40}?)\s*:?\s*(\d+\.?\d*)\s*([A-Za-z/]{1,10})?`)
- Returns a dict with `fields`, `confidence_scores`, `extracted_text`, `lab_test_parameters`

---

## 5. Stage 4: Metadata & Field Extraction

### Purpose

Extract structured metadata and field values from legal document text using a hybrid regex + NER approach with confidence scoring.

### Module

`app/metadata_extractor/`

### Architecture

```
LegalMetadataEngine.extract(text)
  ├── Step 1: Regex-based extraction (13 field-specific extractors)
  ├── Step 2: NER-based extraction (spaCy — opt-in, use_ner=False by default)
  ├── Step 3: Merge candidates per field (regex + NER)
  │   └── Pick best candidate by confidence score
  ├── Step 4: Cross-field validation (Validator)
  └── Step 5: Build LegalMetadata result
```

### Extractors

Each field has a dedicated extractor class in `app/metadata_extractor/extractors/`:

| Field                 | Extractor                | Strategy                                                                          |
| --------------------- | ------------------------ | --------------------------------------------------------------------------------- |
| `title`               | `TitleExtractor`         | Regex for "THE <ACT NAME> ACT, 2006" or "NOTIFICATION NO. ..."                    |
| `version`             | `VersionExtractor`       | Regex for "Act No. X of 2020" or "Amendment Act, 2021"                            |
| `date`                | `DateExtractor`          | Multiple date regex patterns (DD/MM/YYYY, YYYY-MM-DD, "ddth month year")          |
| `authority`           | `AuthorityExtractor`     | Regex for issuing authority names (e.g., "Ministry of Health", "FSSAI")           |
| `gazette_number`      | `GazetteExtractor`       | Regex for "Gazette Notification No. XYZ"                                          |
| `notification_number` | `NotificationExtractor`  | Regex for "Notification No. FSSAI/..."                                            |
| `language`            | `LanguageExtractor`      | Script-based detection (Devanagari → Hindi, Bengali → Bengali, default → English) |
| `jurisdiction`        | `JurisdictionExtractor`  | Keyword match ("India", "Union of India", "Central Government")                   |
| `state`               | `StateExtractor`         | Extract from "State of <name>" or "<state> Government"                            |
| `country`             | `CountryExtractor`       | Default "India" unless other country detected                                     |
| `document_type`       | `DocumentTypeExtractor`  | Regex for "Act", "Rule", "Notification", "Regulation", "Order"                    |
| `amendment_status`    | `AmendmentExtractor`     | Detect "Amended", "Repealed", "Substituted" keywords                              |
| `effective_date`      | `EffectiveDateExtractor` | "comes into effect" / "shall take effect" date patterns                           |

### NER Extraction (Optional)

`app/metadata_extractor/ner.py` — When `use_ner=True`, loads spaCy's `en_core_web_sm` model. Maps spaCy entity types to metadata fields:

| spaCy Label | Mapped Field   |
| ----------- | -------------- |
| `LAW`       | `title`        |
| `ORG`       | `authority`    |
| `GPE`       | `jurisdiction` |
| `DATE`      | `date`         |
| `LOC`       | `state`        |

NER candidates are added to the candidate pool but **regex results are preferred** (higher precision). The best candidate per field is selected by `score_field()` in `app/metadata_extractor/confidence.py`.

### Confidence Scoring

`app/metadata_extractor/confidence.py` — `score_field()` assigns confidence based on:

- **Extraction method**: regex (0.9) > hybrid (0.75) > ner (0.6) > heuristic (0.5) > default (0.0)
- **Candidate count**: more matches = higher confidence (diminishing returns)
- **Text length**: longer matches = higher confidence
- **Field-specific validation**: e.g., dates must parse; gazette numbers must match expected format

### Validation

`app/metadata_extractor/validation.py` — `Validator.validate_all()` performs cross-field consistency checks:

- Date consistency (effective_date must be after notification date)
- Authority-jurisdiction consistency (state mentioned must match authority scope)
- Section reference validity

### Field Authority for Conflict Resolution

`app/metadata_extractor/models.py` includes `FieldAuthority` model for the OCR conflict-resolution workflow (Phase B). Sources ranked by weight:

- `vision_llm` (weight: 3.0) — highest trust (Vision-LLM zonal extraction)
- `zonal_ocr` (weight: 2.0) — medium trust (per-zone OCR)
- `manual` (weight: 5.0) — highest trust (human-corrected)

When multiple sources produce different values for the same field, a `ConflictLog` row is created for reviewer resolution.

### Extracted Field Types (from `process_document_ocr`)

Two categories of fields are extracted from lab reports:

1. **Legal metadata fields** (from `LegalMetadataEngine`): title, version, date, authority, gazette_number, notification_number, language, jurisdiction, state, country, document_type, amendment_status, effective_date

2. **Lab-test parameters** (from `_extract_lab_test_parameters()`): free-form `parameter_name` / `observed_value` / `unit` triples. Examples:
    - "Vitamin A: 120 IU/ml" → `{name: "Vitamin A", value: "120", unit: "IU/ml"}`
    - "Lead 0.3 mg/kg" → `{name: "Lead", value: "0.3", unit: "mg/kg"}`

These map to the `lab_test_parameter` model columns: `parameter_name`, `observed_value`, `unit`, `source_authority`, `confidence`.

### Autopopulation Fields

The OCR service defines two target field sets for autopopulation of case records:

- **SAMPLE_AUTOFIELDS**: `nature_of_food`, `batch_no`, `mfd`, `exp`, `manufacturer_details`
- **LEGAL_AUTOFIELDS**: `case_number`, `sample_code`, `lab_registration_no`, `analyst_report_no`, `batch_no`, `mfg_date`, `expiry_date`

---

## 6. Stage 5: Legal Analysis & Paragraph Detection

### Purpose

Analyze pasted or loaded legal document text to produce a structured breakdown of sections, clauses, sub-clauses, citations, and confidence scores.

### Module

`app/legal_analysis/` + standalone `legal_paragraph_detection_engine/`

### Architecture

```
POST /legal/analyze
  ├── Request body: {"text": "raw legal text"}
  ├── Calls: analyze_legal_text(text) in app/services/legal_engine.py
  │   ├── Lazy-imports LegalParagraphEngine from legal_paragraph_detection_engine
  │   ├── engine.process_document(text) → list[dict] paragraphs
  │   ├── _summarize(paragraphs) → aggregate stats
  │   └── Returns: {"summary": {...}, "paragraphs": [...]}
  └── Returns: JSON response
```

### Engine

The `LegalParagraphEngine` (from the standalone `legal_paragraph_detection_engine/` package, T-46 integration):

1. **Tokenizes** the input into candidate segments using rule-based boundary detection (numbered sections, "Provided that", "Explanation", "Proviso", etc.)
2. **Classifies** each segment into `paragraph_type`: `section`, `subsection`, `subsubsection`, `clause`, `subclause`, `explanation`, `note`, `proviso`, `schedule`, `table`, `paragraph`, `header`, `footer`, `title`
3. **Assigns hierarchy** via nesting depth (hierarchy_depth 1-5, mapped to indent levels in the UI)
4. **Extracts section numbers** (e.g., "3(1)(a)") via regex: `\b\d{1,3}(?:\(\d+(?:\([a-z]\))?)+\)`
5. **Extracts clause numbers** (e.g., "(aa)", "(b)") via nested-parenthesis regex
6. **Extracts citations** using a curated set of patterns:
    - Section refs: `Section\s+\d{1,3}(?:\(\d+(?:\([a-z]\))?)+\)`
    - Case citations: `\(\d{4}\)\s+\d+\s+SCC\s+\d+` (Supreme Court Cases)
    - AIR: `AIR\s+\d{4}\s+(?:SC|\w+)\s+\d+`
    - Journals: `(?:JT|SCR|CrLJ|PLJR)\s+\(?\d+\)?`
7. **Scores confidence** per paragraph:
    - Structural confidence: how well the text matches expected structure for its type
    - Format confidence: presence of expected formatting (numbered headings, citations)
    - Word count confidence: longer paragraphs with more signal = higher confidence
    - Overall = weighted average (default threshold: 0.6)
8. **Returns** `meets_confidence_threshold` boolean per paragraph

### Summary Metrics

```
summary
├── total_paragraphs       : int
├── paragraph_types        : dict[str, int] (e.g., {"section": 5, "subsection": 3})
├── document_types         : list[str] (e.g., ["Act", "Notification"])
├── sections               : list[str] (e.g., ["3", "5", "26"])
├── total_citations        : int
├── meets_threshold        : int (paragraphs passing confidence threshold)
├── avg_confidence         : float (0.0-1.0)
```

### Paragraph Structure

Each paragraph in the result contains:

```
paragraph
├── text                  : str (the full paragraph text)
├── paragraph_type        : str (section/subsection/clause/etc.)
├── section               : str | None (e.g., "3(1)(a)")
├── clause                : str | None (e.g., "(aa)")
├── hierarchy_depth       : int (1-5)
├── confidence_scores     : {overall, structural, format, word_count}
├── meets_confidence_threshold : bool
├── citations             : list[{type, reference, confidence}]
├── word_count            : int
└── document_type         : str (e.g., "Act", "Notification")
```

### UI

`app/legal_analysis/templates/legal_analysis/index.html`:

- Textarea for pasting legal text
- "Load sample" button (FSS Act Section 3 excerpt)
- Analysis results rendered as collapsible cards with confidence bars
- Section tags with color coding
- Citation badges

---

## 7. Stage 6: Cross-Reference Detection & Renumbering

### Purpose

Detect and link cross-references within legal documents (section numbers, annexure references, paragraph numbers, citations) and renumber ordered lists after paragraph insertions/deletions.

### Module

`app/cross_reference/engine.py`

### Architecture

The `CrossReferenceEngine` performs three operations:

1. **Extraction** (pure, no DB): Scans text for patterns, returns `CrossReference` objects
2. **Linking** (DB-backed): Resolves annexure references against stored `Annexure` records
3. **Renumbering** (pure + DB): Rewrites `<ol start="N">` attributes and annexure letters

### Reference Types

| Type        | Pattern                                                            | Target Format                |
| ----------- | ------------------------------------------------------------------ | ---------------------------- |
| `paragraph` | `paragraph \d+`, `para \d+`, `clause \d+`, line-start `1.` / `(1)` | Numbered                     |
| `annexure`  | `Annexure A`, `Annexure-A`, `Annexure 1`, `Annexure No. B`         | Letter (A-Z) or index (1-26) |
| `section`   | `Section \d{1,3}`, `Sec. \d+`, `u/s \d+`, `Sections 55, 56 and 58` | 1-3 digit number             |

### Extraction Regex Patterns

```python
# Annexure: "Annexure A" through "Annexure No. B"
_ANNEXURE_RE = r"\bAnnexure(?!s)\b\s*[-:\u2013\u2014.]?\s*(?:No\.?\s*)?\(?\s*([A-Za-z]|\d{1,2})\s*\)?"

# Section runs: "Sections 55, 56 and 58" → splits into individual refs
_SECTION_RUN_RE = r"\b(?:Section|Sec\.?|Sections|u/s)\s+(\d{1,3}(?:\s*[,&and-]+\s*\d{1,3})*)"

# Sub-clause: "Section 26(2)(ii)" → one ref with full sub-clause path
_SECTION_SUBCLAUSE_RE = r"\b(?:Section|Sec\.?|s\.?|u/s)\s*(\d{1,3}(?:\(\d+\)|\([a-z]+\)){1,3})"

# Paragraph words: "paragraph 3", "para 5", "clause 2"
_PARA_WORD_RE = r"\b(?:paragraph|para\.?|clause)\s+(\d{1,3})"

# List markers at line start: "1. ..." or "(1) ..."
_LIST_MARKER_RE = r"(?m)^(?P<lead>\s*)(?:\((?P<paren>\d+)\)|(?P<dot>\d+)\.|(?P<letter>[A-Za-z])\.)(?P<sep>\s+)(?=\S)"
```

### Annexure Linking

When `link_references(text, case_id, adjudication_id)` is called:

1. Loads all `Annexure` records for the case (ordered by upload time)
2. For annexure refs with alphabetic targets (A-Z): matches against `Annexure.annexure_letter`
3. For annexure refs with numeric targets (1-26): matches against the Nth annexure in order
4. Returns resolved metadata: `{annexure_id, annexure_letter, caption, filename, page_count}`

### Renumbering

#### `renumber_html_lists(html)` — Phase 6 HTML Post-Processing

Operates on `<ol>` elements in rendered HTML:

- Only renumbers lists with `class="justify"` (the pattern used by petition/permission templates)
- Detects continuation: if the first list starts at 1, a sequence is established
- Subsequent justify lists get `start = prev_last + 1`
- Nested `<ol>` within justify lists are preserved (not renumbered)
- Only runs when the first list in the document starts at 1 (avoids clobbering pre-numbered content)

#### `renumber_annexures(case_id, adjudication_id)` — DB-backed

Reassigns A/B/C... letters to annexures in upload order via `_SECTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"`. Writes updates to the database and returns the list of changes.

### Enclosure List Generation

`build_enclosures_html()` generates the auto "List of Enclosures" `<ol>`:

```
<ol class="justify">
<li>Copy of <caption> — Annexure A, 3 pages</li>
<li>Copy of <caption> — Annexure B, 1 page</li>
</ol>
```

This fills `<ol data-cross-reference="enclosures"></ol>` placeholders in the petition/permission templates.

### Annotation (PDF-Assembly Entry Point)

`annotate_html(html, case_id, adjudication_id)` chains:

1. `renumber_html_lists(html)` — fix continuation numbering
2. Fill enclosures placeholder if present
3. Returns final HTML for PDF conversion

---

## 8. Stage 7: Table of Contents Generation

### Purpose

Generate a dynamic Table of Contents from document HTML headings, with hierarchical numbering (1, 1.1, 1.2, 2, ...) and annexure detection.

### Module

`app/toc_generator/engine.py`

### Architecture

```
TocGeneratorEngine.annotate_html(html)
  ├── extract_toc(html)
  │   └── _HeadingExtractor(HTMLParser)
  │       └── Finds all <h1>-<h6> tags in document order
  ├── annotate_headings(html, entries)
  │   └── Adds id="toc-N" attributes to heading tags
  ├── build_toc_html(entries)
  │   └── Nested <ol> with numbered entries + annexure badges
  └── Replace <div data-toc></div> with <nav class="toc-nav">...</nav>
```

### Heading Extraction

Uses Python's stdlib `HTMLParser` (no third-party dependency):

- Subclass `_HeadingExtractor` tracks start/end tags
- Extracts text content from `handle_data()` callbacks
- Only non-empty headings become entries (ensures anchor IDs are sequential)
- Each entry gets a `heading_id` like `toc-1`, `toc-2`, etc.

### Hierarchical Numbering

Numbering algorithm:

1. Maintain a `counters` list (stack) that grows/shrinks with heading depth
2. `while len(counters) > entry.level: counters.pop()` — unwind
3. `while len(counters) < entry.level: counters.append(0)` — grow
4. `counters[-1] += 1` — increment
5. Number = `".  ".join(str(c) for c in counters)` → e.g., "1.2.3"

### Annexure Detection

```python
_ANNEXURE_MARKER_RE = re.compile(
    r"^(annexure|appendix|enclosure|attachment)(?![a-z]"
    r"(?:\s*[-\u2013\u2014:.]?\s*(?:[a-z]{1,2}|[0-9]+|\[?[ivxlcdm]+\]?))?$",
    re.IGNORECASE,
)
```

Flags headings as `is_annexure` when they match "Annexure A", "Appendix I", etc. The `(?![a-z])` guard prevents matching "Annexures" (plural).

### TOC HTML Output

```html
<nav class="toc-nav" role="navigation" aria-label="Table of Contents">
    <ol class="toc-list">
        <li class="toc-item level-1">
            <a href="#toc-1"><span class="toc-number">1</span> Relevancy of Facts</a>
            <ol class="toc-sub">
                <li class="toc-item level-2">
                    <a href="#toc-2"><span class="toc-number">1.1</span> Place of Business</a>
                </li>
            </ol>
        </li>
        <li class="toc-item level-1 toc-annexure">
            <a href="#toc-5"
                ><span class="toc-number">2</span>
                <span class="toc-annexure-badge">Annexure</span> Lab Report</a
            >
        </li>
    </ol>
</nav>
```

The closing logic uses a stack to track open `<li>` elements and only emits `</ol>` when a sub-list was actually opened.

### CSS for PDF Bookmarks

In `app/pdf_assembly/engine.py`, the bookmark CSS is injected:

```css
h1 {
    bookmark-level: 1;
}
h2 {
    bookmark-level: 2;
}
h3 {
    bookmark-level: 3;
}
... h6 {
    bookmark-level: 6;
}
```

This enables PDF bookmarks/outline navigation in the generated PDF.

### JSON Report

`generate_toc_data(html)` returns a JSON-safe dict for the UI:

```json
{
  "entries": [{"level": 1, "text": "...", "heading_id": "toc-1", "number": "1", ...}],
  "total_headings": 15,
  "total_annexures": 2,
  "max_depth": 4,
  "has_toc_placeholder": true
}
```

---

## 9. Stage 8: Document Versioning

### Purpose

Snapshot document content on save, enable version comparison (diff), restore to previous versions, and support branching/draft mode.

### Module

`app/services/version_control.py` + `app/version_control/`

### Data Model

`app/models/document.py` — `Version` table:

```
Version
├── id               : int (PK, autoincrement)
├── case_id          : int → case_files.id (nullable)
├── adjudication_id  : int → adjudications.id (nullable)
├── doc_type         : str ("petition" | "permission")
├── version_number   : int (1-based, per case+doc_type+branch)
├── content_hash     : str (SHA-256 of HTML content — for dedup)
├── html_snapshot    : str (full HTML snapshot)
├── delta            : str | None (Quill Delta JSON)
├── created_at       : datetime (UTC)
├── user_id          : int → user.id
├── change_summary   : str | None
├── branch_name      : str | None (None = mainline)
└── branch_of        : int → versions.id (source version for branch)
```

**Conditional unique indexes**:

- Mainline: `(case_id|adjudication_id, doc_type, version_number)` WHERE `branch_name IS NULL`
- Branches: same triple + `branch_name` unique WHERE `branch_name IS NOT NULL`

### VersionService

`app/services/version_control.py` — `VersionService` class:

#### `create_version()`

- Computes SHA-256 `content_hash`
- Gets next `version_number` via `max(version_number) + 1` per scope
- Stores HTML + optional Delta JSON
- Records `user_id`, `change_summary`, `branch_name`, `branch_of`

#### `create_version_if_changed()` (auto-save dedup)

- Compares new content's hash against the latest stored snapshot
- Only creates a new `Version` row if the hash differs
- Prevents the versions table from exploding from keystroke-level autosaves

#### `compare_versions()`

- Uses `difflib.SequenceMatcher` on word-split HTML
- Produces: `content_changed` (bool), `insertions` (list[str]), `deletions` (list[str]), `word_count_diff` (int), `similarity` (0-1), `unified` (text)
- Also extracts a tag-stripped line-level unified diff for human review

#### `restore_version()`

- Append-only: creates a **new** version record with the restored content
- Tags `change_summary` as "Restored to version N"
- Persists the snapshot to `instance/saved/` so the editor's session-restore picks it up
- If disk write fails, the appended version is rolled back (transactional consistency)

#### `create_branch()`

- Creates a root version with `branch_name` set and `version_number = 1`
- Isolated from mainline by the conditional unique index (branch_name IS NOT NULL scope)
- The source version's content is copied as the branch root

#### `get_branches()`

- Queries `Version` where `branch_name IS NOT NULL`
- Returns the first (root) version of each branch (ordered by `created_at`)

### DocumentSaveCoordinator

`app/services/document_lifecycle.py` — encapsulates the three side-effects of an editor save:

1. **Content persistence**: Writes HTML + Delta to `instance/saved/` via `save_saved_document()` (shared module in `app/utils/document_storage.py`)
2. **Version snapshot**: `VersionService.create_version()` (explicit saves) or `create_version_if_changed()` (auto-saves)
3. **Audit logging**: `log_audit()` — best-effort, never fails the save

The coordinator handles CaseFile vs Adjudication disambiguation via `CaseResolver`:

```python
if case_type == "case_file":
    case_id_arg, adjudication_id_arg = case_id, None
else:
    case_id_arg, adjudication_id_arg = None, case_id
```

### Storage Convention

Files in `instance/saved/` named: `<label>_<doc_type>_<timestamp>.html` + matching `.delta` file

- `label`: case_id or adjudication_id
- `doc_type`: "petition" or "permission"
- `timestamp`: `YYYYMMDD_HHMMSS`

The `document_viewer.get_saved_document` endpoint reads the most recent file matching `<case_id>_<doc_type>_*.html` for session restore.

### Version Control UI

`app/version_control/templates/version_control/history.html`:

- Lists all versions grouped by doc_type
- Shows version number, created_at, created_by username, change_summary
- Diff viewer (side-by-side or unified)
- Restore button (creates new version pointing back to restored content)
- Branch creation form
- Branch history view

### Diff Algorithm

`_diff_html(html_a, html_b)`:

- Uses `difflib.SequenceMatcher` at the **word level** (split on whitespace)
- `insertions`: words in B but not A (in their original order)
- `deletions`: words in A but not B
- `similarity`: `matcher.ratio()` — accounts for both insertions and deletions
- Also produces a tag-stripped line-level unified diff (`difflib.unified_diff`) for human-readable comparison

---

## 10. Stage 9: PDF Assembly

### Purpose

Convert HTML documents to PDF with bookmarks, hyperlinks, photo embedding, and cross-reference/TOC post-processing.

### Module

`app/pdf_assembly/engine.py`

### Architecture

```
PDFAssemblyEngine
├── generate_from_html(html) → (pdf_bytes|None, error|None)
├── post_process(html, **kw) → str
├── embed_photos(urls) → list[dict]
├── renumber_html_lists(html) → str
├── annotate_html(html, **kw) → str
└── generate_from_html(html) → (bytes, error)
```

### PDFAssemblyEngine Class

The engine consolidates all PDF operations that were previously scattered across `app/utils/pdf_utils.py`. All existing callers delegate to a shared singleton.

#### `generate_from_html(html)`

1. Injects bookmark CSS (h1-h6 → `bookmark-level: N`)
2. Injects hyperlink CSS (`a[href] { color: #1e40af; text-decoration: underline }`)
3. Converts internal anchor links (`href="#section"`) to PDF named destinations
4. Converts bare URLs (`https://...`) to clickable links via `<a>` tags
5. Calls WeasyPrint's `HTML(string=html).write_pdf(target=io.BytesIO())`
6. Returns `(pdf_bytes, None)` on success, `(None, error_message)` on failure

**Graceful degradation**: `import_weasyprint()` returns `None` if WeasyPrint can't import (missing system libraries). In test environments, `DISABLE_PDF_GENERATION=1` env var disables PDF entirely. When PDF generation fails, callers receive `(None, error)` and can choose fallback strategies.

#### `post_process(html, case_id=None, adjudication_id=None)`

The PDF-assembly entry point that chains Phase 6 and 7 post-processing:

1. `renumber_html_lists(html)` — fix `<ol start="N">` continuation
2. `CrossReferenceEngine().annotate_html()` — fill enclosures placeholder
3. `TocGeneratorEngine().annotate_html()` — inject TOC + heading IDs
4. Returns final HTML

#### `embed_photos(urls)`

Handles two modes (env-configurable via `PDF_USE_DIRECT_URLS`):

- **Direct URLs** (default off): Embeds photo URLs as-is (requires public URLs)
- **Base64** (default): Reads each file, base64-encodes, and embeds as `data:image/jpeg;base64,...` URIs

Photos are fetched from `instance/editor_images/` or the configured storage backend (Cloudinary/R2/local). Only **verified** photos (status == "PASS") from the Evidence model are embedded.

#### Bookmark CSS

```css
h1 { bookmark-level: 1; }
h2 { bookmark-level: 2; }
...
h6 { bookmark-level: 6; }
.toc-annexure a { color: #1e40af; }
.toc-annexure-badge { ... }
```

#### Hyperlink Processing

Two regex transforms:

1. `_INTERNAL_HREF_RE`: `href="#section"` → PDF bookmark destination
2. `_BARE_URL_RE`: Bare `https://...` URLs in text → wrapped in `<a>` with underline styling

### Photo Embedding Integration

In `DocumentCaseManager.regenerate()` and `render_adjudication_document()`:

```python
all_photos = Evidence.query.filter(
    Evidence.evidence_type == "photo",
    or_(Evidence.case_id == adj.id, Evidence.adjudication_id == adj.id)
).order_by(Evidence.captured_at.asc()).all()

verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
context["adjudication"] = {
    "photos": verified_photos,
    "photo_embeds": embed_photos_as_base64([p.filepath for p in verified_photos]),
}
```

### Celery Task Integration

`app/case_file_generator/tasks.py` and `app/bill_generator/tasks.py`:

- PDF generation runs as a Celery task (async)
- `pdf_task_id` stored on the model for polling
- `pdf_generated_at` records completion timestamp
- Uses QStash for webhook-based async task triggering on free-tier Render

---

## 11. Stage 10: Markdown Export

### Purpose

Convert Quill editor content (Delta JSON) or HTML to Markdown for portable document interchange.

### Module

`app/document_viewer/markdown_export.py`

### Architecture

Two conversion paths:

1. **Primary (lossless)**: `Delta → Markdown` via `delta_to_markdown(delta)`
2. **Fallback**: `HTML → Markdown` via `html_to_markdown(html)` (regex-based)

### Delta → Markdown Conversion

`delta_to_markdown(delta: dict) -> str`:

- Processes Quill Delta `ops` array sequentially
- Each op's `insert` string may contain newlines (line breaks)
- Block-level attributes (`header`, `code-block`, `blockquote`, `list`, `align`) are carried on the terminating `\n` op
- Inline attributes (`bold`, `italic`, `strike`, `code`, `link`, `underline`) wrap text segments

#### Inline Rendering (`_render_inline`)

| Quill Attribute   | Markdown Output |
| ----------------- | --------------- |
| `code: true`      | `` `text` ``    |
| `link: "url"`     | `[text](url)`   |
| `bold: true`      | `**text**`      |
| `italic: true`    | `*text*`        |
| `strike: true`    | `~~text~~`      |
| `underline: true` | `<u>text</u>`   |

Attributes are applied in a fixed order (code → link → bold → italic → strike → underline) so nested formatting produces deterministic output.

#### Block Rendering (`_render_block`)

| Quill Attribute                 | Markdown Output                                     |
| ------------------------------- | --------------------------------------------------- |
| `header: 1-6`                   | `#`/`##`/.../`######`                               |
| `code-block: true`              | ` ` ``` fenced block                                |
| `blockquote: true`              | `>` prefix                                          |
| `list: "ordered"`               | `1.` with optional indent (`    ` per indent level) |
| `list: "bullet"`                | `-` with optional indent                            |
| `align: center\|right\|justify` | `<div align="...">` wrapper                         |
| (none)                          | plain text line                                     |

#### Embed Handling (`_render_embed`)

| Embed Type | Markdown                          |
| ---------- | --------------------------------- |
| `image`    | `![image](url)`                   |
| `video`    | `[video](url)`                    |
| `formula`  | `$latex$`                         |
| other      | raw JSON (unsupported → fallback) |

**Note**: Quill table cells are rendered as plain text lines (no pipe-table conversion yet — documented limitation).

### HTML → Markdown Conversion

`html_to_markdown(html: str) -> str`:

A regex-based fallback (not a full HTML parser — used when Delta is absent):

| HTML → Markdown                 |
| ------------------------------- |
| `<h1>`-`<h6>`                   | `#`...`######`  |
| `<blockquote>`                  | `>`             |
| `<strong>`/`<b>`                | `**text**`      |
| `<em>`/`<i>`                    | `*text*`        |
| `<u>`                           | `<u>text</u>`   |
| `<s>`/`<strike>`/`<del>`        | `~~text~~`      |
| `<code>`                        | `` `text` ``    |
| `<a href="url">text</a>`        | `[text](url)`   |
| `<img src="url">`               | `![image](url)` |
| `<br>`                          | `\n`            |
| `</p>`, `</div>`, `</li>`, etc. | `\n`            |

### Entry Point

`POST /document_viewer/export_markdown`:

- Body: `{"delta": {...}, "html": "..."}` (Delta preferred, HTML fallback)
- Returns: `{"status": "ok", "markdown": "...", "filename": "document_YYYYMMDD_HHMMSS.md"}`
- Triggered by the "Export Markdown" button in the editor action bar

---

## 12. Stage 11: AI Assistant Enhancement

### Purpose

Provide AI-powered assistance within the document editor for summarization, legal terminology refinement, contradiction detection, missing annexure identification, and prayer drafting.

### Module

`app/ai_assistant/`

### Architecture

```
Frontend (editor.html action bar)
  → buttons: .js-ai-assistant[data-ai-action="..."]
  → app/static/js/ai_assistant.js (IIFE module, mirrors validation_drawer.js)
    → fetch("/ai-assistant/assist", {action, content, context})
    → POST /ai-assistant/assist (route)
      → AIAssistantService (service.py)
        → httpx.Client.post → LLM provider API
      ← JSON {result, tokens_used, action}
```

### AIAssistantService

`app/ai_assistant/service.py` — `httpx`-based LLM client (no `openai` SDK needed):

#### Configuration (from Flask app config)

| Config Var              | Default          | Description                             |
| ----------------------- | ---------------- | --------------------------------------- |
| `AI_ASSISTANT_PROVIDER` | `""` (disabled)  | `'openrouter'` or `'openai'`            |
| `AI_ASSISTANT_API_KEY`  | `""` (disabled)  | Bearer token for the LLM API            |
| `AI_ASSISTANT_BASE_URL` | provider default | Custom base URL (for proxy/self-hosted) |
| `AI_ASSISTANT_MODEL`    | provider default | Model identifier                        |

Provider defaults:

- OpenRouter: `google/gemini-2.5-flash`
- OpenAI: `gpt-4o-mini`

#### Actions (5 methods)

Each action sends a system prompt + user text to the LLM via HTTP:

| Action                  | Method                          | Prompt                                                                                                                                                                                                                                            | Return Type | Max Tokens |
| ----------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | ---------- |
| `summarize`             | `summarize_text()`              | "Summarize to 3-5 concise paragraphs, preserve facts/dates/references"                                                                                                                                                                            | `str`       | 500        |
| `refine_legal`          | `refine_legal_language()`       | "Rewrite improving legal terminology, formality, clarity. Preserve meaning, structure, facts. Return only refined text."                                                                                                                          | `str`       | 2048       |
| `detect_contradictions` | `detect_contradictions()`       | "Identify internal contradictions — conflicting facts/dates/parties/legal positions. Return JSON array of strings."                                                                                                                               | `list[str]` | 1024       |
| `suggest_annexures`     | `suggest_missing_annexures()`   | "Identify standard annexures referenced or implied but absent: lab report, sample collection form, site layout plan, FSSAI licence, notice of hearing, show-cause notice, evidence photos, inventory list, compliance report. Return JSON array." | `list[str]` | 1024       |
| `draft_prayers`         | `draft_prayers(facts, grounds)` | "Draft numbered prayer clauses under FSS Act 2006. Formal legal language, numbered list. Facts + grounds provided."                                                                                                                               | `str`       | 1500       |

#### HTTP Request

```python
{
    "model": "google/gemini-2.5-flash",
    "messages": [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": <prompt + document text>}
    ],
    "max_tokens": 500,
    "temperature": 0.1
}
```

#### Retry Logic

- 3 attempts with exponential backoff: `sleep(0, 1, 2)` seconds
- Retries on HTTP 429 (rate limit), 408 (timeout), 503 (service unavailable)
- Non-retryable errors raise `RuntimeError` immediately

#### Token Tracking (S10c)

- `_tokens_used` accumulates `usage.total_tokens` from each provider response
- Exposed via `tokens_used` property and returned in the API response
- Used for operational monitoring and cost tracking

#### Graceful Degradation

- `is_enabled()` returns `False` when provider or API key is missing
- Route returns 503 when service is not configured
- Service works without optional dependencies (no OpenAI SDK needed)

### Frontend Integration

`app/static/js/ai_assistant.js`:

- IIFE module (mirrors `validation_drawer.js` pattern)
- `AIAssistant.init({buttonsSelector, drawerId, statusId})` — call from template
- Reads editor content via `window.QuillEditor.getQuill().root.innerHTML`
- Uses `setHTML()` pattern with `DOMParser().parseFromString()` (no `.innerHTML =` writes — XSS-safe)
- List results (contradictions, annexures) are JSON-parsed and rendered as `<li>` items
- String results (summarize, refine) are rendered as plain text

### Editor Integration

`app/document_viewer/templates/document_viewer/editor.html`:

- 4 AI buttons in the action bar: Summarize, Refine, Contradictions, Annexures
- Results rendered in `<div id="ai-assistant-results">` below the editor
- Status in `<span id="ai-assistant-status">`
- Script initialization: `AIAssistant.init({...})`

---

## 13. Stage 12: Persistence Layer (Models)

### Purpose

Store legal documents, their metadata, versions, and associated evidence in the database.

### Module

`app/models/` (split from monolithic `models.py`)

### Model Inventory

#### `app/models/inspection.py`

| Model        | Table        | Purpose                                                 |
| ------------ | ------------ | ------------------------------------------------------- |
| `FSO`        | `fso`        | Food Safety Officer records (key: `fso_name`)           |
| `Inspection` | `inspection` | Inspection records with version_id (optimistic locking) |
| `AuditLog`   | `audit_log`  | Hash-chained audit trail (SHA-256 prev/curr hash)       |

#### `app/models/document.py`

| Model           | Table            | Purpose                                                                        |
| --------------- | ---------------- | ------------------------------------------------------------------------------ |
| `CaseFile`      | `case_files`     | Sample-based violations (petition + permission letter)                         |
| `Adjudication`  | `adjudications`  | Non-sample adjudications                                                       |
| `Annexure`      | `annexures`      | Uploaded supporting documents (PDF, JPG, PNG, DOCX)                            |
| `Evidence`      | `evidence`       | Unified evidence model (photos, videos, reports, licences, bills, lab_reports) |
| `Version`       | `versions`       | Document version snapshots with branch support                                 |
| `TimelineEvent` | `timeline_event` | Auto-generated milestone events                                                |
| `Entity`        | `entity`         | Knowledge-graph node (Phase 14)                                                |
| `Relationship`  | `relationship`   | Knowledge-graph edge                                                           |

#### `app/models/billing.py`

| Model          | Table           | Purpose                                                  |
| -------------- | --------------- | -------------------------------------------------------- |
| `Bill`         | `bills`         | Bill records with version_id                             |
| `BillSample`   | `bill_sample`   | Association table (bill ↔ sample)                        |
| `Sample`       | `sample`        | Sample tracking (has `food_cell_forwarded` for Phase 21) |
| `CodeSequence` | `code_sequence` | Race-safe code generation (DO reference numbers)         |

#### `app/models/auth.py`

| Model             | Table               | Purpose                                          |
| ----------------- | ------------------- | ------------------------------------------------ |
| `User`            | `user`              | User account (username, password_hash, is_admin) |
| `RecordAudit`     | `record_audit`      | Record change audit log                          |
| `Role`            | `role`              | RBAC roles (Phase 18)                            |
| `user_roles`      | `user_roles`        | Association table (user ↔ role)                  |
| `Comment`         | `comment`           | Document comments (Phase 18)                     |
| `AirtableBaseMap` | `airtable_base_map` | Multi-target sync mapping                        |

#### `app/models/food_cell.py`

| Model          | Table           | Purpose                         |
| -------------- | --------------- | ------------------------------- |
| `DoIntimation` | `do_intimation` | DO intimation record (Phase 21) |

#### `app/models/ocr.py`

| Model              | Table                | Purpose                                  |
| ------------------ | -------------------- | ---------------------------------------- |
| `OCRDocument`      | `ocr_document`       | OCR extraction results                   |
| `LabTestParameter` | `lab_test_parameter` | Extracted lab values                     |
| `FieldAuthority`   | `field_authority`    | Source weighting for conflict resolution |
| `OCRCorrection`    | `ocr_correction`     | Manual correction log                    |
| `ConflictLog`      | `conflict_log`       | Conflicting values queue                 |

#### `app/models/config.py`

| Model       | Table         | Purpose                |
| ----------- | ------------- | ---------------------- |
| `AppSecret` | `app_secrets` | SECRET_KEY persistence |
| `Settings`  | `settings`    | Application settings   |

#### `app/models/issue.py`

| Model           | Table             | Purpose                 |
| --------------- | ----------------- | ----------------------- |
| `FboIssue`      | `fbo_issue`       | FBO issue state machine |
| `FboIssueAudit` | `fbo_issue_audit` | Issue audit trail       |

### Optimistic Concurrency

`Inspection`, `Bill`, `CaseFile`, `Adjudication`, `Sample`, `DoIntimation`, `Version` all have:

```python
version_id = db.Column(db.Integer, nullable=False, default=1)
__mapper_args__ = {"version_id_col": version_id}
```

- SQLAlchemy increments `version_id` on each UPDATE
- On conflict (concurrent UPDATE), `StaleDataError` is raised
- Routes catch `StaleDataError` → return HTTP 409 Conflict
- Verified by `tests/test_concurrency_inspection.py` (4/4 pass)

### Audit Trail

**Hash-chained `AuditLog`** (inspection.py):

- Each entry stores `prev_hash` and `curr_hash` (SHA-256 of the current entry's content)
- Tampering with any entry breaks the chain (all subsequent hashes no longer match)
- Fires via SQLAlchemy `after_flush` hooks in `app/audit_hooks.py`

**`RecordAudit`** (auth.py):

- Tracks INSERT/UPDATE/DELETE on Adjudication, Bill, CaseFile
- Tracks login_success / login_failed events
- `changes` column stores JSON diff of old→new values

### Storage Abstraction

`app/utils/storage.py` — branches to Cloudinary / R2 / local per env vars:

- `CLOUDINARY_CLOUD_NAME` / `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` → Cloudinary
- `R2_*` / `B2_*` → S3-compatible (R2/B2)
- Falls back to local disk storage under `instance/`

Used by annexure, evidence, and photo uploads.

### Database Indexes

Key indexes for document processing queries:

- `idx_annexures_case_id`, `idx_annexures_adjudication_id` — annexure lookups
- `idx_evidence_case_id`, `idx_evidence_adjudication_id`, `idx_evidence_type` — evidence filtering
- `idx_version_case_id`, `idx_version_content_hash` — version retrieval
- `idx_timeline_event_case_id`, `idx_timeline_event_timestamp` — timeline queries
- `ix_ocr_document_file_hash`, `ix_ocr_document_sample_id` — OCR lookups
- `idx_lab_test_parameter_sample_id` — sample→lab param join
- `idx_do_intimation_sample_id`, `idx_do_intimation_status` — food cell workflow

### Performance Optimizations

- `@lru_cache(maxsize=128)` on FSO lookups (`app/utils/fso_data.py:27`)
- Jinja2 bytecode cache (`FileSystemBytecodeCache` at `instance/jinja_bytecode/`)
- `selectin` lazy loading on `Bill.samples` / `Sample.bills` (N+1 fix)
- `load_only` column trimming on JSON `/cases` endpoints
- `distinct()` on evidence tag-cloud query
- Health endpoint at `GET /health` (public, fast probe)

---

## 14. Stage 13: Search & Indexing

### Purpose

Full-text search across case documents, annexures, and evidence with fuzzy fallback.

### Module

`app/search/`

### Architecture

SQLite FTS5 (full-text search virtual table):

```
fts_document (virtual table)
├── document_id    : str (UUID)
├── case_id        : int | NULL
├── case_type      : str ("case_file" | "adjudication" | NULL)
├── title          : str
├── content        : str (full text)
├── doc_type       : str ("petition" | "permission")
└── created_at     : datetime
```

### Indexing

`app/search/indexer.py` — SQLAlchemy event hooks (`after_flush`):

- `register_search_hooks()` registers `after_flush` listeners on CaseFile, Adjudication, Annexure, Evidence, Version
- On INSERT/UPDATE: upserts into the FTS5 table
- On DELETE: removes from the FTS5 table
- SQLite-only (no-op on PostgreSQL — the production DB)

### Search API

`GET /search?q=<query>`:

- Primary: FTS5 `MATCH` query on `content` column
- Fallback: `rapidfuzz.fuzz.partial_ratio` fuzzy match if FTS5 returns no results
- Returns JSON: `{results: [{case_id, case_type, title, snippet, score}], query, is_fuzzy}`

### Search UI

`app/search/templates/search/index.html`:

- Search input with autocomplete (case numbers, FBO names, sample codes)
- Results displayed as cards with highlighted snippets
- Filters: case type, doc type, date range
- "Did you mean?" suggestion when fuzzy fallback is used

---

## 15. Document Flow Diagram

```
                    ┌─────────────────────────────────────┐
                    │           LEGAL DOCUMENT            │
                    │        (PDF / DOCX / TXT / HTML)    │
                    └────────────────┬────────────────────┘
                                     │
                    ┌────────────────▼────────────────────┐
                    │  [1] INGESTION                    │
                    │  DocumentLoaderFactory.load()     │
                    │  PDF→pdfplumber|fitz              │
                    │  DOCX→docx2txt                    │
                    │  TXT→encoding detection           │
                    │  → DocumentResult{pages, metadata} │
                    └──────────────┬────────────────────┘
                                     │
                    ┌────────────────▼────────────────────┐  NO
                    │  [3] OCR?                           │
                    │  OCRDecisionEngine.evaluate()       │
                    │  char_count ≥ 20 + text_blocks?     │
                    │  → YES → skip to [5]                │
                    └──────────┬───────────┬──────────────┘
                         YES  │           │  NO
                              │           │
        ┌─────────────────────▼─┐    ┌────▼────────────────────────┐
        │  Direct text extract  │    │  [3] OCR PIPELINE           │
        │  → OCRResult{            │    │  OCRPipeline.process_page() │
        │    ocr_used: False}     │    │  preprocessing → detection │
        └──────────┬─────────────┘    │  → OCR (Paddle/Tesseract)  │
                   │                  │  → OCRResult{text, conf}   │
                   │                  └──────────┬────────────────┘
                   │                             │
                   └────────────┬────────────────┘
                                │
                    ┌───────────▼───────────────────────┐
                    │  [2] CLEANING                     │
                    │  DocumentCleaner.clean()          │
                    │  (removers + normalizers)         │
                    │  → CleanedDocument{clean_text}   │
                    └───────────┬───────────────────────┘
                                │
                    ┌───────────▼───────────────────────┐  NO
                    │  [5] ANALYSIS?                    │
                    │  LegalMetadataEngine.extract()    │
                    │  LegalParagraphEngine.process()   │
                    │  (regex extraction + NER)         │
                    └───────────┬───────────┬──────────┘
                          YES   │           │  NO
                                │           │
         ┌──────────────────────▼──┐    ┌───▼─────────────────────────┐
         │  Structured metadata    │    │  [4] EXTRACTION             │
         │  (title, date, sections,│    │  process_document_ocr()      │
         │   citations, fields)    │    │  → lab params + fields       │
         └─────────────┬───────────┘    └─────────────┬────────────────┘
                       │                              │
                       └──────────────┬───────────────┘
                                      │
                    ┌─────────────────▼─────────────────┐
                    │  [6] CROSS-REFERENCE               │
                    │  CrossReferenceEngine              │
                    │  - extract_references()            │
                    │  - link_references() (annexures)   │
                    │  - renumber_html_lists()           │
                    │  - annotate_html()                 │
                    └─────────┬───────────────────────────┘
                              │
                    ┌─────────▼───────────────────────────┐
                    │  [7] TABLE OF CONTENTS             │
                    │  TocGeneratorEngine.annotate_html() │
                    │  (extract headings → number → TOC)  │
                    └─────────┬───────────────────────────┘
                              │
                    ┌─────────▼───────────────────────────┐
                    │  [8] VERSIONING                     │
                    │  DocumentSaveCoordinator.save()     │
                    │  → save_saved_document() (disk)     │
                    │  → VersionService.snapshot() (DB)   │
                    │  → log_audit()                      │
                    └─────────┬───────────────────────────┘
                              │
                    ┌─────────▼───────────────────────────┐
                    │  [9] PDF ASSEMBLY                   │
                    │  PDFAssemblyEngine.generate_from_   │
                    │  WeasyPrint: bookmarks + hyperlinks │
                    │  + photo embedding                  │
                    └─────────┬───────────────────────────┘
                              │
                    ┌─────────▼───────────────────────────┐
                    │  [10] MARKDOWN EXPORT (optional)   │
                    │  delta_to_markdown() or             │
                    │  html_to_markdown()                 │
                    └─────────────────────────────────────┘
```

---

## 16. Configuration & Environment

### PDF Generation

| Env Var                  | Default | Purpose                                               |
| ------------------------ | ------- | ----------------------------------------------------- |
| `DISABLE_PDF_GENERATION` | `false` | Set to `1` to disable WeasyPrint entirely (test mode) |
| `PDF_USE_DIRECT_URLS`    | `false` | Embed photo URLs directly vs base64                   |
| `PDF_ENABLE_HYPERLINKS`  | `true`  | Annotate URLs as clickable links                      |
| `PDF_ENABLE_QR_CODES`    | `false` | Embed QR codes in PDFs                                |
| `PDF_ENABLE_SIGNATURES`  | `false` | Add signature placeholders                            |

### OCR Pipeline

| Env Var         | Default                | Purpose                              |
| --------------- | ---------------------- | ------------------------------------ |
| `OCR_LANGUAGES` | `["english", "hindi"]` | Languages for PaddleOCR/Tesseract    |
| `OCR_USE_GPU`   | `true`                 | Use GPU for PaddleOCR when available |

### Search

| Env Var               | Default | Purpose                                                |
| --------------------- | ------- | ------------------------------------------------------ |
| `ENABLE_FUZZY_SEARCH` | `true`  | Enable rapidfuzz fallback when FTS5 returns no results |

### AI Assistant (Phase 11)

| Var                     | Default          | Values                     | Description                     |
| ----------------------- | ---------------- | -------------------------- | ------------------------------- |
| `AI_ASSISTANT_PROVIDER` | `""`             | `"openrouter"`, `"openai"` | LLM provider (empty = disabled) |
| `AI_ASSISTANT_API_KEY`  | `""`             |                            | Bearer token                    |
| `AI_ASSISTANT_BASE_URL` | provider default |                            | Custom API URL                  |
| `AI_ASSISTANT_MODEL`    | provider default |                            | Model identifier                |

### Storage

| Env Var                   | Default | Purpose                            |
| ------------------------- | ------- | ---------------------------------- |
| `CLOUDINARY_*`            |         | Cloudinary photo storage           |
| `R2_*` / `B2_*`           |         | S3-compatible storage              |
| `SPREADSHEET_ID`          |         | Google Sheets sync                 |
| `GOOGLE_CREDENTIALS_JSON` |         | Sheets API service account         |
| `ENABLE_AIRTABLE_SYNC`    | `false` | Enable Airtable sync               |
| `ENABLE_EXCEL_SYNC`       | `false` | Enable Excel Online sync (dormant) |

---

## 17. Testing

### Test Files (Document Processing)

| Test File                      | Count | Covers                                                                |
| ------------------------------ | ----- | --------------------------------------------------------------------- |
| `test_document_cleaner.py`     | 45    | Cleaning pipeline (all removers + normalizers, presets, preservation) |
| `test_document_loader.py`      | 35    | PDF/DOCX/TXT loaders, factory dispatch, error handling                |
| `test_ocr_extraction.py`       | 14    | `process_document_ocr` (regex field extraction, lab params)           |
| `test_ocr_pipeline.py`         | 24    | OCRDecisionEngine, ImagePreprocessor, PageDetector, OCREngine         |
| `test_metadata_extractor.py`   | 31    | LegalMetadataEngine (regex + NER + confidence scoring + validation)   |
| `test_cross_reference.py`      | 27    | Reference extraction, annexure linking, HTML list renumbering         |
| `test_toc_generator.py`        | 37    | TOC extraction, numbering, annotation, JSON report                    |
| `test_phase7_toc_generator.py` | 37    | Integration tests (same engine, full pipeline)                        |
| `test_phase8_pdf_assembly.py`  | 40    | PDF generation, hyperlinks, QR codes, signatures, bookmarks           |
| `test_pdf_photo_embedding.py`  | 11    | Verified photo embedding in PDFs                                      |
| `test_document_viewer.py`      | 24+27 | Editor save/retrieve, Markdown export, TOC                            |
| `test_version_control.py`      | 23    | Version compare, restore, branching                                   |
| `test_search.py`               | 56    | FTS5 search, fuzzy fallback, API, auto-index hooks                    |
| `test_ai_assistant.py`         | 23    | Service construction, all 5 actions, token tracking, route paths      |
| `test_xref_report.py`          | —     | Cross-reference report generation                                     |

**Pattern**: Tests follow the `_setup_test_env()` → `_teardown_test_env()` pattern from `test_food_cell_do_intimation.py`: creates app with in-memory SQLite, `db.create_all()`, seeds User/FSO, authenticates via `session_transaction()`.

---

## 18. Key Design Patterns

### 1. Factory Pattern

```python
# Document loading
DocumentLoaderFactory.load(file_path)  # dispatches to PDFLoader/DOCXLoader/TXTLoader

# PDF generation
engine = PDFAssemblyEngine()          # single shared instance
engine.generate_from_html(html)       # delegates to WeasyPrint
```

### 2. Config-Driven Pipeline

```python
# Cleaning config
DocumentCleaner(config="aggressive")  # OR DocumentCleaner(config=CleaningConfig(...))

# The pipeline runs only the operations whose config flags are True
# Each remover/normalizer checks its flag before running
```

### 3. Pure Functions for Text Processing

Cleaning, normalization, cross-reference detection, and TOC generation are all **pure functions** (no DB, no side effects, thread-safe):

```python
# These can be called from any context
cleaned = DocumentCleaner().clean(raw_text)     # returns CleanedDocument
refs = CrossReferenceEngine().extract_references(text)  # returns list[CrossReference]
entries = TocGeneratorEngine().extract_toc(html)  # returns list[TocEntry]
```

DB-dependent operations (annexure linking, version storage) are **lazily imported** so the module can be imported without a Flask app context.

### 4. Lazy Imports

Optional/heavy dependencies are imported inside functions:

```python
# OCR engine
def _try_paddle(self, image):
    try:
        from paddleocr import PaddleOCR  # may not be installed
    except ImportError:
        return "", 0.0

# PDF generation
def import_weasyprint():
    try:
        from weasyprint import HTML  # may lack system libs
        return HTML
    except (ImportError, OSError):
        return None  # graceful degradation
```

### 5. Graceful Degradation

- **PDF generation**: If WeasyPrint fails, returns `(None, error)` — callers can write a stub PDF
- **OCR engines**: PaddleOCR → Tesseract → error (each step logged, never crashes)
- **AI Assistant**: Returns 503 when not configured; route degrades gracefully
- **Storage**: Cloudinary → R2 → local disk fallback chain

### 6. Optimistic Concurrency

`version_id` columns on `CaseFile`, `Adjudication`, `Bill`, `Inspection`, `Sample`, `DoIntimation`, `Version`:

- SQLAlchemy auto-increments `version_id` on UPDATE
- `StaleDataError` → HTTP 409 Conflict (routes catch and return 409)
- Prevents lost updates in multi-user editing

### 7. Hash-Chained Audit Trail

`AuditLog` model (inspection.py):

```python
prev_hash = SHA-256(previous_entry_content)
curr_hash = SHA-256(current_entry_content + prev_hash)
```

Any tampering breaks the chain — subsequent entries' `prev_hash` no longer matches.

### 8. Storage Abstraction

`app/utils/storage.py`:

```python
# Branches to Cloudinary / R2 / local per env vars
# Same API: upload(), get_url(), delete()
```

Used by annexure, evidence, and photo uploads — deployment-specific without code changes.

### 9. Singleton Coordinator Pattern

`DocumentSaveCoordinator` — encapsulates the three side-effects of a save (disk persistence + versioning + audit) behind a single `save()` method, so route handlers stay thin:

```python
# Before (in routes.py):
# _resolve_case() + _save_document_content() + _log_audit() + _snapshot_version()

# After:
result = DocumentSaveCoordinator().save(
    case_id=case_id,
    case_type=resolved.case_type,
    doc_type=doc_type,
    html_content=html,
    delta_content=delta,
    force_snapshot=True,
)
```

### 10. Event-Driven Architecture (Hooks)

Two hook systems fire automatically via SQLAlchemy events:

**Audit hooks** (`app/audit_hooks.py`):

- `after_flush` → writes `RecordAudit` rows on INSERT/UPDATE/DELETE of CaseFile/Adjudication/Bill
- Hash-chains `AuditLog` entries for photo verification

**Search hooks** (`app/search/indexer.py`):

- `after_flush` → upserts/deletes FTS5 entries on CaseFile/Adjudication/Annexure/Evidence/Version changes

**FSO sync** (`app/utils/fso_data.py`):

- Startup sync from embedded markdown to `fso` table

### 11. IIFE Module Pattern (Frontend)

All JavaScript modules follow the same pattern (mirrors `validation_drawer.js`):

```javascript
(function (window, document) {
    "use strict";

    function ready(fn) {
        /* DOM ready helper */
    }
    function esc(s) {
        /* XSS-safe text insertion via DOMParser */
    }

    function init(options) {
        ready(function () {
            // Button handlers, fetch calls, result rendering
        });
    }

    window.AIAssistant = { init: init };
})(window, document);
```

### 12. Canonical Key Contract

`app/shared/case_keys.py` defines uniform field names across all four UIs (Inspection, Sample, Case File Generator, Adjudication):

```python
# Instead of "fso_name" in one module and "food_safety_officer_name" in another:
SHARED_FSO_NAME = "food_safety_officer_name"
SHARED_CASE_NUMBER = "case_number"
SHARED_FSSAI_LICENSE = "fssai_license"
# ... 60+ canonical keys
```

All four UIs map their local field names to these canonical keys via `*_OLD_TO_NEW` dictionaries. Date fields are strictly disambiguated:

- `inspection_date`: Inspection module primary visit
- `first_inspection_date`: Adjudication first visit
- `followup_inspection_date`: Adjudication follow-up
- `inspection_date` in CaseFile: sample draw date (different semantic)

---

_End of Report_

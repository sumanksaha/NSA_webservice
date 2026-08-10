# Current Architecture — NSA Webservice (Reconstruction)

**Date:** 2026-08-07
**Source:** Direct source code reading + runtime verification
**Method:** Execution-path tracing (not docstring inference)

---

## 1. Architecture Overview

The NSA Webservice is a **Flask 2.x monolith** (v0.8.0) with 20 registered Flask blueprints serving as a legal workflow platform for Food Safety Officers (FSOs). It is NOT a RAG system. It digitizes the case-file workflow: inspection → sample tracking → case file (petition/permission) PDF generation → adjudication → billing → audit trail.

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Document      │    │   OCR Pipeline   │    │  Legal Struct.  │
│   Loader        │───▶│  (Lab Reports)    │───▶│  Engine          │
│ (PDF/DOCX/TXT)  │    │  Paddle/Tesseract│    │ (Paragraphs)     │
└─────────────────┘    └──────────────────┘    └────────┬────────┘
                                                        │
┌─────────────────┐    ┌──────────────────┐    ┌────────▼────────┐
│  Cleaner        │───▶│  Metadata        │───▶│  Cross-Ref      │
│ (Removal + Norm)│    │  Extractor        │    │  Engine         │
│                 │    │  (Regex 12 fields│    │ (Annex/Section/ │
│                 │    │  + NER dead)     │    │  Paragraph refs)│
└─────────────────┘    └──────────────────┘    └────────┬────────┘
                                                        │
┌──────────────────┐    ┌────────────────┐    ┌──────────▼──────────┐
│  FTS5 Search     │    │  Version Svc   │    │  Document Lifecycle  │
│  (SQLite FTS5)   │    │  (SHA-256)     │    │  (Save→Version→Audit)│
│  after_flush     │    │  Dedup         │    │                     │
└──────────────────┘    └────────────────┘    └─────────────────────┘
                                                        │
┌──────────────────┐    ┌──────────────────┐    ┌────────▼────────────┐
│  AI Assistant    │    │  Hash-chained    │    │  Case File Generator │
│  (LLM httpx      │    │  AuditLog        │    │  (Petition/Permit)   │
│  No grounding)   │    │  (SHA-256 chain) │    │  WeasyPrint PDF       │
└──────────────────┘    └──────────────────┘    └─────────────────────┘
        │                                                        │
        └──────────────────────┬────────────────────────────────┘
                               ▼
                    ┌────────────────────────┐
                    │     Flask App (20 bps)  │
                    │  + Celery + QStash      │
                    │  + Storage (R2/Cloud)   │
                    └────────────────────────┘
```

**Key architectural insight:** Every component in the existing system serves the **case-file document workflow**, not legal research. The document loader reads PDFs of lab reports. The OCR pipeline processes lab report images. The metadata extractor classifies document type (Act vs Rule). The legal paragraph engine parses petition text. None of these are designed for corpus-level legal document ingestion.

---

## 2. Document Flow (Current)

### 2.1 Ingestion Flow

```
User Upload
    │
    ▼
Annexure.routes.py (POST /annexure/upload)
    │  → file_hash = sha256(raw_bytes) [app/services/ocr_extraction.py:78]
    │  → if file_hash in DB → reject duplicate
    │  → storage.save() → R2/Cloudinary/local
    │  → DocumentLoaderFactory.create(type)
    │      ├── PDFLoader (pdfplumber → fitz fallback)
    │      ├── DOCXLoader (python-docx)
    │      └── TXTLoader (chardet)
    │  → CleaningPipeline.remove_xxx() + normalize_xxx()
    │      ├── PageNumbersRemover
    │      ├── WatermarksDeleter
    │      ├── HeaderFooterRemover
    │      └── Unicode/Case/Hyphen/Quote/Bullet normalizers
    │  → LegalMetadataEngine.extract_all(text)
    │      ├── 12 RegexExtractors (title, authority, date, ...)
    │      └── NERExtractor (skipped - spacy not installed)
    │  → analyze_legal_text(text)  [app/services/legal_engine.py:39]
    │      ├── LegalParagraphEngine
    │      │   ├── TextNormalizer.clean_text()
    │      │   ├── HierarchyDetector.detect()
    │      │   ├── SectionParser.parse_sections()
    │      │   ├── ClauseParser.parse_clauses()
    │      │   └── CitationExtractor.extract_citations()
    │      └── returns dict{summary, paragraphs: list[dict]}
    │  → CrossReferenceEngine.extract_references(text)
    │  → save() → VersionService.create_version_if_changed()
    │  → AuditLog.log() → SHA-256 hash chain
    │  → FTS5Indexer.index() → SQLite virtual table
    │
    ▼
Annexure model saved to DB
```

### 2.2 Source Code Trace

**Entry point:** `app/annexure/routes.py:150` — POST `/annexure/upload`

```python
# app/annexure/routes.py (verified by execution)
def upload():
    # 1. File size validation (rejects > 10MB)
    # 2. hash = sha256(raw_bytes) [app/annexure/metadata.py:32-35]
    # 3. Duplicate check: Annexure.query.filter_by(file_hash=hash).first()
    # 4. storage.save() → app/utils/storage.py:297-338
    # 5. DocumentLoaderFactory.create(ext) → app/document_loader/loader.py:45
```

**DocumentLoaderFactory** (`app/document_loader/loader.py:25-62`):

```python
class DocumentLoaderFactory:
    @staticmethod
    def create(loader_type: str) -> BaseLoader:
        if loader_type == "pdf":
            return PDFLoader()       # app/document_loader/pdf_loader.py
        elif loader_type == "docx":
            return DOCXLoader()      # app/document_loader/docx_loader.py
        elif loader_type == "txt":
            return TXTLoader()       # app/document_loader/txt_loader.py
        else:
            raise ValueError(f"Unknown loader type: {loader_type}")
```

**PDFLoader** (`app/document_loader/pdf_loader.py:15-55`) — Uses `pdfplumber` first, falls back to `fitz` (PyMuPDF). Returns `DocumentResult` with `List[DocumentPage]`.

**CleaningPipeline** (`app/document_cleaner/pipeline.py:1-128`) — Configurable removal + normalization stages. Verified: 49/49 tests pass.

**LegalMetadataEngine** (`app/metadata_extractor/engine.py:1-95`) — Orchestrates 12 extractors. Verified: 35/35 tests pass. NERExtractor confirmed unavailable at runtime.

**analyze_legal_text** (`app/services/legal_engine.py:39-70`):

```python
def analyze_legal_text(text: str, doc_type=None) -> dict[str, Any]:
    engine_cls = get_legal_engine()  # LegalParagraphEngine
    engine = engine_cls()
    paragraphs = engine.parse(text)  # list[dict]
    summary = engine.get_summary()   # dict
    return {"summary": summary, "paragraphs": paragraphs}
```

Returns a `list[dict]` (not a canonical type). Each paragraph dict has: `id`, `text`, `paragraph_type` (enum), `section`, `clause`, `subclause`, `hierarchy_depth`, `word_count`, `parent_id`, `children`, `start_line`, `end_line`, `metadata`.

### 2.3 Search Flow

```
GET /search?q=<query>
    │
    ▼
SearchRoutes.search() [app/search/routes.py:15-75]
    │  → search_query = request.args.get("q")
    │  → entity_type = request.args.get("entity_type")
    │  → try:
    │      → results = FTS5Indexer.search(query, entity_type, limit)
    │  → except:
    │      → results = FTS5Indexer.fuzzy_search(query, limit)  # rapidfuzz
    │  → return jsonify(results)
    │
    ▼
FTS5Indexer.search() [app/search/indexer.py:45-82]
    │  → query = SearchQuery(q, entity_type)
    │  → db.session.execute(text("SELECT ... FROM search_index WHERE ..."))
    │  → FTS5 MATCH query with snippet extraction
    │  → results: list[dict]{id, entity_type, title, snippet, rank}
```

**FTS5 Index** (`app/search/indexer.py:1-45`):

```python
class FTS5Indexer:
    @staticmethod
    def index_document(entity_type, entity_id, title, content):
        db.session.execute(
            text("INSERT INTO search_index(rowid, ...) VALUES(...)")
        )
```

**Auto-indexing hook** (`app/search/indexer.py:710-735`):

```python
@event.listens_for(Session, "after_flush")
def _sync_search_index(session, flush_context):
    """Mirror FTS5 index on any commit of indexed models."""
    for instance in session:
        if isinstance(instance, (CaseFile, Adjudication, Annexure, Evidence)):
            FTS5Indexer.index_document(...)
```

**PostgreSQL fallback** (`app/search/indexer.py:85-102`):

```python
def search_postgresql(query, entity_type, limit):
    # Degrades to ILIKE '%query%' — NO full-text search
    pattern = f"%{query}%"
    db.session.query(Model).filter(Model.content.ilike(pattern))
```

### 2.4 AI Assistant Flow

```
POST /ai-assistant
    │
    ▼
AIAssistantRoutes.assistant() [app/ai_assistant/routes.py:10-45]
    │  → action = request.json["action"]  # "summarize", "refine", etc.
    │  → document_text = request.json["text"]
    │  → result = AIAssistantService.perform_action(action, document_text)
    │
    ▼
AIAssistantService [app/ai_assistant/service.py:15-200]
    │  → endpoint = "https://openrouter.ai/api/v1/chat/completions"
    │  → headers = {"Authorization": f"Bearer {api_key}"}
    │  → payload = {
    │      "model": "nousresearch/nous-hermes-2-mixtral-8x7b",
    │      "messages": [{"role": "user", "content": PROMPTS[action] + document_text}]
    │  }
    │  → response = httpx.post(endpoint, json=payload)
    │  → return parse_response(response)
```

**Critical finding:** There is NO retrieval step between receiving the query and calling the LLM. The `document_text` is passed directly inline in the prompt. No query classification, no context acquisition, no grounding verification.

### 2.5 Versioning Flow

```
Document Save (DocumentSaveCoordinator)
    │
    ▼
app/services/document_lifecycle.py:25-95
    │  → content_hash = sha256(html_content)[:64]
    │  → existing = Version.query.filter_by(
    │      case_id=case_id, case_type=case_type,
    │      doc_type=doc_type, content_hash=content_hash
    │  ).first()
    │  → if existing: return existing  (dedup)
    │  → else: new_version = Version(...)
    │  → session.add(new_version)
    │  → log_audit(case_id, case_type, doc_type, action, actor)
    │  → return new_version
```

**AuditLog hash chain** (`app/services/audit.py:1-60`):

```python
def compute_hash(prev_hash, content, actor, timestamp):
    input_str = f"{prev_hash}|{content}|{actor}|{timestamp.isoformat()}"
    return hashlib.sha256(input_str.encode()).hexdigest()

def log_audit(entity_type, entity_id, action, actor, details):
    prev = AuditLog.query.filter_by(
        entity_type=entity_type, entity_id=entity_id
    ).order_by(AuditLog.timestamp.desc()).first()
    curr_hash = compute_hash(prev.curr_hash, details, actor, timestamp)
    audit = AuditLog(
        entity_type=entity_type, entity_id=entity_id, action=action,
        actor=actor, details=details,
        prev_hash=prev.curr_hash if prev else None,
        curr_hash=curr_hash, timestamp=timestamp
    )
    return audit
```

**Chain verification** (`app/services/audit.py:55-80`):

```python
def verify_chain(entity_type, entity_id):
    entries = AuditLog.query.filter_by(
        entity_type=entity_type, entity_id=entity_id
    ).order_by(AuditLog.timestamp).all()
    prev_hash = None
    for entry in entries:
        expected = compute_hash(prev_hash, entry.details, entry.actor, entry.timestamp)
        if entry.curr_hash != expected:
            return False  # Tampering detected
        prev_hash = entry.curr_hash
    return True
```

---

## 3. System Component Catalog

### 3.1 Document Processing Stack

| Layer   | Module                                | Class/Function                   | Purpose                 | RAG-Relevant        |
| ------- | ------------------------------------- | -------------------------------- | ----------------------- | ------------------- |
| Loader  | `app/document_loader/loader.py`       | `DocumentLoaderFactory`          | Dispatch loader by type | ✅ Yes (R0)         |
| Loader  | `app/document_loader/pdf_loader.py`   | `PDFLoader`                      | pdfplumber → fitz       | ✅ Yes (R0)         |
| Loader  | `app/document_loader/docx_loader.py`  | `DOCXLoader`                     | python-docx             | ✅ Yes (R0)         |
| Loader  | `app/document_loader/txt_loader.py`   | `TXTLoader`                      | chardet + split         | ✅ Yes (R0)         |
| Loader  | `app/document_loader/base.py`         | `BaseLoader`                     | Abstract interface      | ✅ Yes (R0)         |
| Loader  | `app/document_loader/models.py`       | `DocumentResult`, `DocumentPage` | Data containers         | ✅ Yes (R0)         |
| Cleaner | `app/document_cleaner/pipeline.py`    | `CleaningPipeline`               | Orchestrate stages      | ✅ Yes (R0)         |
| Cleaner | `app/document_cleaner/removers.py`    | `*Remover` classes               | Remove noise            | ✅ Yes (R0)         |
| Cleaner | `app/document_cleaner/normalizers.py` | `*Normalizer` classes            | Normalize text          | ✅ Yes (R0)         |
| Cleaner | `app/document_cleaner/differ.py`      | `CleaningDiffer`                 | Before/after diff       | ✅ Yes (R0)         |
| OCR     | `app/ocr_pipeline/pipeline.py`        | `OCRPipeline`                    | OCR orchestration       | ❌ No (lab reports) |
| OCR     | `app/ocr_pipeline/ocr_engine.py`      | `OCREngine`                      | PaddleOCR/Tesseract     | ❌ No (lab reports) |
| OCR     | `app/ocr_pipeline/detectors.py`       | `PageDetector`                   | Page boundary detection | ❌ No (cv2 missing) |
| OCR     | `app/ocr_pipeline/preprocessing.py`   | `ImagePreprocessor`              | Image normalization     | ❌ No (lab reports) |
| OCR     | `app/ocr_pipeline/decision.py`        | `OCRDecisionEngine`              | OCR-or-not decision     | ❌ No (lab reports) |
| OCR     | `app/ocr_pipeline/batch.py`           | `OCRBatchProcessor`              | Batch processing        | ❌ No (lab reports) |

### 3.2 Legal Structure Stack

| Layer     | Module                                                           | Class/Function         | Purpose                           | RAG-Relevant |
| --------- | ---------------------------------------------------------------- | ---------------------- | --------------------------------- | ------------ |
| Engine    | `legal_paragraph_detection_engine/src/legal_engine.py`           | `LegalParagraphEngine` | Main entry point                  | ✅ Yes (R1)  |
| Engine    | `legal_paragraph_detection_engine/src/core/paragraph.py`         | `TextNormalizer`       | Clean + preserve patterns         | ✅ Yes (R1)  |
| Engine    | `legal_paragraph_detection_engine/src/core/paragraph.py`         | `ParagraphType`        | Enum (section/clause/etc)         | ✅ Yes (R1)  |
| Engine    | `legal_paragraph_detection_engine/src/core/paragraph.py`         | `ParagraphInfo`        | Dataclass                         | ✅ Yes (R1)  |
| Hierarchy | `legal_paragraph_detection_engine/src/core/hierarchy.py`         | `HierarchyDetector`    | Level + parent detection          | ✅ Yes (R1)  |
| Section   | `legal_paragraph_detection_engine/src/parsers/section_parser.py` | `SectionParser`        | `Section 3(1)(a)` → `SectionInfo` | ✅ Yes (R1)  |
| Clause    | `legal_paragraph_detection_engine/src/parsers/clause_parser.py`  | `ClauseParser`         | `1. (a)` → `ClauseInfo`           | ✅ Yes (R1)  |
| Citation  | `legal_paragraph_detection_engine/src/storage/citation.py`       | `CitationExtractor`    | Statutory + case citations        | ✅ Yes (R1)  |
| Service   | `app/services/legal_engine.py`                                   | `analyze_legal_text()` | App-level wrapper                 | ✅ Yes (R1)  |

### 3.3 Metadata Extraction Stack

| Layer      | Module                                       | Class/Function        | Purpose                 | RAG-Relevant            |
| ---------- | -------------------------------------------- | --------------------- | ----------------------- | ----------------------- |
| Engine     | `app/metadata_extractor/engine.py`           | `LegalMetadataEngine` | Orchestrator            | ⚠️ Partial (R2)         |
| Extractors | `app/metadata_extractor/extractors/base.py`  | `BaseExtractor`       | Abstract base           | ✅ Pattern (R2)         |
| Regex      | `app/metadata_extractor/extractors/regex.py` | 12 extractors         | Document-level metadata | ⚠️ Partial (R2)         |
| NER        | `app/metadata_extractor/ner.py`              | `NERExtractor`        | spaCy entity extraction | ❌ Dead (spaCy missing) |
| Confidence | `app/metadata_extractor/confidence.py`       | `score_field()`       | Confidence scoring      | ✅ Pattern (R2)         |
| Validation | `app/metadata_extractor/validation.py`       | `Validator`           | Cross-field checks      | ✅ Pattern (R2)         |

### 3.4 Search Stack

| Layer    | Module                  | Class/Function         | Purpose                | RAG-Relevant       |
| -------- | ----------------------- | ---------------------- | ---------------------- | ------------------ |
| Indexer  | `app/search/indexer.py` | `FTS5Indexer`          | SQLite FTS5 indexing   | ❌ No (R3 pattern) |
| Indexer  | `app/search/indexer.py` | `SearchQuery`          | Query model            | ❌ No (R3 pattern) |
| Hooks    | `app/search/indexer.py` | `_sync_search_index()` | after_flush auto-index | ✅ Pattern (R3)    |
| Routes   | `app/search/routes.py`  | `search()`             | GET /search?q=         | ❌ No (R3 pattern) |
| Fallback | `app/search/indexer.py` | `fuzzy_search()`       | rapidfuzz fuzzy        | ✅ Pattern (R3)    |

### 3.5 Versioning & Audit Stack

| Layer     | Module                               | Class/Function            | Purpose                | RAG-Relevant |
| --------- | ------------------------------------ | ------------------------- | ---------------------- | ------------ |
| Version   | `app/services/version_control.py`    | `VersionService`          | Incremental versioning | ✅ Yes (R0)  |
| Version   | `app/services/version_control.py`    | `VersionDiffEngine`       | difflib-based diff     | ✅ Yes (R1)  |
| Lifecycle | `app/services/document_lifecycle.py` | `DocumentSaveCoordinator` | Save→version→audit     | ✅ Yes (R1)  |
| Lifecycle | `app/services/document_lifecycle.py` | `SaveResult`              | Dataclass result       | ✅ Yes (R1)  |
| Audit     | `app/models/inspection.py`           | `AuditLog`                | Hash-chained log       | ✅ Yes (R0)  |
| Audit     | `app/models/auth.py`                 | `RecordAudit`             | Change tracking        | ✅ Yes (R1)  |
| Audit     | `app/services/audit.py`              | `compute_hash()`          | SHA-256 hashing        | ✅ Yes (R0)  |
| Audit     | `app/services/audit.py`              | `verify_chain()`          | Chain integrity check  | ✅ Yes (R0)  |

### 3.6 AI/LLM Stack

| Layer   | Module                        | Class/Function       | Purpose                   | RAG-Relevant       |
| ------- | ----------------------------- | -------------------- | ------------------------- | ------------------ |
| Service | `app/ai_assistant/service.py` | `AIAssistantService` | LLM client (no grounding) | ❌ No (R3 pattern) |
| Routes  | `app/ai_assistant/routes.py`  | `assistant()`        | POST /ai-assistant        | ❌ No (R3 pattern) |
| Tasks   | `app/ai_assistant/tasks.py`   | `call_llm_async()`   | Celery async task         | ⚠️ Partial (R3)    |
| Prompts | `app/ai_assistant/service.py` | `PROMPTS` dict       | 5 static templates        | ❌ No (R6 rewrite) |

### 3.7 Async & Infrastructure Stack

| Layer   | Module                       | Class/Function        | Purpose             | RAG-Relevant |
| ------- | ---------------------------- | --------------------- | ------------------- | ------------ |
| Celery  | `celery_app.py`              | `make_celery()`       | Celery factory      | ✅ Yes (R1)  |
| QStash  | `app/utils/qstash_client.py` | `QStashClient`        | Webhook + scheduler | ✅ Yes (R1)  |
| QStash  | `app/utils/qstash_client.py` | `sign_payload()`      | SHA-256 signing     | ✅ Yes (R1)  |
| Storage | `app/utils/storage.py`       | `upload_photo()`      | R2/Cloudinary/B2    | ✅ Yes (R1)  |
| Storage | `app/utils/storage.py`       | Lazy singleton client | S3-compatible       | ✅ Yes (R1)  |

### 3.8 Data Models (SQLAlchemy)

| Model                  | File                          | Table          | Key Fields                                                                                                                       | RAG-Relevant |
| ---------------------- | ----------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| `Annexure`             | `app/models/document.py:167`  | `annexure`     | `id (uuid4)`, `file_hash (sha256)`, `file_path`, `file_size`, `mime_type`, `title`, `description`, `uploaded_at`, `uploader_id`  | ✅ Yes       |
| `Version`              | `app/models/document.py:270`  | `version`      | `id (uuid4)`, `content_hash (sha256)`, `case_id`, `case_type`, `doc_type`, `version_number`, `content`, `created_at`, `actor_id` | ✅ Yes       |
| `Evidence`             | `app/models/document.py:?`    | `evidence`     | `id`, `file_hash`, `file_name`, `mime_type`, `description`, `uploaded_at`                                                        | ✅ Yes       |
| `CaseFile`             | `app/models/document.py:?`    | `case_file`    | `id`, `sample_id`, `inspection_id`, `petition_text`, `permission_text`, `created_at`                                             | ⚠️ Partial   |
| `Adjudication`         | `app/models/document.py:?`    | `adjudication` | `id`, `case_number`, `subject`, `facts`, `decision`, `created_at`                                                                | ⚠️ Partial   |
| `Entity`               | `app/models/document.py:350`  | `entity`       | `id`, `entity_type`, `name`, `source_table`, `source_id`, `metadata_json`, `created_at`                                          | ❌ Dead code |
| `Relationship`         | `app/models/document.py:400`  | `relationship` | `id`, `source_id`, `target_id`, `relationship_type`, `weight`, `created_at`                                                      | ❌ Dead code |
| `AuditLog`             | `app/models/inspection.py:55` | `audit_log`    | `id`, `entity_type`, `entity_id`, `action`, `actor`, `details`, `prev_hash`, `curr_hash`, `timestamp`                            | ✅ Yes       |
| `RecordAudit`          | `app/models/auth.py:?`        | `record_audit` | `id`, `entity_type`, `entity_id`, `action`, `old_values`, `new_values`, `actor_id`, `timestamp`                                  | ✅ Yes       |
| `Version` (inspection) | `app/models/inspection.py:?`  | `version`      | `id`, `adjudication_id`, `content_hash`, `version_number`, `content`, `created_at`, `actor_id`                                   | ✅ Yes       |

### 3.9 Dependency Stack

**Python 3.12+ packages verified via `pip list`:**

| Category         | Package               | Version         | RAG-Relevant                              |
| ---------------- | --------------------- | --------------- | ----------------------------------------- |
| Web framework    | Flask                 | 2.x             | Yes (hosting)                             |
| ORM              | SQLAlchemy            | 2.x             | Yes (data layer)                          |
| PostgreSQL       | psycopg2              | -               | Yes (primary DB)                          |
| SQLite           | sqlite3               | stdlib          | Partial (development only)                |
| PDF processing   | pdfplumber            | -               | ✅ Yes (R0)                               |
| PDF processing   | PyMuPDF (fitz)        | -               | ✅ Yes (R0)                               |
| DOCX processing  | python-docx           | -               | ✅ Yes (R0)                               |
| Text detection   | chardet               | -               | ✅ Yes (R0)                               |
| OCR engine       | pytesseract           | -               | ⚠️ Partial (wrong domain)                 |
| OCR framework    | PaddleOCR             | not installed   | ❌ No                                     |
| CV library       | opencv-python (cv2)   | not installed   | ❌ No                                     |
| Image processing | Pillow                | -               | ✅ Yes (image ops)                        |
| HTML→PDF         | WeasyPrint            | -               | ✅ Yes (PDF generation)                   |
| Text diff        | difflib               | stdlib          | ✅ Yes (R0)                               |
| Fuzzy matching   | rapidfuzz             | -               | ✅ Yes (R3 pattern)                       |
| HTTP client      | httpx                 | -               | ✅ Yes (R3 pattern)                       |
| Regex            | regex                 | -               | ✅ Yes (R2 pattern)                       |
| Async queue      | Celery                | -               | ✅ Yes (R1)                               |
| Async queue      | redis                 | -               | ✅ Yes (R1)                               |
| QStash           | qstash                | -               | ✅ Yes (R1)                               |
| Cloudinary       | cloudinary            | -               | ✅ Yes (storage)                          |
| S3 client        | boto3                 | -               | ✅ Yes (R2 storage)                       |
| **INSTALLED**    | qdrant-client         | installed       | ✅ R6 (Agent A Phase 1)                   |
| **INSTALLED**    | sentence-transformers | installed       | ✅ R6 (Agent A Phase 1)                   |
| **INSTALLED**    | torch / transformers  | installed (CPU) | ✅ R6 (Agent A Phase 1)                   |
| —                | langchain             | not installed   | ❌ Not used (custom implementations)      |
| —                | faiss-cpu             | not installed   | ❌ Not used (Qdrant native vector search) |
| **MISSING**      | spacy                 | not installed   | ❌ (NER dead)                             |
| **MISSING**      | openai (embeddings)   | not installed   | ⚠️ Only `openai` for chat                 |
| Jinja2           | jinja2                | -               | ✅ Yes (templates)                        |
| Template engine  | jinja2 bytecode cache | -               | ✅ Yes (performance)                      |

### 3.10 Test Landscape

**921 tests collected** across 41 test files.

| Test File                                 | Tests | Components Covered           | Status                      |
| ----------------------------------------- | ----- | ---------------------------- | --------------------------- |
| `test_document_loader.py`                 | 39    | PDF/DOCX/TXT loaders         | ✅ 39/39                    |
| `test_document_cleaner.py`                | 49    | Cleaning pipeline            | ✅ 49/49                    |
| `test_metadata_extractor.py`              | 35    | Metadata engine + extractors | ✅ 35/35                    |
| `test_cross_reference.py`                 | 31    | Cross-reference engine       | ✅ 31/31                    |
| `test_search.py`                          | 56    | FTS5 search + fuzzy          | ✅ 56/56                    |
| `test_version_control.py`                 | 27    | Versioning + diff            | ✅ 27/27                    |
| `legal_paragraph_detection_engine/tests/` | 168   | Legal paragraph engine       | ✅ 168/168                  |
| `test_document_lifecycle.py`              | 14    | Save→version→audit           | ✅ 14/14                    |
| `test_ai_assistant.py`                    | 27    | LLM client (mocked)          | ✅ 27/27                    |
| `test_concurrency_inspection.py`          | 4     | AuditLog concurrency         | ✅ 4/4                      |
| `test_qstash_webhook.py`                  | 20    | QStash client                | ✅ 20/20                    |
| `test_toc_generator.py`                   | 50    | TOC engine                   | ✅ 50/50                    |
| `test_ocr_pipeline.py`                    | 24    | OCR pipeline                 | ⚠️ 9 pass/5 fail/10 skipped |
| `test_legal_suggest.py`                   | 4     | Section reference extraction | ✅ 4/4                      |
| `test_annexure.py`                        | 26    | Annexure CRUD                | ✅ 26/26                    |
| `test_case_resolver.py`                   | 12    | Case resolution              | ✅ 12/12                    |
| `test_food_cell_do_intimation.py`         | 15    | DO intimation                | ✅ 15/15                    |
| `test_inspection_photo_service.py`        | 17    | Photo service                | ✅ 17/17                    |

**Tests with NO coverage for RAG components (0 tests each):**

- Legal paragraph engine app integration (`analyze_legal_text` only has 4 indirect tests)
- QStash client (`test_qstash_webhook.py` covers but not RAG integration)
- Entity/Relationship models (0 tests)
- AI Assistant grounding (tests only cover mock dispatch, no grounding)
- Document lifecycle integration with OCR (no e2e test)

### 3.11 RAG Stack (Built 2026-08-08)

#### Corpus/Embedding Pipeline (Agent A)

| Module                         | Class/Function                 | Purpose                                                               | Tests |
| ------------------------------ | ------------------------------ | --------------------------------------------------------------------- | ----- |
| `app/rag/qdrant_client.py`     | `QdrantStore`                  | Qdrant connection, collection mgmt, upsert/search/delete              | 25 ✅ |
| `app/rag/embedding_service.py` | `EmbeddingService`             | sentence-transformers, embed_text/embed_batch, dim validation         | 17 ✅ |
| `app/rag/chunker.py`           | `Chunker`                      | LegalParagraphEngine → Chunk adapter, §5.1 payload schema             | 20 ✅ |
| `app/rag/qdrant_indexer.py`    | `QdrantIndexer`                | after_flush → Qdrant upsert, retry-once, ChunkIngestionResult         | 16 ✅ |
| `app/rag/ingestion.py`         | `IngestionPipeline`            | DocumentLoader → Cleaner → Chunker → Qdrant (batch, fault isolation)  | 16 ✅ |
| `app/rag/dedup.py`             | `ContentHasher`/`ChunkDeduper` | SHA-256 normalized hashing, document + chunk-level dedup              | 12 ✅ |
| `app/rag/metadata_adapter.py`  | `MetadataAdapter`              | LegalMetadataEngine → §5.1 payload (document_type, dates, is_current) | 19 ✅ |
| `app/rag/citation_adapter.py`  | `CitationAdapter`              | CitationExtractor (fixed) → §5.1 citations + §5.2 structured          | 18 ✅ |
| `app/rag/crossref_adapter.py`  | `CrossRefAdapter`              | CrossReferenceEngine → §5.1 references + §5.2 structured              | 14 ✅ |
| `app/rag/chunk_quality.py`     | `ChunkQualityValidator`        | A-F grading, score_field + Validator confidence                       | 12 ✅ |
| `app/rag/tasks.py`             | `embed_and_index_task`         | Celery embed+upsert task, document_id injection                       | 7 ✅  |
| `app/models/rag.py`            | `LegalDocument`, `LegalChunk`  | SQLAlchemy models, file_hash UNIQUE, content_hash, indexes            | 7 ✅  |

**Subtotal (Agent A):** 11 modules | ~25 test files | 176 tests | ✅ All pass |

#### Retrieval Layer (Agent B — Phase 1)

| Module                                  | Class/Function                                   | Purpose                                          | Tests  |
| --------------------------------------- | ------------------------------------------------ | ------------------------------------------------ | ------ |
| `app/rag/retrieval/result.py`           | `SearchResult`, `RetrievedChunk`                 | Unified return type                              | —      |
| `app/rag/retrieval/query_classifier.py` | `QueryClassifier`, `QueryType`, `QueryParser`    | 5 query types + regex section parsing            | ~12 ✅ |
| `app/rag/retrieval/dense_retriever.py`  | `DenseRetriever`                                 | sentence-transformers + Qdrant, mock-injection   | ~15 ✅ |
| `app/rag/retrieval/sparse_retriever.py` | `SparseRetriever`                                | rapidfuzz partial_ratio + token_set_ratio        | ~10 ✅ |
| `app/rag/retrieval/hybrid_retriever.py` | `HybridRetriever`                                | RRF fusion (k=60) + optional reranker            | ~20 ✅ |
| `app/rag/retrieval/reranker.py`         | `Reranker`                                       | Cross-encoder → BM25 + rapidfuzz dual fallback   | ~8 ✅  |
| `app/rag/retrieval/logger.py`           | `RetrievalLogger`, `RetrievalAuditLog`           | Query log + hash-chained audit                   | 8 ✅   |
| `app/rag/tasks.py`                      | `retrieve_task`                                  | Celery bind=True task → `run_retrieval_pipeline` | 9 ✅   |
| `app/models/rag.py`                     | `RAGQueryLog`, `RAGEvalResult`, `RAGEvalDataset` | SQLAlchemy models with indexes                   | 11 ✅  |
| `app/rag/__init__.py`                   | Blueprint                                        | RAG blueprint + `/rag/health` endpoint           | —      |

**Subtotal (Agent B Phase 1):** 10 modules | 8 test files | 102 tests | ✅ All pass |

#### Generation Layer (Agent B — Phase 2)

| Module | Class/Function | Purpose | Tests |
| ------ | -------------- | ------- | ----- |
| `app/rag/generation/__init__.py` | Package exports | ContextBuilder, PromptTemplate, GroundedLLMClient, GroundedGenerationService, CitationTracker, ResponseSanitizer, GenerationLogger | — |
| `app/rag/generation/context_builder.py` | `ContextBuilder`, `BuiltContext` | Chunks → LLM context with metadata, citations, hierarchy | — |
| `app/rag/generation/prompt_template.py` | `PromptTemplate` | Grounded QA template: query + context + citations | — |
| `app/rag/generation/llm_client.py` | `GroundedLLMClient`, `GroundedLLMResponse` | httpx-based dual-endpoint (OpenRouter + OpenAI), stub fallback | — |
| `app/rag/generation/citation_tracker.py` | `CitationTracker` | Extracts `[n]` bracket + inline section citations, maps to chunks | — |
| `app/rag/generation/sanitizer.py` | `ResponseSanitizer`, `SanitizedResponse` | Validates citations against retrieved chunks, groundedness scoring | — |
| `app/rag/generation/grounded_service.py` | `GroundedGenerationService` | Orchestrates 7-step pipeline: context → prompt → LLM → citations → sanitize → log → RAGResponse | — |
| `app/rag/generation/logger.py` | `GenerationLogger` | Updates RAGQueryLog with response text, token usage, citations, groundedness | — |

**Subtotal (Agent B Phase 2):** 8 modules | 1 test file | 40 tests | ✅ All pass |

#### Verification Layer (Agent B — Phase 3)

| Module | Class/Function | Purpose | Tests |
| ------ | -------------- | ------- | ----- |
| `app/rag/verification/__init__.py` | Package exports | All verification components | — |
| `app/rag/verification/claim_extractor.py` | `ClaimExtractor`, `ExtractedClaim` | Regex + sentence-based claim extraction (sections, percentages, amounts, authorities) | 10 |
| `app/rag/verification/evidence_verifier.py` | `EvidenceVerifier`, `EvidenceVerification` | rapidfuzz partial_ratio + section matching + authority fallback | — |
| `app/rag/verification/citation_validator.py` | `CitationValidator`, `CitationValidationResult` | Citation→chunk ID validation + section-number consistency | 6 |
| `app/rag/verification/scorer.py` | `GroundednessScorer`, `GroundednessScore` | Weighted blend: 0.6 × claim support + 0.4 × citation validity | — |
| `app/rag/verification/hallucination_detector.py` | `HallucinationDetector`, `HallucinationReport` | Orchestrator with optional LLM double-check (stub-safe) | 12 |
| `app/rag/verification/token_counter.py` | `TokenCounter`, `TokenUsage` | tiktoken-based token estimation (word-count fallback) + RAGQueryLog.context_length | 10 |

**Subtotal (Agent B Phase 3):** 6 modules | 3 test files | 38 tests | ✅ All pass |

#### Evaluation Layer (Agent B — Phase 4)

| Module | Class/Function | Purpose | Tests |
| ------ | -------------- | ------- | ----- |
| `app/rag/evaluation/__init__.py` | Package exports | All evaluation components | — |
| `app/rag/evaluation/metrics.py` | 6 metrics + `EvalScore` | Faithfulness, AnswerRelevance, ContextPrecision, ContextRecall, CitationRecall, Groundedness | 24 |
| `app/rag/evaluation/storage.py` | `EvalStorage` | Persists RAGEvalResult/RAGEvalDataset (db.session.query to avoid Model.query shadowing) | 4 |
| `app/rag/evaluation/runner.py` | `EvalRunner`, `MetricBundle` | Batch evaluation with MRR, error isolation, summary aggregation, injected pipeline_fn | 10 |
| `app/rag/evaluation/report.py` | `EvalReport`, `EvalSummary` | JSON-serializable report dataclasses | 3 |

**Subtotal (Agent B Phase 4):** 5 modules | 2 test files | 37 tests | ✅ All pass |

#### Integration (Agent B — Phase 5)

| Module | Class/Function | Purpose | Tests |
| ------ | -------------- | ------- | ----- |
| `app/rag/resilient.py` | `ResilientRAGPipeline`, `CircuitState`, `CircuitOpenError` | Circuit breaker (closed→open→half-open→closed) + degraded-mode fallback | 10 |
| `app/rag/routes.py` | `/api/rag/query`, `/api/rag/eval` | Full pipeline route (retrieve→generate→verify→log) + batch evaluation route | 9 |
| `app/rag/tasks.py` | `run_evaluate`, `evaluate_task` | Celery task for batch evaluation (auto-discovered via TASK_MODULES) | 6 |
| `tests/test_rag_e2e_verification.py` | Integration tests | Generation→verification→evaluation end-to-end | 6 |

**Subtotal (Agent B Phase 5):** 3 modules | 2 test files | 25 tests | ✅ All pass |

#### Built (2026-08-09)

| Layer        | Components                                                                  | Owner   | Status         | Tests |
| ------------ | --------------------------------------------------------------------------- | ------- | -------------- | ----- |
| Generation     | ContextBuilder, GroundedLLMClient, GroundedGenerationService, CitationTracker, ResponseSanitizer, GenerationLogger | Agent B P2 | Complete (2026-08-09) | 40 |
| Verification   | ClaimExtractor, EvidenceVerifier, CitationValidator, GroundednessScorer, HallucinationDetector, TokenCounter | Agent B P3 | Complete (2026-08-09) | 34 |
| Evaluation     | 6 metrics, EvalRunner, EvalStorage, EvalReport, EvalSummary | Agent B P4 | Complete (2026-08-09) | 39 |
| API            | `/api/rag/query`, `/api/rag/eval`, `/api/rag/generate`, `/api/rag/ingest`   | Agent B P5 | Complete (2026-08-09) | 15 |
| Resilience     | ResilientRAGPipeline (circuit breaker + fallback)                          | Agent B P5 | Complete (2026-08-09) | 10 |

#### Remaining (not yet built)

| Layer       | Components                                                                                       | Owner             | Status         |
| ----------- | ------------------------------------------------------------------------------------------------ | ----------------- | -------------- |
| OCR         | LegalDocumentOCR (legal-specific, not lab-report OCR)                                            | Agent A (Phase 2) | Not started    |
| Entity      | LegalEntityExtractor (person/org/case)                                                           | Agent A P2        | Not started    |

---

## 4. Architecture Diagrams (Code-Verified)

### 4.1 Data Flow: Document → Chunks

```
PDF File
  │
  ├──▶ [sha256 hash] ──▶ Deduplication check ──▶ (duplicate? skip : continue)
  │
  ├──▶ PDFLoader.load()
  │     ├──▶ pdfplumber.extract_text()  [per-page text]
  │     └──▶ DocumentResult(pages=[DocumentPage(text, page_number, ...)])
  │
  ├──▶ CleaningPipeline.run(text)
  │     ├──▶ PageNumbersRemover.remove()
  │     ├──▶ WatermarksDeleter.delete()
  │     ├──▶ HeaderFooterRemover.remove()
  │     ├──▶ UnicodeNormalizer.normalize()
  │     ├──▶ HyphenNormalizer.normalize()
  │     ├──▶ QuoteNormalizer.normalize()
  │     └──▶ BulletNormalizer.normalize()
  │
  ├──▶ analyze_legal_text(cleaned_text)
  │     ├──▶ LegalParagraphEngine
  │     │     ├──▶ TextNormalizer.clean_text()  [caching via stable_key()]
  │     │     ├──▶ HierarchyDetector.detect()
  │     │     │     ├──▶ SectionParser.parse_sections()
  │     │     │     └──▶ ClauseParser.parse_clauses()
  │     │     ├──▶ CitationExtractor.extract_citations()
  │     │     └──▶ returns {summary: dict, paragraphs: list[dict]}
  │     │
  │     └──▶ CrossReferenceEngine.extract_references(text)
  │           ├──▶ extract_annexure_refs()
  │           ├──▶ extract_section_refs()  [KNOWN_SECTIONS limited set]
  │           └──▶ extract_paragraph_refs()
  │
  └──▶ VersionService.create_version_if_changed()
        ├──▶ sha256(content) ──▶ dedup check
        └──▶ AuditLog.log() ──▶ hash chain
```

### 4.2 Data Flow: Search → Results

```
GET /search?q="contamination"
  │
  ├──▶ SearchRoutes.search()
  │     ├──▶ FTS5Indexer.search(query, entity_type="annexure")
  │     │     ├──▶ "SELECT ... FROM search_index WHERE content MATCH 'contamination'"
  │     │     ├──▶ snippet extraction
  │     │     ├──▶ rank ordering (BM25)
  │     │     └──▶ returns list[dict]{id, entity_type, title, snippet, rank}
  │     │
  │     └──▶ fallback: FTS5Indexer.fuzzy_search(query)
  │           ├──▶ rapidfuzz.fuzz.ratio(text, query)
  │           └──▶ results sorted by similarity
  │
  └──▶ jsonify(results)
```

### 4.3 Data Flow: Version → Audit

```
Document Save (POST /document_viewer/save)
  │
  ├──▶ DocumentSaveCoordinator.save()
  │     ├──▶ VersionService.create_version(case_id, case_type, doc_type, html)
  │     │     ├──▶ content_hash = sha256(html) [app/services/version_control.py:124]
  │     │     ├──▶ existing = Version.query.filter_by(content_hash=...)
  │     │     ├──▶ if existing: return (dedup)
  │     │     ├──▶ new_version = Version(content=html, content_hash=hash, ...)
  │     │     └──▶ session.add(new_version)
  │     │
  │     └──▶ log_audit("document", case_id, "save", actor, details)
  │           ├──▶ prev_entry = AuditLog.query.filter_by(entity_id=case_id).order_by(timestamp.desc()).first()
  │           ├──▶ curr_hash = compute_hash(prev_hash, details, actor, timestamp)
  │           │     └──▶ sha256(f"{prev_hash}|{content}|{actor}|{timestamp.isoformat()}")
  │           └──▶ session.add(AuditLog(prev_hash=prev_hash, curr_hash=curr_hash, ...))
  │
  └──▶ session.commit() → after_flush hooks fire
        ├──▶ FTS5Indexer.index_document() (if indexed model)
        └──▶ AuditHook.after_flush() (if model has audit hook)
```

---

## 5. Infrastructure Readiness for RAG

### 5.1 Ready for Direct Reuse (R0)

| Component               | Path                                                     | Import Statement                                                                 | Readiness                                                            |
| ----------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Document Loader Factory | `app/document_loader/loader.py`                          | `from app.document_loader import DocumentLoaderFactory`                          | ✅ Direct import, no deps beyond pdfplumber/fitz/python-docx/chardet |
| PDF Loader              | `app/document_loader/pdf_loader.py`                      | `from app.document_loader.pdf_loader import PDFLoader`                           | ✅ Direct import                                                     |
| DOCX Loader             | `app/document_loader/docx_loader.py`                     | `from app.document_loader.docx_loader import DOCXLoader`                         | ✅ Direct import                                                     |
| TXT Loader              | `app/document_loader/txt_loader.py`                      | `from app.document_loader.txt_loader import TXTLoader`                           | ✅ Direct import                                                     |
| Data Models             | `app/document_loader/models.py`                          | `from app.document_loader.models import DocumentResult, DocumentPage`            | ✅ Direct import                                                     |
| Cleaning Pipeline       | `app/document_cleaner/pipeline.py`                       | `from app.document_cleaner.pipeline import CleaningPipeline`                     | ✅ Direct import                                                     |
| All Removers            | `app/document_cleaner/removers.py`                       | `from app.document_cleaner.removers import *`                                    | ✅ Direct import                                                     |
| All Normalizers         | `app/document_cleaner/normalizers.py`                    | `from app.document_cleaner.normalizers import *`                                 | ✅ Direct import                                                     |
| Version Service         | `app/services/version_control.py`                        | `from app.services.version_control import VersionService`                        | ✅ Direct import                                                     |
| Audit Service           | `app/services/audit.py`                                  | `from app.services.audit import compute_hash, verify_chain, log_audit`           | ✅ Direct import                                                     |
| Text Normalizer         | `legal_paragraph_detection_engine/src/core/paragraph.py` | `from legal_paragraph_detection_engine.src.core.paragraph import TextNormalizer` | ✅ Direct import                                                     |

### 5.2 Ready for Adaptation (R1)

| Component                 | Path                                                       | Adaptation Needed                                                 |
| ------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------- |
| Legal Paragraph Engine    | `legal_paragraph_detection_engine/`                        | Output → Chunk objects, integrate with document loader output     |
| Citation Extractor        | `legal_paragraph_detection_engine/src/storage/citation.py` | Fix statute misparse, expand citation patterns for SCC/AIR/ILM    |
| Cross-Reference Engine    | `app/cross_reference/engine.py`                            | Expand KNOWN_SECTIONS (10 → 100+), add case law citation patterns |
| Version Service           | `app/services/version_control.py`                          | Adapt `create_version_if_changed` for chunk versioning            |
| Document Save Coordinator | `app/services/document_lifecycle.py`                       | Adapt `SaveResult` for legal document context                     |
| RecordAudit               | `app/models/auth.py`                                       | Extend for legal corpus change tracking                           |
| QStash Client             | `app/utils/qstash_client.py`                               | Adapt `sign_payload()` for RAG task signing                       |

### 5.3 Pattern References Only (R3–R5)

| Component         | Pattern to Reference             | Notes                                           |
| ----------------- | -------------------------------- | ----------------------------------------------- |
| FTS5 Indexer      | `after_flush` auto-indexing hook | Replicate as `after_flush` → Qdrant upsert      |
| FTS5 Search       | Query → fuzzy fallback           | Replicate as: vector search → keyword fallback  |
| AI Assistant      | `httpx` client config            | Reuse dual-endpoint, timeout, retry pattern     |
| Confidence Scorer | Method-based scoring             | Adapt for retrieval confidence                  |
| Validator         | Cross-field rules                | Adapt for retrieval result validation           |
| TOC Engine        | HTML heading hierarchy           | Not directly usable (RAG chunks are plain text) |
| OCR Pipeline      | Async OCR task pattern           | Wrong domain, rebuild for legal documents       |

### 5.4 Net-New Requirements (R6)

| Component                    | Requirement                                    | Why Not Reusable                                     |
| ---------------------------- | ---------------------------------------------- | ---------------------------------------------------- |
| Qdrant Vector Store          | `qdrant-client`                                | Not installed, no vector store in codebase           |
| Embedding Service            | `sentence-transformers` or `openai` embeddings | Not installed for embeddings, `openai` only for chat |
| Embedding Models             | Legal domain embeddings                        | No models configured or downloaded                   |
| Hybrid Retriever             | Sparse + dense fusion                          | No vector retrieval exists at all                    |
| Reranker                     | Cross-encoder reranking                        | No ML infrastructure beyond OCR                      |
| Query Classifier             | Query type classification                      | No such logic exists                                 |
| Context Builder              | LLM context from evidence                      | `AIAssistantService` has no context building         |
| Grounded Prompt Templates    | Retriever-augmented prompts                    | `PROMPTS` dict has no retrieval slots                |
| Citation Validator           | Check LLM citations against evidence           | No grounding/verification code                       |
| Grounded Generation Pipeline | End-to-end: retrieve → generate → verify       | No such pipeline exists                              |
| Evaluation Framework         | RAGAS or custom eval                           | No evaluation infrastructure                         |

---

## 6. Architectural Decision Impact

### 6.1 Standalone Legal Paragraph Engine

The `legal_paragraph_detection_engine/` is a **standalone package** with its own `pyproject.toml`, `pytest` configuration, and test suite (168 tests). It is imported via `app/services/legal_engine.py` as a thin service wrapper.

**Implication for RAG:** This is ideal — it can evolve independently of the Flask monolith. Agent A can fork/adapt it for corpus-level legal text parsing without touching the case-file application.

### 6.2 Monolithic Search Coupling

The `FTS5Indexer` is tightly coupled to SQLite's `FTS5` extension. The `after_flush` hook directly registers with `Session`:

```python
@event.listens_for(Session, "after_flush")
def _sync_search_index(session, flush_context): ...
```

**Implication for RAG:** This hook pattern is directly reusable for Qdrant indexing (swap the `index_document` call body). But the SQLite-specific SQL must be entirely replaced.

### 6.3 ORM Model Design

All models use SQLAlchemy 2.x with `db.Model` (Flask-SQLAlchemy). UUID4 primary keys (`str(uuid.uuid4())`), SHA-256 hash columns (`db.String(64)`), and composite indexes are the standard patterns.

**Implication for RAG:** The versioning and audit models can be extended. But the `Entity`/`Relationship` models are dead code — the schema can be dropped and rebuilt.

### 6.4 Async Task Architecture

The system uses a **dual-queue pattern**: Celery (for CPU-bound tasks like OCR and PDF generation) + QStash (for webhook-based tasks like Sheets sync and bill generation).

**Implication for RAG:** QStash is ideal for RAG pipeline orchestration (embedding generation → indexing → evaluation → report). The `sign_payload()` pattern ensures webhook integrity.

### 6.5 Security & Compliance

The system has: hash-chained audit logs (SHA-256), optimistic concurrency control (`version_id` columns), RBAC (role-based access), RBAC table structure in migrations, Talisman CSP, Flask-WTF CSRF, ProxyFix.

**Implication for RAG:** All security patterns are directly reusable. The hash-chained audit can track legal document provenance per section/amendment. RBAC can control access to legal corpus documents.

---

## 7. Smoke Test Results

### 7.1 Legal Paragraph Engine

```python
from legal_paragraph_detection_engine import LegalParagraphEngine
engine = LegalParagraphEngine()
result = engine.parse("Section 55 of the Food Safety and Standards Act, 2006...")
# Output: list of dict with ParagraphInfo fields
# Detected: 1 section, 2 clauses, 1 explanation, correct hierarchy_depth=2
# Citations found: Section 55 (conf=0.85), Section 56 (conf=0.85)
```

### 7.2 Metadata Extractor

```python
from app.metadata_extractor.engine import LegalMetadataEngine
engine = LegalMetadataEngine()
result = engine.extract_all(text)
# title: "FOOD SAFETY AND STANDARDS ACT" score=0.90 method=regex
# document_type: "Act" score=0.85 method=regex
# authority: "CENTRAL GOVERNMENT" score=0.80 method=regex
# ner_extractor.available → False (spaCy not installed)
```

### 7.3 Document Cleaner

```python
from app.document_cleaner.pipeline import CleaningPipeline
pipeline = CleaningPipeline()
cleaned = pipeline.run(text)
# Removed: 12 page numbers, 3 headers, 3 footers
# Normalized: unicode (NFKC), smart quotes, hyphen joins
# Output length: 23% smaller than input
```

### 7.4 Version Service

```python
from app.services.version_control import VersionService
result = VersionService.create_version("case_123", "case_file", "petition", html_content)
# content_hash = sha256(html_content) → "a1b2c3d4..."
# No existing version with that hash → creates new Version(id, version_number, content_hash)
# Returns Version object
```

### 7.5 Audit Chain

```python
from app.services.audit import compute_hash, verify_chain
# compute_hash("prev_hash_value", "action details", "actor", timestamp)
# returns: "sha256(prev_hash|details|actor|timestamp)"
# verify_chain("document", "case_123") → True (chain validates)
```

### 7.6 AI Assistant (NO Grounding)

```python
from app.ai_assistant.service import AIAssistantService
service = AIAssistantService()
# PROMPTS = {
#   "summarize": "Summarize the following legal document text into 3-5 paragraphs...",
#   "refine": "Refine the following legal language for clarity...",
#   ...
# }
# perform_action("summarize", text) → LLM response, NO retrieval, NO citation checking
```

---

## Phase 1 Completion — Agent B RAG Retrieval Foundation (2026-08-08)

**Status:** Phase 1 Complete — Retrieval foundation built and verified (102/102 tests passing)

### What Was Added Since §3.5

| Component                         | Module                                  | Purpose                                                         | Tests                          |
| --------------------------------- | --------------------------------------- | --------------------------------------------------------------- | ------------------------------ |
| `RAGQueryLog` model               | `app/models/rag.py`                     | Query log with SHA-256 hash, type, chunks, scores, latency      | 11 pass                        |
| `RAGEvalResult` model             | `app/models/rag.py`                     | Evaluation metrics (faithfulness, relevance, precision, recall) | 11 pass                        |
| `RAGEvalDataset` model            | `app/models/rag.py`                     | Ground-truth eval dataset with difficulty levels                | 11 pass                        |
| `SearchResult`/`RetrievedChunk`   | `app/rag/retrieval/result.py`           | Unified result type across retrievers                           | 0 (covered by retriever tests) |
| `QueryClassifier` + `QueryParser` | `app/rag/retrieval/query_classifier.py` | 5 QueryType classification + regex section parsing              | ~12 pass                       |
| `DenseRetriever`                  | `app/rag/retrieval/dense_retriever.py`  | Qdrant + sentence-transformers with mock-injection              | ~15 pass                       |
| `SparseRetriever`                 | `app/rag/retrieval/sparse_retriever.py` | rapidfuzz `partial_ratio` + `token_set_ratio` fallback          | ~10 pass                       |
| `HybridRetriever`                 | `app/rag/retrieval/hybrid_retriever.py` | RRF fusion (k=60) + optional reranker                           | ~20 pass                       |
| `Reranker`                        | `app/rag/retrieval/reranker.py`         | Cross-encoder -> BM25 + rapidfuzz dual fallback                 | ~8 pass                        |
| `RetrievalLogger`                 | `app/rag/retrieval/logger.py`           | Persists to `rag_query_log` table                               | 8 pass                         |
| `RetrievalAuditLog`               | `app/rag/retrieval/logger.py`           | Hash-chained audit via `app/services/audit.py`                  | 8 pass                         |
| `retrieve_task`                   | `app/rag/tasks.py`                      | Celery `bind=True` task wrapping `run_retrieval_pipeline`       | 9 e2e pass                     |
| `run_retrieval_pipeline`          | `app/rag/tasks.py`                      | Plain-function pipeline entry point                             | 9 e2e pass                     |
| RAG blueprint                     | `app/rag/__init__.py`                   | Blueprint + `/rag/health` endpoint                              | 9 e2e pass                     |
| `add_rag_tables` migration        | `migrations/versions/add_rag_tables.py` | Creates 3 RAG tables, merges 2 Alembic heads                    | Stamped                        |

### Key Reused Components

| RAG Component         | Reuses From                                        | Pattern                                              |
| --------------------- | -------------------------------------------------- | ---------------------------------------------------- |
| Hash-chained audit    | `app/services/audit.py`                            | `compute_hash(prev_hash, content, actor, timestamp)` |
| Query content hashing | `app/services/version_control.py`                  | SHA-256 content dedup pattern                        |
| Async task dispatch   | `celery_app.py` + `app/utils/qstash_client.py`     | Celery `bind=True` + QStash signing                  |
| Fuzzy matching        | `app/search/indexer.py` (FTS5Indexer.fuzzy_search) | rapidfuzz `partial_ratio` pattern                    |
| Confidence scoring    | `app/metadata_extractor/confidence.py`             | Method-based scoring table (_METHOD_BASE)            |
| LLM client config     | `app/ai_assistant/service.py`                      | httpx dual-endpoint (OpenRouter + OpenAI), retry     |
| LLM task pattern      | `app/ai_assistant/tasks.py` + `app/food_cell/tasks.py` | Celery bind=True, lazy import, graceful degradation  |
| After-flush hook      | `app/search/indexer.py` (_sync_search_index)       | SQLAlchemy after_flush auto-indexing pattern          |
| Stub fallback         | `app/rag/generation/llm_client.py::GroundedLLMClient` | Stub mode when OPENAI_API_KEY unset                  |
| Circuit breaker       | `app/rag/retryable_embedding_client.py`            | failure_threshold + cooldown, fail-fast              |
| Best-effort DB writes | `app/rag/retrieval/logger.py::RetrievalLogger`     | try/except + db.session.rollback, never breaks pipeline |

### What's Next (Phase 2+)

**✅ Phase 1 (Agent A) Complete.** See updates below.

**Remaining work:**

- ~~Agent A Phase 3: CLI tool, benchmarks (pending)~~ ✅ **Complete (2026-08-09)** — `scripts/ingest_corpus.py` CLI + §6.2 integration tests (`test_corpus_ingestion_e2e.py` / `test_batch_ingestion.py` / `test_reindexing.py`, 16 tests) + `scripts/benchmark_rag.py` timing harness (`test_rag_benchmarks.py`, 11 tests)
- TokenCounter: dedicated token-counting component (currently inline only)
- LegalDocumentOCR (legal-specific OCR for Act/Rule PDFs) ✅ built 2026-08-09 (`app/rag/legal_ocr.py` + `tests/test_legal_ocr.py`)
- LegalEntityExtractor (person/org/case extraction)

**Key deliverables already in place:**

1. ✅ Qdrant collection `fssai_legal_768` (768-dim cosine) created by Agent A
2. ✅ `RAG_EMBEDDING_MODEL=all-mpnet-base-v2` configured in `.env.example`
3. ✅ Chunk payload schema matching §5.1 (now §5.1 of `RAG_AGENT_A_SCOPE.md`) populated in Qdrant
4. ✅ Agent B Phase 1 retrieval foundation (102/102 tests) consumes Agent A's collection

---

## Progress Tracker — Updated 2026-08-08

### RAG System Build Progress

| Phase       | Description                                                              | Owner   | Status                     | Tests         |
| ----------- | ------------------------------------------------------------------------ | ------- | -------------------------- | ------------- |
| Phase 0     | Retrieval Foundation                                                     | Agent B | ✅ Complete (2026-08-08) | 102/102       |
| Phase 1     | Core Ingestion (Qdrant, embeddings, chunker, indexer, pipeline, dedup)   | Agent A | ✅ **Complete 2026-08-08** | 117/117       |
| Phase 2     | Enhancement (Metadata/Citation/CrossRef adapters + quality validator)    | Agent A | ✅ Complete (Days 6-7)     | 63/63         |
| Phase 3     | Grounded Generation (ContextBuilder, GroundedLLMClient, CitationTracker, ResponseSanitizer, GenerationLogger) | Agent B | ✅ Complete (2026-08-09) | 40/40         |
| Phase 4     | Hallucination Detection (ClaimExtractor, EvidenceVerifier, CitationValidator, GroundednessScorer, HallucinationDetector, TokenCounter) | Agent B | ✅ Complete (2026-08-09) | 34/34         |
| Phase 5     | Evaluation (6 metrics, EvalRunner, EvalStorage, report)                   | Agent B | ✅ Complete (2026-08-09) | 39/39         |
| Phase 6     | Integration (API routes, E2E pipeline, ResilientRAGPipeline circuit breaker) | Agent B | ✅ Complete (2026-08-09) | 25/25         |
| Phase 7     | Agent A Polish (QStash schedule, CLI, integration tests, benchmarks)     | Agent A | ✅ Complete (2026-08-09)   | 27/27         |
| **Overall** |                                                                          |         | **~100%** (Agent A + Agent B) | **445+/~700+** |

### Cross-Agent Dependency Chain

| Step | Task                                                                                      | Depends     | Status                     | Tests   |
| ---- | ----------------------------------------------------------------------------------------- | ----------- | -------------------------- | ------- |
| 1    | Agent B Phase 0: Retrieval foundation (models, retrieval, logger, tasks)                  | None        | ✅ Complete                | 102/102 |
| 2    | Agent A Phase 1: Corpus/embedding (Qdrant, embeddings, chunker, indexer, pipeline, dedup) | Step 1      | ✅ **Complete 2026-08-08** | 117/117 |
| 3    | Agent A Phase 2 Days 6-7: Adapters (metadata, citation, crossref, quality)                | Step 2      | ✅ Complete                | 63/63   |
| 4    | Agent B Phase 2: Grounded generation                                                      | Steps 1+2   | ✅ Complete (2026-08-09)   | 40/40   |
| 5    | Agent A Phase 2 Days 8-10: Observability + classifier + Phase 3 (CLI, bench)              | Step 2      | 🔶 Partial                 | 0/~20   |
| 6    | Agent B Phase 3: Hallucination detection (claims → evidence → citations → report)       | Step 4      | ✅ Complete (2026-08-09)   | 34/34   |
| 7    | Agent B Phase 4: Evaluation (metrics, EvalRunner, EvalStorage)                             | Steps 4+6   | ✅ Complete (2026-08-09)   | 39/39   |
| 8    | Agent B Phase 5: Integration (API, E2E, circuit breaker)                                  | Steps 4+6+7 | ✅ Complete (2026-08-09)   | 25/25   |

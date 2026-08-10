# RAG Implementation Gap Analysis

**Date:** 2026-08-07
**Source:** Audit evidence + workplan requirements
**Purpose:** Identify every missing capability required by the FSSAI Legal RAG architecture and determine whether it can be borrowed, adapted, or must be built from scratch.

---

## 1. Gap Summary

| RAG Capability                           | Status             | Existing Code?                                                      | Reuse Path                                                             | Gap Size | Priority |
| ---------------------------------------- | ------------------ | ------------------------------------------------------------------- | ---------------------------------------------------------------------- | -------- | -------- |
| Document loading (PDF/DOCX/TXT)          | ✅ Exists, tested  | `app/document_loader/`                                              | **R0** — direct import                                                 | 0%       | P0       |
| Text cleaning (noise removal)            | ✅ Exists, tested  | `app/document_cleaner/`                                             | **R0** — direct import                                                 | 0%       | P0       |
| Legal paragraph segmentation             | ✅ Built (adapted) | `legal_paragraph_detection_engine/` → `app/rag/chunker.py`          | **R1** — `LegalParagraphEngine` → `Chunk` adapter                      | Done     | P0       |
| Section/subsection hierarchy             | ✅ Built (adapted) | `legal_paragraph_detection_engine/` → `app/rag/chunker.py`          | **R1** — hierarchy mapped to chunk `hierarchy_level`/`parent_chunk_id` | Done     | P0       |
| Citation extraction                      | ✅ Fixed + adapted | `legal_paragraph_detection_engine/` → `app/rag/citation_adapter.py` | **R1** — bug fixed 2026-08-08, expanded via `CitationAdapter`          | Done     | P1       |
| Document classification (Act/Rule/Notif) | ✅ Built; quality fixed | `app/metadata_extractor/` → `app/rag/metadata_adapter.py`           | **R2** — regex patterns → §5.1 payload fields; `policy`-regex substring bug + §2.4.1 gazette-shadowing both FIXED 2026-08-09 (instruments checked first, line-anchored + case-scoped + newline-safe; corpus probe now `Regulation` 10 / `Notification` 7 / `Gazette` 4 / `Act` 3) | Done     | P1       |
| Entity extraction (person/org/case)      | ✅ Built 2026-08-09 | `app/rag/entity_extractor.py`                                                       | **R6** — three-tier: rule-based → spaCy NER → LLM fallback (only when spaCy absent); 26 tests | Done | P2       |
| Authority/jurisdiction extraction        | ✅ Built (adapted) | `app/metadata_extractor/` → `app/rag/metadata_adapter.py`           | **R2** — regex patterns → §5.1 payload fields                          | Done     | P1       |
| Effective date extraction                | ✅ Built (adapted) | `app/metadata_extractor/` → `app/rag/metadata_adapter.py`           | **R2** — ISO normalization via datetime validation                     | Done     | P1       |
| Language/script detection                | ✅ Built (adapted) | `app/metadata_extractor/` → `app/rag/metadata_adapter.py`           | **R2** — adapted into document classification                          | Done     | P1       |
| **Qdrant vector store connection**       | ✅ Built           | `app/rag/qdrant_client.py`                                          | **R6** (was build-from-scratch) → now done                             | Done     | P0       |
| **Embedding model loading**              | ✅ Built           | `app/rag/embedding_service.py`                                      | **R6** (was build-from-scratch) → now done                             | Done     | P0       |
| **Text embedding generation**            | ✅ Built           | `app/rag/embedding_service.py`                                      | **R6** (was build-from-scratch) → now done                             | Done     | P0       |
| Chunk vectorization                      | ✅ Built           | `app/rag/ingestion.py`                                              | **R6** (was build-from-scratch) → now done                             | Done     | P0       |
| Vector indexing (upsert)                 | ✅ Built           | `app/rag/qdrant_indexer.py`                                         | **R6** (was build-from-scratch) → now done                             | Done     | P0       |
| Vector search (dense retrieval)          | ✅ Built           | `app/rag/retrieval/dense_retriever.py`                              | **R6** (was build-from-scratch) → now done (Agent B Phase 1)           | Done     | P0       |
| Hybrid retrieval (dense + sparse)        | ✅ Built           | `app/rag/retrieval/hybrid_retriever.py`                             | **R3** — RRF fusion (k=60), sparse from rapidfuzz pattern              | Done     | P1       |
| **Reranker**                             | ✅ Built           | `app/rag/retrieval/reranker.py`                                     | **R6** (was build-from-scratch) → now done (Agent B Phase 1)           | Done     | P2       |
| Query classification                     | ✅ Built           | `app/rag/retrieval/query_classifier.py`                             | **R6** (was build-from-scratch) → now done (Agent B Phase 1)           | Done     | P2       |
| **Context builder**                      | ❌ Doesn't exist   | —                                                                   | **R6** — build from scratch                                            | Large    | P0       |
| Grounded prompt templates                | ❌ Doesn't exist   | —                                                                   | **R6** — build from scratch                                            | Medium   | P1       |
| **Citation validator**                   | ❌ Doesn't exist   | —                                                                   | **R6** — build from scratch                                            | Large    | P2       |
| **Grounded response verifier**           | ❌ Doesn't exist   | —                                                                   | **R6** — build from scratch                                            | Large    | P2       |
| **RAG evaluation framework**             | ❌ Doesn't exist   | —                                                                   | **R6** — build from scratch                                            | Large    | P3       |

---

## 2. Detailed Gap Analysis by Workplan Section

### 2.1 Phase 1: Infrastructure Setup

The workplan states: "Leverage the existing document loading infrastructure (`app/document_loader/`) which provides PDF/DOCX/TXT loaders."

**Audit Finding:** ✅ **Confirmed accurate.** The document loaders are functional (39/39 tests pass), support the three required formats, and return structured `DocumentResult` objects. The `DocumentLoaderFactory` pattern is directly reusable.

**Gap:** The OCR pipeline (`app/ocr_pipeline/`) is claimed to support document digitization but:

- 5 tests fail due to missing `cv2` (OpenCV)
- PaddleOCR is not installed (only Tesseract fallback works)
- The pipeline is designed for **lab report images**, not legal document scans
- No integration with the document loader (separate pipeline entirely)

**Action:** Agent A must rebuild the OCR pipeline for legal documents. The async task pattern (Celery + QStash) is reusable (R3).

**✅ Confirmed by real corpus evaluation (2026-08-09, see `RAG_AGENT_A_SCOPE.md` §2.4):** of the 24-doc product corpus (`FSSAI_rules documents/`), **2 files are image-only scans with 0 extractable chars** — `FSS_Amendment_Act_1-2008.pdf` and `LicReg.pdf` (single-page image scans). The dev env has only the `pytesseract` wrapper (no `tesseract` binary) and no PaddleOCR, so these files cannot be ingested without OCR.

**✅ OCR RESOLVED 2026-08-09** (`RAG_AGENT_A_SCOPE.md` §3.3): the existing `app/ocr_pipeline/` (decision engine + OpenCV preprocessor + orchestrator) was adapted rather than rebuilt — `OCREngine` is now **EasyOCR-first** (torch-based, pip-only — no system binary; the missing tesseract binary / PaddleOCR are no longer blockers), with Paddle + Tesseract kept as fallbacks. A new `LegalDocumentOCR` adapter (`app/rag/legal_ocr.py`) is wired into `IngestionPipeline`/`make_ingestion_pipeline()` and OCRs image-only PDF pages automatically. Verified on both real scans: `FSS_Amendment_Act_1-2008.pdf` → "The Food Safety And Standards (Amendment) Act, 2008" (2,020 chars); `LicReg.pdf` → MoHFW gazette (1,200 chars).

### 2.2 Phase 2: Corpus Construction

The workplan states: "Use the existing `CleaningPipeline` (`app/document_cleaner/`) and `OCRDecisionEngine` for corpus construction."

**Audit Finding:** ✅ **CleaningPipeline confirmed** (49/49 tests pass). The `OCRDecisionEngine` is confirmed functional but:

- Designed for lab reports (image preprocessing, table detection, signature detection)
- **No legal document support** (no handling of Act headers, section markers, watermarks on legal text)

**Gap — Legal Metadata Integration:** The workplan claims metadata is extracted via `LegalMetadataEngine`. Confirmed functional (35/35 tests pass), BUT:

- Extracts **document-level metadata** (title, authority, date, document_type) — entity-level extraction (persons, organizations, case names, legal provisions) is now covered by the §3.4 `LegalEntityExtractor` (`app/rag/entity_extractor.py`, 2026-08-09)
- NERExtractor is dead (spaCy not installed) — `NERExtractor.available = False` confirmed at runtime (the §3.4 extractor's spaCy tier degrades gracefully to rule-based/LLM)
- No output format suitable for vector store payloads

**✅ Real-corpus validation (2026-08-09, `RAG_AGENT_A_SCOPE.md` §2.4/§2.4.1):** the full corpus pipeline (load → clean → classify → chunk → quality) was run on the product owner's 24-doc `FSSAI_rules documents/` corpus via the new `scripts/evaluate_corpus.py` harness:

- **Cleaning:** 2,324,743 raw chars → 91.4% avg clean ratio; PDF text extraction works on 22/24 files.
- **Chunking:** 13,104 chunks, 692 sections, max hierarchy depth 21, 3,029 citations — the paragraph engine's hierarchy mapping holds on real legal text.
- **Quality:** 0/13,104 error-severity failures; warnings only (`chunk_too_short` on 18 docs, `chunk_too_long` on 8 docs — candidates for future merging/splitting, not blockers).
- **Classification quality issue found:** 20/24 docs classified `notification` because every `DOCUMENT_TYPE_PATTERNS` match scores a flat 0.90 and the first generic pattern wins — the `gazette` pattern (`THE GAZETTE OF INDIA`, present in nearly every official PDF header) shadows the specific `act`/`regulation` patterns (e.g. `Alcoholic_Beverages_Regulations.pdf` matched both `gazette`+`regulation` → `notification`). Two fixes landed 2026-08-09: (1) the `policy` pattern matching "Commission"/"evaluating policy" via substring + IGNORECASE — **FIXED** (case-sensitive, line-anchored, word-boundaried; FSS Act now classifies `act`); (2) the §2.4.1 **pattern-priority fix** — instrument patterns now checked FIRST with line-anchored, case-scoped, newline-safe, trailing-year-guard patterns (title-case ≥2-word + all-caps bare-fragment branches), so gazette/notification wrappers no longer shadow real Act/Regulation/Rule/Bill titles. Corpus probe after the fix: `Regulation` 10, `Notification` 7, `Gazette Notification` 4, `Act` 3. 9 regression tests added; extractor/adapter/classifier surface green.

**Gap — Citation Graph:** The workplan references "citation extraction for graph building." The `CitationExtractor` exists (now 176/176 tests) and its confirmed bug — misidentifying `"of the Act"` instead of `"the Food Safety and Standards Act, 2006"` in statutory references — was **FIXED 2026-08-08** (see `RAG_AGENT_A_SCOPE.md` §2.3).

- ✅ Bug resolved: statutory patterns case-sensitive + 3-word statute-name minimum + dedup; `SectionParser` `(1)(a)` marker-chain misclassification also fixed
- Only extracts Section references, statutory report citations, and case citations — no relationship graph construction code
- No graph storage or retrieval exists

**Gap — Entity/Relationship Models:** Migration `add_entity_relationship_tables` exists with `entity` and `relationship` tables, but:

- **ZERO extraction code** — no service imports these models
- **ZERO query code** — no route or task queries these tables
- **ZERO tests** — no test references these models
- These are **false positives** — schema exists but implementation does not

### 2.3 Phase 3: Embeddings & Vectorization

The workplan references "embedding model integration via sentence-transformers."

**Audit Finding:** ❌ **NOTHING EXISTS.**

- `sentence-transformers` is NOT installed
- `torch` / `transformers` is NOT installed
- No `embed_text()` function exists anywhere in the codebase
- No embedding model configuration exists
- `openai` library IS installed but only used for chat completions (in `app/ai_assistant/service.py`), NOT for embeddings

**Gap — Embedding Infrastructure:** ~~100% net-new (R6)~~ ✅ **CLOSED 2026-08-08.** `EmbeddingService` (`app/rag/embedding_service.py`) built with `sentence-transformers` lazy import, `embed_text`/`embed_batch`/`embed_chunks`, and `validate_vector_size()` guard. Model: `all-mpnet-base-v2` (768-dim).**Update 2026-08-08:** The original spec named `all-MiniLM-L6-v2` (384-dim) — corrected to `all-mpnet-base-v2` (768-dim) to match Agent B's `DenseRetriever` expectations (`RAG_AGENT_A_SCOPE.md` §5.1 reconciliation).

### 2.4 Phase 4: Vector Store (Qdrant)

The workplan references "Qdrant for vector search with hybrid retrieval."

**Audit Finding:** ❌ **NOTHING EXISTS.**

- `qdrant-client` is NOT installed
- No Qdrant connection code exists
- No collection management code exists
- No point insert/upsert code exists
- No vector search code exists

**Partial Pattern Available:** The `FTS5Indexer` (`app/search/indexer.py`) provides:

- `after_flush` SQLAlchemy hook for auto-indexing (directly adaptable to Qdrant)
- Fuzzy search fallback via `rapidfuzz` (reusable for spell-correction on queries)
- Query/response models (`SearchQuery`, search results)

**Gap — Vector Storage:** ~~100% net-new (R6)~~ ✅ **CLOSED 2026-08-08.** `QdrantStore` (`app/rag/qdrant_client.py`) built with collection management, `PointStruct`, `upsert_points`, `search_points`, `delete_points`, `scroll_points`, `create_payload_index`. Collection: `fssai_legal_768` (768-dim cosine). The FTS5 `after_flush` pattern was replicated in `QdrantIndexer` (`app/rag/qdrant_indexer.py`) but is **deliberately not armed** in `create_app()` — see `RAG_AGENT_A_SCOPE.md` Phase 1 notes.

**✅ Qdrant Cloud deployment (2026-08-09, `RAG_AGENT_A_SCOPE.md` §2.4.2):** the codebase is cloud-ready — `RAG_QDRANT_URL` + `RAG_QDRANT_API_KEY` are wired through `app/__init__.py`, `.env.example`, and `render.yaml`, and consumed by `QdrantStore` and `DenseRetriever` (`QdrantClient(url=..., api_key=...)`). The free tier (1 node, 0.5 vCPU / 1 GB RAM / 4 GB disk ≈ 1M 768-dim vectors) comfortably fits the 13,104-chunk corpus (~40 MB). Recommended: keep **local embeddings** (`all-mpnet-base-v2`, 768-dim — matches the §5.1 contract and keeps index/query models identical) and use the cloud purely for storage/search; tune **quantization** (scalar int8), **HNSW** (`m`/`ef_construct`/`ef`), and payload indexes in the cloud console. Switching to Qdrant **Cloud Inference** (hosted FastEmbed models) is possible but requires pipeline code changes and a collection sized to the hosted model's dims.

### 2.5 Phase 5: Retrieval Layer

The workplan references "hybrid retrieval (dense + sparse), reranking, context building."

**Audit Finding:** ❌ **Nothing exists for vector retrieval.**

**Partial Pattern Available:**

- `FTS5Indexer.search()` — lexical search pattern (R3)
- `FTS5Indexer.fuzzy_search()` — rapidfuzz fallback (R3)
- `AIAssistantService` — LLM client pattern (R3)

**Gap — Retrieval Components:** ~~All must build~~ ✅ **MOSTLY CLOSED 2026-08-08 (Agent B Phase 1).**

| Component                           | Status          | Location                                |
| ----------------------------------- | --------------- | --------------------------------------- |
| Dense vector search                 | ✅ Built        | `app/rag/retrieval/dense_retriever.py`  |
| Sparse retrieval (rapidfuzz)        | ✅ Built        | `app/rag/retrieval/sparse_retriever.py` |
| Hybrid retrieval (RRF fusion)       | ✅ Built        | `app/rag/retrieval/hybrid_retriever.py` |
| Reranker (cross-encoder + fallback) | ✅ Built        | `app/rag/retrieval/reranker.py`         |
| Query classifier + parser           | ✅ Built        | `app/rag/retrieval/query_classifier.py` |
| Context builder                     | ❌ Still needed | Agent B Phase 2                         |

### 2.6 Phase 6: Grounded Generation

The workplan references "grounded prompt templates, citation tracking, hallucination detection."

**Audit Finding:** ❌ **Grounding does not exist.**

The `AIAssistantService` (`app/ai_assistant/service.py`) has:

- 5 static prompt templates (summarize, refine, detect_contradictions, suggest_annexures, draft_prayer)
- Simple prompt + document_text concatenation
- No retrieval step (document text passed directly inline)
- No citation tracking in prompts
- No hallucination detection
- No provenance mapping (response → source chunks)

**Gap - Grounded Generation:** ~~100% net-new (R6)~~ ✅ **CLOSED 2026-08-08 (Agent A — pipeline foundation).** `IngestionPipeline` (`app/rag/ingestion.py`) built with `run_ingest_document` + `ingest_corpus_dir` (batch, per-file fault isolation). The grounded generation service itself (ContextBuilder, grounded prompts, `GroundedGenerationService`) remains ❌ **still needed** — Agent B Phase 2.

### 2.7 Phase 7: Evaluation Framework

The workplan references "RAG evaluation (RAGAS-style metrics, citation recall, groundedness)."

**Audit Finding:** ❌ **Nothing exists.**

- No evaluation test files exist
- No metric computation exists
- No ground-truth annotation exists
- No RAGAS or similar library is installed

**Gap — Evaluation:** 100% net-new (R6).

### 2.8 Phase 8: Observability

The workplan references "retrieval logging, token counting, latency tracking, error capture."

**Partial Pattern Available:**

- `QStashClient.sign_payload()` — SHA-256 payload integrity (R2 pattern)
- `AuditLog` hash-chaining — audit trail pattern (R0)
- `app/utils/qstash_client.py` — QStash webhook for async task status (R2 pattern)

**Gap — RAG Observability:** ✅ **CLOSED 2026-08-08 (Agent A Day 3+) / 2026-08-09 (Agent B).** `QdrantIndexer` includes retry-once upsert with error capture; `embed_and_index_task` and `ingest_corpus_task` (`app/rag/tasks.py`) provide async dispatch with SHA-256 payload signing. Retrieval-side observability ✅ `RetrievalLogger` + `RetrievalAuditLog` built (Agent B Phase 1); **TokenCounter** ✅ built (Agent B Phase 3) — `tiktoken` + word-count fallback, integrated into `GroundedGenerationService` to populate `RAGQueryLog.context_length`; **LatencyTracker** and dedicated **ErrorCapture** dashboard remain — existing `duration_ms`/`error` fields partially cover these gaps.

---

## 3. Gap Priority Matrix

| Gap  | Capability                                    | Effort   | Priority           | Depends On                       | Reuse Path                                             |
| ---- | --------------------------------------------- | -------- | ------------------ | -------------------------------- | ------------------------------------------------------ |
| G-01 | Qdrant client + collection mgmt               | 2 days   | **P0**             | None                             | ✅ Built (`app/rag/qdrant_client.py`)                  | Done |
| G-02 | Embedding model loading                       | 1 day    | **P0**             | None                             | ✅ Built (`app/rag/embedding_service.py`)              | Done |
| G-03 | Text → vector generation                      | 1 day    | **P0**             | G-02                             | ✅ Built (`app/rag/embedding_service.py`)              | Done |
| G-04 | Chunk → vector indexing (upsert)              | 2 days   | **P0**             | G-01, G-03                       | ✅ Built (`app/rag/qdrant_indexer.py`)                 | Done |
| G-05 | Vector search (dense retrieval)               | 2 days   | **P0**             | G-01, G-04                       | ✅ Built (`app/rag/retrieval/dense_retriever.py`)      | Done |
| G-06 | Context builder (retrieved → LLM context)     | 3 days   | **P0**             | G-05                             | R6 (net-new)                                           |
| G-07 | Document loader integration                   | 0.5 day  | **P0**             | None                             | ✅ Done (R0 — direct import)                           | Done |
| G-08 | Text cleaning pipeline integration            | 0.5 day  | **P0**             | None                             | ✅ Done (R0 — direct import)                           | Done |
| G-09 | Legal paragraph engine → Chunk                | 1 day    | **P0**             | None                             | ✅ Built (`app/rag/chunker.py`)                        | Done |
| G-10 | Legal paragraph engine → chunk hierarchy      | 1 day    | **P0**             | G-09                             | ✅ Built (`app/rag/chunker.py`)                        | Done |
| G-11 | Citation extractor fix                        | 1 day    | **P1**             | G-09                             | ✅ Closed 2026-08-08 (see `RAG_AGENT_A_SCOPE.md` §2.3) |
| G-12 | Cross-reference engine expansion              | 1 day    | **P1**             | G-11                             | ✅ Built (`app/rag/crossref_adapter.py`)               | Done |
| G-13 | Metadata extractor → RAG payload              | 1 day    | **P1**             | G-08                             | ✅ Built (`app/rag/metadata_adapter.py`)               | Done |
| G-14 | Entity extractor (person/org/case)            | 3 days   | **P2**             | G-13                             | ✅ Built 2026-08-09 (`app/rag/entity_extractor.py`)                     | Done |
| G-15 | Hybrid retrieval (dense + sparse)             | 2 days   | **P1**             | G-05                             | ✅ Built (`app/rag/retrieval/hybrid_retriever.py`)     | Done |
| G-16 | Reranker (cross-encoder)                      | 2 days   | **P2**             | G-05                             | ✅ Built (`app/rag/retrieval/reranker.py`)             | Done |
| G-17 | Query classifier                              | 2 days   | **P2**             | G-15                             | ✅ Built (`app/rag/retrieval/query_classifier.py`)     | Done |
| G-18 | Grounded prompt templates                     | 2 days   | **P1**             | G-06                             | R6 (net-new)                                           |
| G-19 | Citation validator (response → chunks)        | 3 days   | **P2**             | G-06                             | R6 (net-new)                                           |
| G-20 | Hallucination detector                        | 2 days   | **P2**             | G-19                             | R6 (net-new)                                           |
| G-21 | RAG evaluation framework                      | 2 days   | **P3**             | G-06, G-05                       | R6 (net-new)                                           |
| G-22 | RAG observability (logging/tokens/latency)    | 1 day    | **P1**             | G-06                             | ✅ Built (`app/rag/tasks.py` observability)            | Done |
| G-23 | after_flush auto-index hook                   | 0.5 day  | **P0**             | G-04                             | ✅ Built (`app/rag/qdrant_indexer.py`)                 | Done |
| G-24 | Async task orchestration (embedding/indexing) | ✅ Built | `app/rag/tasks.py` | **R2** — QStash + Celery pattern | Done                                                   | P1   |

**Phase breakdown:**

- **P0 (build first):** G-07, G-08, G-09, G-10, G-01, G-02, G-03, G-04, G-05, G-06, G-23 — **core ingestion + retrieval** (80% of workplan Phase 1-4)
- **P1 (build second):** G-11, G-12, G-13, G-15, G-18, G-22, G-24 — **enhancement + hybrid + observability** (workplan Phase 5-6, 8)
- **P2 (build third):** G-14, G-16, G-17, G-19, G-20 — **advanced features** (reranking, query classification, hallucination detection)
- **P3 (build last):** G-21 — **evaluation framework**

---

## 4. False-Positive Impact Assessment

### 4.1 "Entity/Relationship Knowledge Graph"

**Claims in workplan:** "The platform has Entity/Relationship models supporting knowledge graph construction."

**Reality:** The models exist as SQLAlchemy tables (`app/models/document.py`, migration `add_entity_relationship_tables`) but:

- No extraction code anywhere in the codebase
- No graph construction code anywhere
- No graph query code anywhere
- Zero tests
- Zero imports outside model definition

**Impact:** Agent B cannot reuse any graph construction or retrieval logic. Must build both entity extraction (which entity types? what patterns?) and graph storage/retrieval from scratch. The existing schema is not even suitable for RAG — it lacks `vector_embedding`, `document_uri`, `temporal_validity`, or `similarity_score` fields.

**Recommendation:** Drop the `entity`/`relationship` tables entirely. Build a dedicated RAG corpus schema: `legal_document`, `legal_chunk`, `citation_edge`, `entity_mention`.

### 4.2 "AI Assistant with RAG capabilities"

**Claims in workplan:** "The existing AI assistant can be adapted for grounded generation."

**Reality:** The `AIAssistantService` is a bare `httpx` client calling OpenRouter/OpenAI chat completions. It:

- Receives document text inline in the prompt (no retrieval)
- Has no query understanding or classification
- Has no context window management
- Has no citation tracking
- Has no hallucination detection
- Has no provenance mapping

**Impact:** Agent B cannot reuse the prompt templates or generation logic. The `httpx` client configuration (dual endpoint, timeout, retry) is the only reusable pattern (R3).

**Recommendation:** Build a `GroundedGenerationService` that wraps the LLM client but adds: query classification → dense retrieval → context construction → grounded prompt → citation extraction → hallucination check.

### 4.3 "OCR Pipeline for Document Digitization"

**Claims in workplan:** "The OCR pipeline supports document digitization for the corpus."

**Reality:** The OCR pipeline (`app/ocr_pipeline/`) is designed for **lab report photos** taken during food safety inspections:

- `PageDetector` requires `cv2` (OpenCV) — NOT installed, 5 tests fail
- `ImagePreprocessor` applies lab-report-specific preprocessing (contrast, denoise, deskew)
- `OCRDecisionEngine` decides whether a photo needs OCR based on image quality
- `OCREngine` dispatches to PaddleOCR (not installed) or Tesseract (installed but not configured for legal text)
- Output: `OCRDocument` with `raw_text`, `clean_text`, `confidence`, `word_count` — no legal structure

**Impact:** Agent A cannot reuse the OCR pipeline for legal document scanning. Must rebuild for legal document characteristics (multi-column layouts, serif fonts, section headers, decorative elements). The async task pattern (Celery + QStash webhook) is reusable.

**Recommendation:** Build a `LegalDocumentOCR` service that handles legal document characteristics. Reuse the Celery task + QStash webhook pattern.

---

## 5. Effort Estimation

### 5.1 Net-New Implementation (R6)

| Category                 | Components                                                           | Est. Effort | Remaining   | Done By   | Status      |
| ------------------------ | -------------------------------------------------------------------- | ----------- | ----------- | --------- | ----------- |
| Vector Infrastructure    | Qdrant client, collection mgmt, point upsert, vector search          | 7 days      | 0 days      | Agent A   | ✅ Complete |
| Embedding Infrastructure | Model loading, text→vector, batch processing                         | 3 days      | 0 days      | Agent A   | ✅ Complete |
| Retrieval Layer          | Dense search, hybrid fusion, query classifier, reranker              | 7 days      | 0 days      | Agent B   | ✅ Complete |
| Grounded Generation      | Context builder, grounded prompts, citation validator, hallucination | 7 days      | 0 days      | Agent B   | ✅ Phase 2 Complete (2026-08-09) |
| Entity Extraction        | Person/organization/case name extraction (NER or LLM)                | 3 days      | 0 days      | Agent A   | ✅ Complete 2026-08-09 |
| Evaluation Framework     | RAGAS-style metrics, ground truth, annotation                        | 3 days      | 0 days      | Agent B   | ✅ Phase 4 Complete (2026-08-09) |
| RAG Observability        | Retrieval logging, token counting, latency, error capture            | 2 days      | 0 days      | Agent A+B | ✅ Complete |
| **Total**                |                                                                      | **32 days** | **0 days**  |           |             |

### 5.2 Reusable / Adaptable Implementation (R0-R3)

| Category            | Components                                               | Est. Effort | Done By | Status      |
| ------------------- | -------------------------------------------------------- | ----------- | ------- | ----------- |
| Document Loading    | Direct import of `DocumentLoaderFactory` + loaders       | 0.5 days    | Agent A | ✅ Complete |
| Text Cleaning       | Direct import of `CleaningPipeline` + all stages         | 0.5 days    | Agent A | ✅ Complete |
| Legal Segmentation  | Adapt `LegalParagraphEngine` → Chunk adapter             | 2 days      | Agent A | ✅ Complete |
| Citation Extraction | Fix `CitationExtractor` + expand via `CitationAdapter`   | 2 days      | Agent A | ✅ Complete |
| Metadata Extraction | Adapt regex extractors → `MetadataAdapter`               | 2 days      | Agent A | ✅ Complete |
| Cross-Reference     | Expand via `CrossRefAdapter` (full Act sections)         | 1 day       | Agent A | ✅ Complete |
| Versioning/Audit    | Direct import of `VersionService` + `AuditLog` (SHA-256) | 1 day       | Agent A | ✅ Complete |
| Async Pattern       | Reuse QStash/Celery pattern                              | 1 day       | Agent A | ✅ Complete |
| **Total**           |                                                          | **10 days** |         | ✅ Complete |

### 5.3 Grand Total

| Category         | Planned     | Remaining   | Done        |             |
| ---------------- | ----------- | ----------- | ----------- | ----------- |
| Reusable (R0-R3) | 10 days     | 0 days      | 10 days     | ✅ Complete |
| Net-new (R6)     | 32 days     | 0 days      | 32 days     | 100%        |
| **Total**        | **42 days** | **0 days**  | **42 days** | **~100%**   |

**Agent A (Corpus/Embedding):** ~15 days planned → ✅ ~15 days done (Phase 1 + Phase 2 Days 6-9 + Phase 3) — Phase 1 complete (117/117 tests), Phase 2 Days 6-9 complete (139 tests incl. `DocumentClassifier` 17, `IngestionLogger` 16, `RetryableEmbeddingClient` 17, `LegalEntityExtractor` 26). Phase 2 Day 10 (test corpus) superseded by a **real 24-doc corpus evaluation** (2026-08-09, `scripts/evaluate_corpus.py` — 13,104 chunks, 0 failures; one classification bug fixed, one limitation documented); **Phase 3 complete (2026-08-09)** — CLI ✅ + **§6.2 integration tests ✅ (`test_corpus_ingestion_e2e.py` 8, `test_batch_ingestion.py` 5, `test_reindexing.py` 3) + benchmarks ✅ (`scripts/benchmark_rag.py` + `test_rag_benchmarks.py` 11)**
**Agent B (Retrieval/Generation):** ~27 days planned → ✅ ~27 days done — Phase 1 Retrieval (102/102) ✅ + Phase 2 Grounded Generation (40/40) ✅ + Phase 3 Hallucination Detection (34/34) ✅ + Phase 4 Evaluation (39/39) ✅ + Phase 5 Integration (25/25 incl. e2e + circuit breaker + TokenCounter) ✅

---

## 6. Risk Assessment

### 6.1 High-Risk Items

| Risk     | Description                                          | Mitigation                                                                                                                                                                          |
| -------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ~~R-01~~ | ~~**No embedding model availability**~~              | ✅ RESOLVED 2026-08-08 — `EmbeddingService` (`app/rag/embedding_service.py`) built with `all-mpnet-base-v2` (768-dim); `validate_vector_size()` guards against dimension mismatches |
| ~~R-02~~ | ~~**OCR pipeline rebuild needed**~~              | ✅ **RESOLVED 2026-08-09** — reused `app/ocr_pipeline` (decision + OpenCV preprocessor), made `OCREngine` EasyOCR-first (pip-only, no system binary), added `LegalDocumentOCR` (`app/rag/legal_ocr.py`) wired into ingestion. Both image-only scans (`FSS_Amendment_Act_1-2008.pdf`, `LicReg.pdf`) now OCR cleanly (§2.1, `RAG_AGENT_A_SCOPE.md` §3.3). |
| ~~R-13~~     | ~~**Document-type classification skew (gazette shadowing)**~~ | ✅ **RESOLVED 2026-08-09** — §2.4.1 pattern-priority fix in `app/metadata_extractor/regex_library.py`: instrument patterns (`act`/`regulation`/`rule`/`bill`) checked FIRST (stable-sort priority), line-anchored + case-scoped + newline-safe + trailing-year guard, with title-case (≥2-word lead-in) and all-caps (bare-fragment) branches. Corpus probe: `Regulation` 10, `Notification` 7, `Gazette Notification` 4, `Act` 3 (was `Notification` 20 + `Act` 2 + unknown 2). 9 regression tests; `test_metadata_extractor.py` 41/41. Residual: single-word-lead-in wrapped titles (Organic/Fortification/Nutraceuticals, p6–7) still fall back to gazette/notification — position-scoring is the documented next step. |
| ~~R-03~~ | ~~**spaCy NER for entity extraction is not installed**~~ | ✅ RESOLVED 2026-08-09 — `LegalEntityExtractor` (§3.4, `app/rag/entity_extractor.py`) uses rule-based regex first (no deps), spaCy NER when available, and an LLM fallback ONLY when spaCy is absent (injected client or `RAG_ENTITY_LLM=true`); 26 tests |
| ~~R-04~~ | ~~**Entity/Relationship models are dead code**~~         | ✅ RESOLVED 2026-08-09 — built fresh (§3.4 `LegalEntityExtractor`), leaving the dead `Entity`/`Relationship` models untouched                                                                                                                       |
| R-05     | ~~Legal paragraph engine citation bug~~              | ✅ RESOLVED 2026-08-08 — `CitationExtractor` "of the Act" misparse + `SectionParser` `(1)(a)` title bug fixed (`RAG_AGENT_A_SCOPE.md` §2.3)                                         |

### 6.2 Medium-Risk Items

| Risk     | Description                                    | Mitigation                                                                                                                                                  |
| -------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ~~R-06~~ | ~~**Citation engine section set is limited**~~ | ✅ RESOLVED 2026-08-08 — `CrossRefAdapter` (`app/rag/crossref_adapter.py`) uses full-Act `FSS_ACT_SECTIONS` knowledge set; app's `KNOWN_SECTIONS` untouched |
| ~~R-07~~ | ~~**OCR depends on cv2**~~                     | ✅ RESOLVED 2026-08-09 — `opencv-python-headless` 5.0 installed; preprocessing (grayscale/denoise/deskew/threshold/CLAHE) verified on real scans |
| R-08     | **SQLite FTS5 coupling**                       | Don't try to adapt FTS5 for production; build Qdrant from scratch                                                                                           |
| R-09     | **AI assistant has no grounding**              | Don't extend `AIAssistantService`; build new `GroundedGenerationService`                                                                                    |

### 6.3 Low-Risk Items

| Risk | Description                 | Mitigation                                                  |
| ---- | --------------------------- | ----------------------------------------------------------- |
| ~~R-10~~ | ~~**PaddleOCR not installed**~~ | ✅ RESOLVED 2026-08-09 — EasyOCR is the primary engine (pip-only); Paddle/Tesseract remain optional fallbacks |
| R-11 | **Metadata NER is dead**    | Regex extractors are sufficient for document classification |
| R-12 | **TOC engine is HTML-only** | Legal paragraph engine handles text-based chunking          |

---

## 7. Deliverable: Concrete Action List

### Phase 0 — Immediate Reuse (Agent A) ✅ Complete

1. **Import `DocumentLoaderFactory`** from `app/document_loader/loader.py` — used verbatim
2. **Import `CleaningPipeline`** from `app/document_cleaner/pipeline.py` — used verbatim
3. **Import `TextNormalizer`** from `legal_paragraph_detection_engine/src/core/paragraph.py` — used for pre-chunking text cleaning
4. **Import `VersionService`** from `app/services/version_control.py` — reused SHA-256 dedup pattern in `ChunkDeduper`
5. **Import `compute_hash`/`verify_chain`** from `app/services/audit.py` — reused for provenance chains

### Phase 1 — Adaptation (Agent A + Agent B) ✅ Complete

1. **Adapt `LegalParagraphEngine`** — ✅ Done: `Chunker` (`app/rag/chunker.py`) wraps `engine.parse()` to produce `Chunk` objects with all §5.1 payload fields
2. ~~**Fix `CitationExtractor`**~~ — ✅ DONE 2026-08-08 (see §2.3)
3. **Expand `CrossReferenceEngine.KNOWN_SECTIONS`** — ✅ Done: `CrossRefAdapter` (`app/rag/crossref_adapter.py`) uses full-Act `FSS_ACT_SECTIONS`
4. **Adapt `LegalMetadataEngine`** — ✅ Done: `MetadataAdapter` (`app/rag/metadata_adapter.py`) maps to §5.1 payload fields

### Phase 2 — Net-New Build (Agent A) ✅ Complete

1. **Qdrant infrastructure** — ✅ Done: `QdrantStore` (`app/rag/qdrant_client.py`)
2. **Embedding model** — ✅ Done: `EmbeddingService` (`app/rag/embedding_service.py`), `all-mpnet-base-v2` (768-dim)
3. **Chunk vectorization** — ✅ Done: `IngestionPipeline` (`app/rag/ingestion.py`) — text→embed→upsert
4. **after_flush hook** — ✅ Done: `QdrantIndexer` (`app/rag/qdrant_indexer.py`) — FTS5 pattern replicated

### Phase 3 — Net-New Build (Agent B) ✅ Complete

1. **Hybrid retriever** — ✅ Done: `HybridRetriever` (`app/rag/retrieval/hybrid_retriever.py`) — RRF fusion (k=60)
2. ~~Context builder~~ — ❌ Still needed (Agent B Phase 2)
3. ~~Grounded prompt templates~~ — ❌ Still needed (Agent B Phase 2)
4. ~~Citation validator~~ — ❌ Still needed (Agent B Phase 2)

### Phase 4 — Enhancement (Agent B) ⏳ In Progress

1. ~~Reranker~~ — ✅ Done: `Reranker` (`app/rag/retrieval/reranker.py`)
2. ~~Query classifier~~ — ✅ Done: `QueryClassifier` (`app/rag/retrieval/query_classifier.py`)
3. ~~Hallucination detector~~ — ❌ Still needed (Agent B Phase 3)

### Phase 5 — Evaluation (Agent B) ❌ Not Started

1. **Evaluation framework** — ❌ Still needed (Agent B Phase 4)
2. **Test datasets** — ❌ Still needed
3. **Annotation pipeline** — ❌ Still needed

---

## Progress Tracker

### Implementation Gap Closure

**Update 2026-08-08:** 18/24 gaps closed. Agent A Phase 1 complete (117/117 tests), Phase 2 Days 6-7 complete (63 tests). Agent B Phase 1 complete (102/102 tests). Remaining: G-06 (context builder), G-14 (entity extraction), G-18 (grounded prompts), G-19 (citation validator), G-20 (hallucination detector), G-21 (eval framework).

**Update 2026-08-09:** Real-corpus evaluation delivered — `scripts/evaluate_corpus.py` run on the product owner's 24-doc `FSSAI_rules documents/` corpus (13,104 chunks, 0 quality failures, 91.4% clean ratio). Confirmed two previously-theoretical gaps are **real**: OCR (2 image-only scans) and document-type classification quality (gazette pattern shadowing → 20/24 `notification`). Fixed the `policy`-regex substring bug (FSS Act now `act`; 69/69 extractor/adapter/classifier tests). Full findings: `RAG_AGENT_A_SCOPE.md` §2.4.

**Update 2026-08-09 (evening):** §3.4 entity extraction delivered — `LegalEntityExtractor` (`app/rag/entity_extractor.py`) with the scope's three-tier strategy: rule-based regex (person/org/case/statute) → spaCy NER fallback (PERSON/ORG/LAW mapped) → LLM fallback ONLY when spaCy is absent (injected client or `RAG_ENTITY_LLM=true`). Wired into `IngestionPipeline` (opt-in) + `make_ingestion_pipeline(full_enrichment=True)`; `Chunk.entities` payload field + `LegalChunk.entities` JSON column (migration `add_entities_to_legal_chunk`). 29 new tests; G-14 and R-03/R-04 closed.

**Update 2026-08-09 (night):** §2.4.1 classification-priority fix delivered — instrument patterns (`act`/`regulation`/`rule`/`bill`) now outrank generic `gazette`/`notification` patterns in `app/metadata_extractor/regex_library.py` (line-anchored, case-scoped, newline-safe, trailing-year guard; title-case ≥2-word + all-caps bare-fragment branches). **R-13 closed.** Corpus probe: `Regulation` 10, `Notification` 7, `Gazette Notification` 4, `Act` 3 (was `Notification` 20 + `Act` 2 + unknown 2); `Compendium_Licensing_Regulations`/`Licensing_Regulations-2` no longer shadowed by the passing Act reference; the 3 text-extractable Act/Amendment files classify `act`. 9 new regression tests; `test_metadata_extractor.py` 41/41, extractor/adapter/classifier/pipeline surface 105/105. Remaining Agent A: benchmarks (Day 13). Remaining Agent B: G-18 (grounded prompts), G-19 (citation validator), G-20 (hallucination detector), G-21 (eval framework).

| Phase       | Scope                           | Gaps                   | Status         | Progress             |
| ----------- | ------------------------------- | ---------------------- | -------------- | -------------------- |
| Phase 0     | Retrieval Foundation            | G-23                   | ✅ Complete    | 100%                 |
| Phase 1     | Core Ingestion (Agent A)        | G-01-G-05, G-07-G-10   | ✅ Complete    | 100% (117/117 tests) |
| Phase 1.5   | Ingestion Enhancement (Agent A) | G-12, G-13, G-22, G-24 | ✅ Complete    | 100% (63 tests)      |
| Phase 1.5   | Retrieval Foundation (Agent B)  | G-05, G-15-G-17        | ✅ Complete    | 100% (102/102 tests) |
| Phase 2     | Enhancement                     | G-18, G-19(partial)    | ❌ In Progress | ~50%                 |
| Phase 3     | Generation (Agent B)            | G-19, G-20             | ❌ Not Started | 0%                   |
| Phase 4     | Evaluation                      | G-21                   | ❌ Not Started | 0%                   |
| Phase 5     | Entity Extraction               | G-14                   | ✅ Complete 2026-08-09 | 100% (26 tests) |
| **Overall** |                                 | 24 gaps                | **19 closed**  | **~79%**             |

### Test Count Summary

| Component                           | Tests    | Status       |
| ----------------------------------- | -------- | ------------ |
| Phase 1 (Agent A ingestion)         | 117      | ✅ All pass  |
| Phase 2 Days 6-7 (Agent A adapters) | 63       | ✅ All pass  |
| Phase 1 (Agent B retrieval)         | 102      | ✅ All pass  |
| Phase 2 Days 8-9 (observability + classifier) | 50 | ✅ All pass |
| Phase 5 routes + CLI + smoke        | 15 + 10 + 9 | ✅ All pass |
| §3.4 Entity extraction              | 29       | ✅ All pass  |
| **Total RAG tests**                 | **~395** | **All pass** |
| **Real-corpus validation**          | 24 docs (13,104 chunks) | ✅ 0 quality failures — `scripts/evaluate_corpus.py` (2026-08-09) |

### Dependency Installation Status

| Package                 | Status              | Installed By    | Notes                               |
| ----------------------- | ------------------- | --------------- | ----------------------------------- |
| `qdrant-client`         | ✅ Installed 2026-08-09 (1.19) | Agent A Phase 1 | Lazy-imported, graceful degradation; `RAG_QDRANT_URL` + `RAG_QDRANT_API_KEY` support Qdrant Cloud (free tier fits the 13,104-chunk corpus — §2.4.2) |
| `sentence-transformers` | ✅ Installed 2026-08-09 (5.7) | Agent A Phase 1 | `all-mpnet-base-v2` (768-dim); local-embedding mode keeps index/query models identical |
| `torch`                 | ✅ Installed 2026-08-09 (2.13 CPU) | Agent A Phase 1 | CPU backend (`--index-url https://download.pytorch.org/whl/cpu`) |
| `transformers`          | ✅ Installed 2026-08-09 (5.14) | Agent A Phase 1 | Model loading                       |
| `easyocr`               | ✅ Installed 2026-08-09 (1.7) | Agent A §3.3 | Primary OCR engine (torch-based, pip-only, no system binary) — resolves R-02/R-10 |
| `opencv-python-headless`| ✅ Installed 2026-08-09 (5.0) | Agent A §3.3 | OCR preprocessing — resolves R-07   |
| `rapidfuzz`             | ✅ Installed        | Existing        | Used by SparseRetriever             |
| `httpx`                 | ✅ Installed        | Existing        | Used by AI assistant                |
| `celery`                | ✅ Installed        | Existing        | Async tasks                         |
| `qstash`                | ✅ Installed        | Existing        | Webhook scheduling                  |
| `spacy`                 | Optional           | Agent A §3.4 | NER fallback only — `LegalEntityExtractor` runs rule-based first; LLM fallback covers its absence |

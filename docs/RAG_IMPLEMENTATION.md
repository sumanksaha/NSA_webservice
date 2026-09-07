# RAG Implementation Reference — NSA Webservice

> **Scope:** A single, self-contained reference for the Retrieval-Augmented Generation (RAG) system embedded in the NSA Webservice Flask monolith. This document is faithful to the source code as of commit `2b98a82` (2026-08-26). It covers ingestion through retrieval, generation, verification, evaluation, the LangGraph agent pipeline (M3–M5), Knowledge Graph integration, remote inference, resilience, configuration, data models, task orchestration, and test coverage.
>
> **Authoritative source:** The actual source files under `app/rag/`, `kg/`, `app/models/rag.py`, `app/shared/config.py`, and `app/rag/tasks.py`. AGENTS.md is a high-level status snapshot only and may have drifted (e.g. `app/rag/routes.py` is now 71 lines of FastAPI `APIRouter`, not the legacy Flask blueprint).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Phase 1 — Ingestion & Corpus Pipeline](#2-phase-1--ingestion--corpus-pipeline)
3. [Phase 1 — Retrieval Pipeline](#3-phase-1--retrieval-pipeline)
4. [Phase 2 — Grounded Generation](#4-phase-2--grounded-generation)
5. [Phase 3 — Verification & Hallucination Detection](#5-phase-3--verification--hallucination-detection)
6. [Phase 4 — Evaluation Framework](#6-phase-4--evaluation-framework)
7. [Phase 5 — Resilience & Integration](#7-phase-5--resilience--integration)
8. [LangGraph Agent Pipeline (M3–M5)](#8-langgraph-agent-pipeline-m3--m5)
9. [Knowledge Graph Integration](#9-knowledge-graph-integration)
10. [Remote Inference Layer](#10-remote-inference-layer)
11. [Configuration & Feature Flags](#11-configuration--feature-flags)
12. [Data Model](#12-data-model)
13. [Task Orchestration](#13-task-orchestration)
14. [Corpus Inventory](#14-corpus-inventory)
15. [Test Coverage Inventory](#15-test-coverage-inventory)
16. [Infrastructure & Deployment](#16-infrastructure--deployment)
17. [Known Issues & Caveats](#17-known-issues--caveats)

---

## 1. Architecture Overview

### 1.1 High-Level Diagram

```
                    ┌─────────────────────────────────────────────┐
                    │              Query Entry Points              │
                    │  POST /api/rag/query       (legacy pipeline) │
                    │  POST /api/rag/query/agent (LangGraph M3–M5)  │
                    │  POST /api/v2/rag/query    (FastAPI gateway)  │
                    └──────────┬────────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  run_retrieval_      │   (Phase 1)
                    │  pipeline()          │
                    │  QueryClassifier →   │
                    │  HybridRetriever →   │
                    │  EnsembleReranker →  │
                    │  RetrievalLogger     │
                    └──────────┬──────────┘
                               │ chunks
                    ┌──────────▼─────────────────────────────┐
                    │  run_generation_pipeline()              │  (Phase 2–5)
                    │   1. Retrieval (if chunks=None)        │
                    │   2. KG fusion/expansion (RAG_KG_*)    │
                    │   3. GroundedGenerationService.generate()│
                    │   4. HallucinationDetector (Phase 3)    │
                    │   5. Return RAGResponse dict            │
                    └──────────┬────────────────────────────────┘
                               │
          ┌────────────────────┴─────────────────────┐
          ▼                                          ▼
  ┌──────────────┐                         ┌──────────────┐
  │ LangGraph    │                         │ Resilient    │
  │ Agent (M3)   │                         │ Pipeline     │
  │ classify →   │                         │ Circuit      │
  │ retrieve →   │                         │ Breaker      │
  │ generate →   │                         │ (Phase 5)    │
  │ verify →     │                         └──────────────┘
  │ retry on     │
  │ groundedness │
  │ < 0.7        │
  └──────────────┘
```

### 1.2 Core Design Principles

- **Graceful degradation:** Every optional dependency (`sentence-transformers`, `torch`, `qdrant-client`, `langgraph`, `celery`) is imported lazily. The system degrades to deterministic/local fallbacks rather than crashing.
- **Single config seam (`cfg`):** All feature flags resolve through `app/shared/config.py` (`cfg`) using Pattern A: Flask config wins inside an app context, `os.environ` outside, else the declared default.
- **Separation of ingestion and retrieval:** Ingestion writes chunks to Qdrant; retrieval reads from Qdrant. They share only the `Chunk` / `RetrievedChunk` dataclass contract (§5.1).
- **Hash-chained audit:** `RAGQueryLog` records every query invocation with a SHA-256 chain. Cache hits are logged too (audit trail unaffected by caching).
- **Stub mode by default (tests):** `RAG_USE_STUB_LLM` allows the generation pipeline to run end-to-end without API keys.
- **Qdrant-side BM25:** Sparse vectors computed in-cluster (`Qdrant/bm25`), not locally at query time — zero local `fastembed` cost at query time.

### 1.2 Stack Summary

| Layer | Technology | Notes |
|-------|-----------|-------|
| Web Framework | Flask 2.x + FastAPI gateway (`asgi.py`) | FastAPI mounts Flask at `/` via `a2wsgi.WSGIMiddleware`; RAG routes available at both `/rag/query` and `/api/v2/rag/query` |
| Vector Store | Qdrant Cloud | Dense (COSINE, 768-dim) + sparse (BM25) |
| Graph Store | Neo4j Aura | Legal KG + case-file graph |
| Embeddings | Modal (remote) / `sentence-transformers` (local) | `all-mpnet-base-v2` (768-dim) |
| Reranker | Modal (remote CE) / `CrossEncoder` (local) | `sumanksaha/Foodmultidomain` fine-tuned legal CE |
| Generation LLM | OpenRouter / OpenAI | `poolside/laguna-s-2.1:free` default; stub mode for tests |
| Task Queue | Celery (Redis broker) + QStash | QStash for webhook-based async; Celery for heavier jobs |
| Database | PostgreSQL (primary) / SQLite (dev) | SQLAlchemy 2.x ORM |

---

## 2. Phase 1 — Ingestion & Corpus Pipeline

### 2.1 Pipeline Architecture

The ingestion pipeline transforms raw legal documents into indexed vector chunks in Qdrant. It is a linear chain:

```
Raw Document (PDF/DOCX/TXT)
    │
    ▼
DocumentLoaderFactory
    ├── PDFLoader        (pdfplumber → fitz fallback)
    ├── DOCXLoader       (python-docx)
    └── TXTLoader        (chardet)
    │
    ▼
LegalDocumentOCR        (EasyOCR — lazy, for scanned PDFs)
    │
    ▼
Chunker                 (LegalParagraphEngine → §5.1 LegalChunk)
    │
    ▼
ChunkDeduper            (SHA-256 normalized hashing)
    │
    ▼
MetadataAdapter         (LegalMetadataEngine → §5.1 payload)
    ├── CitationAdapter (§2.3 → §5.1/§5.2 citations)
    ├── CrossRefAdapter (full Act sections → §5.1/§5.2 references)
    └── EntityExtractor (rule → spaCy NER → LLM fallback)
    │
    ▼
ChunkQualityValidator   (A-F grading)
    │
    ▼
EmbeddingService        (dense 768-dim vectors)
    ├── RemoteEmbedClient (Modal /embed) or
    └── SentenceTransformer (all-mpnet-base-v2)
    │
    ▼
QdrantIndexer           (after_flush hook, retry-once upsert, batch=100)
    ├── QdrantStore     (upsert/search/delete/scroll_all/health)
    └── SparseEmbeddingService (BM25 sparse vectors)
    │
    ▼
IngestionLogger         (progress, errors, hash chain)
```

### 2.2 Chunker (`app/rag/chunker.py`, 370 lines)

**Class:** `Chunker`

Transforms raw legal text into `LegalChunk` objects conforming to the §5.1 payload schema.

| Component | Role |
|-----------|------|
| `LegalParagraphEngine` | Splits legal text into semantically coherent paragraphs (sections, subsections, clauses) using rule-based boundary detection on `§`, `sub`, numbering patterns. |
| `Chunk` dataclass | Plain dataclass with §5.1 payload fields: `chunk_id`, `document_id`, `document_uri`, `document_title`, `document_type`, `authority`, `jurisdiction`, `state`, `act_name`, `effective_date`, `enactment_date`, `amended_date`, `is_current`, `chunk_index`, `chunk_text`, `chunk_char_count`, `section_number`, `section_title`, `subsection`, `hierarchy_level`, `parent_chunk_id`, `citations`, `references`, `entities`, `confidence`, `created_at`, `embedding_model`, `content_hash`. |
| `ChunkIngestionResult` | Return type from `QdrantIndexer.index_document()` — carries `chunk_count`, `points_upserted`, `errors`. |

**Key methods:**
- `chunk_document(text, document_id, ...)` → splits text into `Chunk` objects using the legal paragraph engine.
- `validate_vector_size(vector, expected)` — guards against dimension mismatch (e.g. `all-MiniLM-L6-v2` 384-dim would break 768-dim collections).

### 2.3 Embedding Service (`app/rag/embedding_service.py`, 177 lines)

**Class:** `EmbeddingService`

Produces dense 768-dimensional vectors.

| Provider | Condition | Model |
|----------|-----------|-------|
| `RemoteEmbedClient` | `RAG_EMBED_ENDPOINT` set | Modal `/embed` (768-dim) |
| `SentenceTransformer` | Fallback (endpoint empty or remote failure) | `sentence-transformers/all-mpnet-base-v2` |

**Key methods:**
- `embed_text(text)` → `list[float]` — single text embedding.
- `embed_batch(texts)` → `list[list[float]]` — batched embedding with lazy local fallback.
- `dimension` property — returns vector dimension (768 for all-mpnet-base-v2).

**Lazy fallback:** `RAG_EMBED_REMOTE_FALLBACK` (default: opt-out=true) controls whether a remote failure falls back to the local embedding model. On Render Free (512MB RAM), this must be `false` to prevent torch OOM — the pipeline degrades to sparse-only.

### 2.4 Sparse Embedding Service (`app/rag/sparse_embedding.py`, 145 lines)

**Class:** `SparseEmbeddingService`

Produces BM25 sparse vectors for lexical search, using `fastembed` with the `Qdrant/bm25` model (default `RAG_SPARSE_MODEL`).

**Key methods:**
- `embed_sparse(text)` → `{"indices": [int, ...], "values": [float, ...]}` — BM25 sparse vector in Qdrant's indexed format.
- `embed_batch(texts)` → batched sparse vectors.

### 2.5 Qdrant Store (`app/rag/qdrant_client.py`, 946 lines)

**Class:** `QdrantStore`

The low-level Qdrant client abstraction wrapping `qdrant-client`.

**Key methods:**
- `connect()` / `health()` — connection verification and 200 OK probe.
- `create_collection(vector_size, ...)` — creates a collection with dense + sparse vector configuration.
- `has_sparse_vectors()` → `bool` — capability check for BM25 sparse path.
- `upsert(points, batch_size=100)` — batch upsert (UPSERT_BATCH_SIZE=100 prevents oversized payload failures on Qdrant Cloud).
- `search_dense(query_vector, top_k, filters)` — dense vector similarity search.
- `search_sparse(sparse_vector, top_k, filters)` — sparse/BM25 vector search.
- `search_sparse_text(query, top_k, filters)` — server-side BM25 in-cluster search (no local fastembed).
- `hybrid_search(dense_vector, sparse_vector, top_k, filters)` — server-side dense+sparse RRF fusion (single round trip).
- `hybrid_search_text(dense_vector, query_text, top_k, filters)` — server-side dense + Qdrant-BM25 text fusion.
- `delete(filter)` / `delete_all()` — point deletion by payload filter.
- `scroll_all(limit, filter)` — full collection scroll (used for backup/restore).
- `_payload_to_chunk(point)` — converts a Qdrant point to a `RetrievedChunk` (shared by dense and sparse retrievers).
- `ensure_indexes(fields)` — creates payload indexes on commonly-filtered fields.

**Test mode:** In-memory Qdrant test mode — no external connection needed for unit tests.

### 2.6 Qdrant Indexer (`app/rag/qdrant_indexer.py`, 518 lines)

**Class:** `QdrantIndexer`

Composes the `Chunker` → `EmbeddingService` → `SparseEmbeddingService` → `QdrantStore` chain. Registered as a SQLAlchemy `after_flush` hook so new `LegalChunk` rows trigger automatic indexing.

**Key methods:**
- `index_document(text, document_meta)` → `ChunkIngestionResult` — full chain: chunk → embed → upsert.
- `_build_payload(chunk, document_meta)` — stamping the §5.1 payload schema onto each point.
- `_upsert_with_retry(points)` — retry-once upsert on transient failures.

### 2.7 Chunk Deduper (`app/rag/dedup.py`, 157 lines)

**Class:** `ChunkDeduper`

Deduplicates chunks using SHA-256 normalized hashing:
- Normalizes text (strip, lowercase, collapse whitespace) before hashing.
- `document_id` granularity: rejects documents whose hash matches an existing indexed document.
- `chunk_id` granularity: rejects individual chunks whose normalized hash matches.
- Batch dedup: `dedup_batch(chunks)` returns `(kept, duplicates)`.

### 2.8 Document Classifier (`app/rag/document_classifier.py`, 216 lines)

**Class:** `DocumentClassifier`

Classifies legal documents into Act vs Rule, and extracts domain metadata.

- Uses regex patterns for primary classification (Act number/year, rule title patterns).
- Returns `(document_type, domain, confidence)` tuple.
- Feeds into `MetadataAdapter` for §5.1 payload stamping.

### 2.9 Metadata Adapter (`app/rag/metadata_adapter.py`, 251 lines)

**Class:** `MetadataAdapter`

Transforms extracted legal metadata into the §5.1 Qdrant payload schema.

- `adapt(document_meta)` → dict conforming to §5.1 fields.
- Handles enum normalization (`document_type`, `legal_domain`).
- Date extraction (`effective_date`, `enactment_date`, `amended_date`).
- Domain-aware: multi-domain Phase 1 (2026-08-20) threads `legal_domain` and `act_name` through.

### 2.10 Citation Adapter (`app/rag/citation_adapter.py`, 131 lines)

**Class:** `CitationAdapter`

Extracts §2.3-style citations from legal text and normalizes them to the §5.1/§5.2 citation schema.

- Fixed extractor (regex-based) matching `§`, `section`, `clause`, subsection patterns.
- Normalizes to structured `{section_number, section_title, act_name}`.
- Returns list of citation dicts.

### 2.11 Cross-Reference Adapter (`app/rag/crossref_adapter.py`, 171 lines)

**Class:** `CrossRefAdapter`

Extracts cross-references to full Act sections and normalizes them to §5.1/§5.2 reference schema.

- Full-Act section resolution (not just §-citations within a document).
- Uses `app/rag/legal_sections.py` BNS 1–358 registry for criminal law references.
- Returns list of `{section, act_name, reference_type}` dicts.

### 2.12 Entity Extractor (`app/rag/entity_extractor.py`, 483 lines)

**Class:** `LegalEntityExtractor`

3-tier entity extraction pipeline:
1. **Rule-based:** regex patterns for legal entities (officer names, penalties, offence descriptions, dates, monetary amounts).
2. **spaCy NER:** `spacy` NER model (lazy-loaded, optional dependency).
3. **LLM fallback:** `GroundedLLMClient` as final backstop when rule + NER yield insufficient coverage.

**Entity types:** `role`, `person`, `penalty`, `offence`, `date`, `money`, `authority`, `provision`.

Returns list of `{name, type, confidence, start, end}` dicts, stored as plain name lists on the `LegalChunk.entities` payload field (structured form preserved internally).

### 2.13 Chunk Quality Validator (`app/rag/chunk_quality.py`, 190 lines)

**Class:** `ChunkQualityValidator`

Grades chunk quality A–F based on:
- Text length (too short → F, optimal 200–2000 chars → A/B).
- Section number presence (missing → penalty).
- Citation/reference density (bare URLs or empty → penalty).
- Entity extraction yield (low yield → penalty).

**Key method:** `grade(chunk)` → `"A"` through `"F"`.

### 2.14 Ingestion Logger (`app/rag/ingestion_logger.py`, 190 lines)

**Class:** `IngestionLogger`

Records ingestion events (progress, errors, hash chain) for audit and debugging.

- `log_ingestion(document_id, chunks, errors)` → writes to `LegalDocument` model.
- `log_error(document_id, error)` → structured error recording.
- Hash-chained audit trail compatible with the `AuditLog` SHA-256 chaining pattern.

### 2.15 Legal OCR (`app/rag/legal_ocr.py`, 122 lines)

**Class:** `LegalDocumentOCR`

Scanned-PDF OCR pipeline using EasyOCR (lazy-loaded, optional dependency).

**Flow:**
1. Extract images from PDF pages (`pdfplumber` or `PyMuPDF`).
2. Run EasyOCR on each image (configurable languages via `OCR_LANGUAGES`, default `english,hindi`).
3. Reconstruct text with page ordering and section boundary detection.
4. Returns `LegalDocument` with extracted text + metadata.

### 2.16 Backup/Restore (`app/rag/backup.py`, 260 lines)

**Functions:** `export_corpus_backup()`, `import_corpus_backup()`, `verify_corpus_integrity()`

- Uses `QdrantStore.scroll_all()` to read every point.
- Serializes to JSON with vectors + full payload.
- `import_corpus_backup()` restores via `QdrantStore.upsert()`.
- `verify_corpus_integrity()` checks point counts, payload field coverage, and content-hash collisions.

### 2.17 Ingestion Scripts

| Script | Purpose |
|--------|---------|
| `scripts/ingest_corpus.py` (122 lines) | CLI entry point: `QdrantIndexer` — chunk → embed → upsert for a single document. Calls `run_embed_and_index()`. |
| `scripts/reingest_fssai_from_db.py` (278 lines) | P1-4 FSSAI re-ingest: rebuilds `fssai_legal_768` from the FSS DB. Identity-preserving: `chunk_id = LegalChunk.id`. Backs up pre-reingest state. |
| `scripts/export_fssai_backup.py` (53 lines) | Exports pre-reingest backup: 1,100 points w/ vectors to `reports/fssai_legal_768_pre_reingest_backup.json`. |

---

## 3. Phase 1 — Retrieval Pipeline

### 3.1 Pipeline Flow (`app/rag/tasks.py::run_retrieval_pipeline`)

```
run_retrieval_pipeline(query, top_k, collection_name, filters, pipeline)
    │
    ├── 1. QueryClassifier.classify(query) → QueryType
    ├── 2. QueryParser.parse(query, query_type) → parsed filters
    ├── 3. Merge: {**parsed_filters, **caller_filters}
    ├── 4. Legal query typing (optional, cfg.legal_query_typing)
    │       classify_legal_query(query) → legal QueryType string
    ├── 5. Identifier route (optional, cfg.identifier_route)
    │       identifier_query(query) → ("Indian Contract Act section 73", {...})
    ├── 6. Cache lookup (cfg.retrieval_cache)
    │       key = _retrieval_cache_key(query, top_k, collection, ...)
    │       hit → SearchResult(source="cache")
    │       miss → HybridRetriever.retrieve(...)
    │           └── DenseRetriever.search()  (Qdrant vector search)
    │           └── SparseRetriever.retrieve() (BM25 / rapidfuzz)
    │           └── (identifier arm via sparse.retrieve(identifier_query))
    │           └── RRF fusion (reciprocal_rank_fuse, k=60)
    │           └── EnsembleReranker.rerank()
    ├── 7. RetrievalLogger.log() — hash-chained audit (cache hits logged too)
    ├── 8. apply_stages(query, result) — post-retrieval enrichment
    └── 9. Return dict: {query, query_type, chunks, total, latency_ms, ...}
```

### 3.2 Query Classifier (`app/rag/retrieval/query_classifier.py`, 259 lines)

**Classes:** `QueryClassifier`, `QueryParser`, `QueryType` (enum)

**Classifies queries into 4 types:**
| Type | Regex patterns | Extracted fields |
|------|----------------|------------------|
| `SECTION` | `§`, `section`, `sub`, numbering | `section_number`, `clause_number`, `act` |
| `AUTHORITY` | act names, year patterns | `authority`, `act_name`, `year` |
| `CASE_LAW` | "case", "judgment", "tribunal", citation patterns | `authority`, `year`, `case_name` |
| `JURISDICTION` | state/territory names, "jurisdiction" | `jurisdiction`, `state` |

**Key methods:**
- `QueryClassifier.classify(query)` → `QueryType`.
- `QueryParser.parse(query, query_type)` → `dict` of extracted filters (section_number, act_name, authority, jurisdiction, state).

### 3.3 Legal Query Classifier (`app/rag/retrieval/legal_query_classifier.py`, 478 lines)

**Class:** `LegalQueryClassifier` / function `classify_legal_query`

Maps queries to legal query-type configurations that drive reranker weight overrides. Each `QueryTypeConfig` carries:
- `feature_weight` — scales sec_match/act_match/exact weights.
- `hierarchy_weight` — override for gold-chunk concentration at levels 3–5.
- `ce_head` — post-sec_act head size for CE scoring.
- `ce_weight` — bonus weight for normalized CE head scores.
- `ce_threshold` — min score to retain CE re-scored chunks.
- `skip_ce` — whether to skip the cross-encoder entirely.

**Query types:** `prohibition`, `authority`, `cross_reference`, `penalty`, `jurisdiction`, `case_law`, `general`. Each has empirically-tuned weights from the CE_RERANK_REVIEW evaluation (2026-08-14).

**Accessors:** `get_config(query_type)` → `QueryTypeConfig`; `classify_legal_query(query)` → `str`.

### 3.4 Dense Retriever (`app/rag/retrieval/dense_retriever.py`, 284 lines)

**Class:** `DenseRetriever`

Produces dense query embeddings and performs vector similarity search in Qdrant.

**Key methods:**
- `embed_query(text)` → `list[float]` — uses `RemoteEmbedClient` (if `RAG_EMBED_ENDPOINT` set) or `SentenceTransformer` (local).
- `search(query, top_k, filters)` → `SearchResult` — embeds the query and calls `QdrantStore.search_dense()`.
- `_payload_to_chunk(point)` (staticmethod) — converts a Qdrant point to a `RetrievedChunk`, shared by dense and sparse retrievers.

**Config-driven collection selection:** consults `app/rag/collections.py` for multi-domain routing (Phase 1, 2026-08-20).

### 3.5 Sparse Retriever (`app/rag/retrieval/sparse_retriever.py`, 243 lines)

**Class:** `SparseRetriever`

Two-path sparse retrieval:

| Path | Condition | Mechanism |
|------|-----------|-----------|
| **Primary: Qdrant BM25** | `store` is set + `store.has_sparse_vectors()` + `server_bm25=True` | Server-side BM25 via `store.search_sparse_text()` — Qdrant computes the BM25 vector in-cluster, no local fastembed. |
| **Fallback: rapidfuzz** | Store path unavailable/fails | Fuzzy token-set-ratio + partial-ratio matching against in-memory corpus dict. |

**Key methods:**
- `embed_query(text)` → `{indices, values}` — BM25 sparse vector via `SparseEmbeddingService`.
- `retrieve(query, top_k, threshold, filters)` → `SearchResult` — primary path or rapidfuzz fallback.
- `_field_score(query, text)` (staticmethod) — best of `token_set_ratio` + `partial_ratio` (0–100); Rust-accelerated via `nsa_rust.field_score` when available (Part 2).

**Rust acceleration:** `nsa_rust.field_score` (Part 2) — lazy import; falls back to pure-Python rapidfuzz. See `rust/normalizers.rs`.

### 3.6 Hybrid Retriever (`app/rag/retrieval/hybrid_retriever.py`, 208 lines)

**Class:** `HybridRetriever`

Fuses dense + sparse retrieval results using Reciprocal Rank Fusion (RRF).

**Two execution paths:**

| Path | Condition | Method |
|------|-----------|--------|
| **Server-side fusion** | `identifier_query is None` + store sparse-capable + both retrievers can embed | `QdrantStore.hybrid_search()` or `hybrid_search_text()` — single round trip. |
| **Client-side RRF** | Default (or identifier arm present) | `reciprocal_rank_fuse()` over dense + sparse + identifier lists. |

**Identifier route arm (V5.5-validated):** When `cfg.identifier_route=True`, a lexical identifier query (e.g. `"Indian Contract Act, 1872 section 73"`) is built from detected Act+section and run through the sparse retriever as a **parallel additive arm**, RRF-fused with dense + sparse. Measured: +13.3pp candidate-pool ceiling (0.705→0.838).

**Key method:** `retrieve(query, top_k, dense_weight, sparse_weight, filters, identifier_query, query_type)` → `SearchResult`.

### 3.7 RRF (`app/rag/retrieval/rrf.py`, 67 lines)

**Constant:** `DEFAULT_RRF_K = 60.0` (standard Cormack et al., 2009 value).

**Function:** `reciprocal_rank_fuse(ranked_lists, rrf_k=60.0)` → `dict[chunk_id, float]`

```
score(chunk) = Σ 1 / (rank(chunk, method) + k)
```

Rank-based (not score-based), so scores from different retrievers are comparable regardless of scale. The `dense_weight`/`sparse_weight` parameters in `HybridRetriever.retrieve()` are **kept for API compatibility but not used** — RRF is rank-based.

### 3.8 Result Types (`app/rag/retrieval/result.py`, 135 lines)

| Dataclass | Purpose | Fields |
|-----------|---------|--------|
| `RetrievedChunk` | A single chunk from vector/sparse search | `chunk_id`, `score`, `text`, `section_number`, `clause_number`, `document_title`, `act_name`, `document_type`, `authority`, `chunk_index`, `hierarchy_level`, `parent_chunk_id` + `to_dict()`/`from_dict()` |
| `SearchResult` | Retrieval result for one query | `query`, `query_type`, `chunks`, `total`, `latency_ms`, `source` ("dense"/"sparse"/"hybrid"/"cache"), `error` |
| `Citation` | A citation supporting an LLM response | `chunk_id`, `section_number`, `document_title`, `document_type`, `authority`, `url`, `snippet`, `confidence` |
| `RAGResponse` | Full RAG response schema (Phase 5) | `query`, `query_type`, `answer`, `citations`, `retrieved_chunks`, `groundedness_score`, `hallucination_detected`, `hallucinated_claims`, `confidence`, `retrieval_latency_ms`, `generation_latency_ms`, `total_latency_ms`, `prompt_tokens`, `completion_tokens`, `llm_model`, `token_usage`, `debug` |

### 3.9 Ensemble Reranker (`app/rag/retrieval/reranker.py`, 413 lines)

**Two reranker classes:**

#### 3.9.1 `Reranker` (plain)

Plain cross-encoder reranker with deterministic fallback.
- `_get_encoder()` — lazy `CrossEncoder` import; `RAG_TORCH_THREADS` bound before load.
- `_rerank_cross_encoder(query, chunks, encoder, top_k)` — optionally prefixes passages with `§<section>` identity (when `RAG_CE_SECTION_PREFIX` is on).
- `_rerank_fallback(query, chunks, top_k)` — BM25-style term-frequency + fuzzy blend: `0.5 * chunk.score + 0.5 * combined`.
- Constants: `_REGEX_BOOST = 0.85`, `_FUZZY_BOOST = 0.70`.

#### 3.9.2 `EnsembleReranker` (sec_act + CE, production — `RAG_ENSEMBLE_RERANK` default on)

CE_RERANK_REVIEW (2026-08-14). Production form of the V5.5 evaluation finding: deterministic `sec_act` features are the strongest single reranker (R@10 0.474 vs 0.362 for CE), but the two are complementary (union any-hit R@10 = 62.0% vs 56.7%).

**Algorithm:**
1. **sec_act primary** — rank all chunks by `base_score + 2.0 * sec_match + 1.5 * act_match + 1.0 * exact + 0.2 * hierarchy`.
   - `_W_SEC = 2.0`, `_W_ACT = 1.5`, `_W_EXACT = 1.0` (sec AND act), `_W_HIERARCHY = 0.2`.
   - `sec_match`: query-detected section number == chunk's leading section number.
   - `act_match`: query-detected Act contained in chunk's `act_name` ∪ `document_title`.
   - `_hierarchy_boost(level)`: 1.0 for levels 3–5 (section/subsection/clause).
2. **CE second opinion on the head only** — score top `ce_head` (default 30, from `cfg.ensemble_ce_head`) chunks with the cross-encoder, min-max normalize (`_minmax`), add `ce_weight * norm` (default 0.5) to primary score.
3. **Dynamic CE skipping:** when both section + Act are detected in the query AND the entire head has exact matches, CE is skipped (~5–9s saved).
4. **Graceful degradation:** no encoder / predict failure → pure sec_act ranking.

**Query-type-aware weights:** When `query_type` is provided (from `legal_query_classifier`), `get_config(query_type)` returns overrides: `feature_weight`, `hierarchy_weight`, `ce_head`, `ce_weight`, `skip_ce`. E.g. prohibition queries use `hierarchy=0`; authority queries need larger CE head; cross-reference queries rely on identifier recovery.

### 3.10 Identifier Route (`app/rag/retrieval/identifier.py`, 142 lines)

**Functions:** `detect_act(query)`, `detect_section(query)`, `identifier_query(query)`

- `detect_act(query)` — matches Act names from `RAG_LEGAL_SECTIONS` registry, returns `(act_name, year)`.
- `detect_section(query)` — extracts section/clause numbers from `§12`, `section 73`, `sub-section (2)` patterns, returns `(section_number, subsection)`.
- `identifier_query(query)` → `(lexical_query, metadata_dict)` — builds a lexical query like `"Indian Contract Act, 1872 section 73"` from detected identifiers.
- Gated by `cfg.identifier_route` (opt-out, default true).

### 3.11 Post-Retrieval Stages (`app/rag/retrieval/stages.py`, 165 lines)

**Function:** `apply_stages(query, result)` → `dict` of enrichment fields

Applies optional, independent, error-isolated enrichment stages to the retrieval result:

| Stage | Gate | Module | Purpose |
|-------|------|--------|---------|
| Legal identity parsing | `cfg.legal_identity` (default true) | `legal_identity.py` | Canonical Act/section identity for retrieved chunks. |
| Section prefix detection | `cfg.ce_section_prefix` (default false) | `section_prefix.py` | §-identity prefix for CE passage scoring. |
| Legal hierarchy expansion | `cfg.enable_reference_expansion` (default false) | `legal_hierarchy.py` | Section→subsection→clause tree. |
| Provision versions | — | `provision_versions.py` | Historical amendment version resolution. |
| Reference graph expansion | `cfg.enable_reference_expansion` (default false) | `reference_graph.py` | Cross-reference graph expansion. |
| Temporal validity | — | `temporal_validity.py` | Filter repealed/amended sections by date. |
| Evidence selector | `cfg.evidence_selector` (default false) | `evidence_selector.py` | Evidence-set selection over top-K. |

Each stage is independently feature-gated and error-isolated (`isolate=True`) — failures don't break the pipeline.

### 3.12 Legal Identity (`app/rag/retrieval/legal_identity.py`, 81 lines)

**Function:** `resolve_legal_identity(chunks)` → canonical identity mapping

Normalizes retrieved chunks to canonical legal identity (Act name, section number, clause). Handles:
- Sub-instrument resolution (regulation → parent Act).
- Section number normalization (e.g. "39(b)" → "39").
- Cross-domain identity (multi-domain Phase 1).

### 3.13 Legal Hierarchy (`app/rag/retrieval/legal_hierarchy.py`, 285 lines)

**Class:** `LegalHierarchyExtractor`

Builds section → subsection → clause hierarchy trees from chunk payloads. Used for:
- Hierarchy-level boosting in `EnsembleReranker` (`_hierarchy_boost`).
- Section prefix detection (`§` vs `clause` prefixes).

### 3.14 Provision Versions (`app/rag/retrieval/provision_versions.py`, 380 lines)

**Class:** `ProvisionVersionResolver`

Resolves historical amendment versions of legal provisions using:
- `effective_date`, `enactment_date`, `amended_date` payload fields.
- `is_current` flag for repeal/supersession handling.
- Cross-reference to CrossRefAdapter resolved citations.

### 3.15 Evidence Selector (`app/rag/retrieval/evidence_selector.py`, 454 lines)

**Class:** `EvidenceSelector`

Selects the minimal evidence set from top-K chunks that maximally supports a generated answer. Gated by `cfg.evidence_selector` (default false, opt-in A/B lever).

### 3.16 Evidence Metrics (`app/rag/retrieval/evidence_metrics.py`, 278 lines)

**Class:** `EvidenceMetrics`

Computes retrieval quality metrics over the evidence set: coverage, density, redundancy, diversity. Feeds into the evaluation framework.

### 3.17 Reference Extractor (`app/rag/retrieval/reference_extractor.py`, 335 lines)

**Class:** `ReferenceExtractor`

Extracts cross-references (§-citations to other provisions) from retrieved chunk text. Used by the reference graph expansion stage.

### 3.18 Reference Graph (`app/rag/retrieval/reference_graph.py`, 401 lines)

**Class:** `ReferenceGraph`

Graph-based cross-reference recovery: expands a chunk's referenced sections by traversing known legal reference patterns. Used by `apply_stages()` when `RAG_REFERENCE_EXPANSION` is enabled.

### 3.19 Temporal Validity (`app/rag/retrieval/temporal_validity.py`, 370 lines)

**Class:** `TemporalValidityFilter`

Filters retrieved chunks by temporal validity:
- Checks `is_current` flag.
- Applies date-range filters against `effective_date` / `enactment_date`.
- Handles repealed/superseded sections.

### 3.20 Section Prefix (`app/rag/retrieval/section_prefix.py`, 84 lines)

**Function:** `prefix_passage(text, section_number, clause_number)` → `str`

Prefixes a passage with its legal identity for CE scoring:
- Act sections: `§<section_number> <text>`.
- Regulations (no section_number): `§<clause_number> <text>` fallback.
- Gated by `cfg.ce_section_prefix` (default false, CV2 P1).

### 3.21 Retrieval Cache (`app/rag/retrieval/cache.py`, 83 lines)

**Class:** `RetrievalCache`

LRU + TTL cache (stdlib only, thread-safe). Default: `max_size=512`, `ttl_seconds=600`.

| Method | Behavior |
|--------|----------|
| `get(key)` | Returns cached `SearchResult` or `None` (expired entries evicted). |
| `put(key, value)` | Inserts with TTL; evicts LRU entry if over max_size. |
| `clear()` | Drops all entries. |

Gated by `cfg.retrieval_cache` (default false, test-safe). Cache hits still run `RetrievalLogger.log()` so the hash-chained audit trail records every query invocation.

**Cache key:** `_retrieval_cache_key(query, top_k, collection, filters, query_type, legal_qt, identifier)` — a hashable tuple including the query (lowercased+stripped), top_k, collection, query_type, legal_qt, identifier form, and JSON-serialized filters.

### 3.22 Logger (`app/rag/retrieval/logger.py`, 177 lines)

**Class:** `RetrievalLogger`

Records every retrieval in the hash-chained `RAGQueryLog` table.

**Key method:** `log(query, query_type, result, pipeline)` → `RAGQueryLog` row.
- Computes SHA-256 of the query + result hash; links to previous entry.
- Records `retrieval_latency_ms`, `token_count`, `top_k`, `source`, `pipeline` stamp (`"legacy"` / `"agent"`).
- `pipeline` parameter stamps the `RAGQueryLog.pipeline` column for the M5 A/B rollout comparison.

### 3.23 Factory / Composition Root (`app/rag/retrieval/factory.py`, 122 lines)

**Functions:** `build_hybrid_retriever(collection_name)`, `build_reranker()`, `build_retrieval_components()`

Single module that owns the wiring of the retrieval stack. Eliminates the historical inline assembly (and the wrong-collection bug class it bred).

- `build_hybrid_retriever(collection_name)`: constructs `DenseRetriever` + `SparseRetriever` + `EnsembleReranker` (when `cfg.ensemble_rerank`) or `Reranker`, wired with `DenseWeight`/`SparseWeight` defaults and `QdrantStore` collection-aware.
- `build_reranker()`: returns `EnsembleReranker` (default) or `Reranker` based on `cfg.ensemble_rerank`. Delegates encoder construction to `build_reranker_encoder()`.
- Collection-aware: consults `app/rag/collections.py` for per-domain collection selection.

### 3.24 Domain Collections (`app/rag/collections.py`, 70 lines)

**Function:** `collection_for_domain(domain)` → `str`

Multi-domain collection map (Phase 1, 2026-08-20):

| Domain | Collection |
|--------|------------|
| `fssai` | `fssai_legal_768` |
| `env` | `env_legal_768` |
| `commercial` | `commercial_legal_768` |
| `animal` | `animal_legal_768` |
| `wb_state` | `wb_state_legal_768` |
| `criminal` | `criminal_legal_768` |

Consulted by `DenseRetriever` / `HybridRetriever` / `QdrantStore` for collection-aware queries.

### 3.25 Legal Sections Registry (`app/rag/legal_sections.py`, 102 lines)

**Registry:** `LEGAL_SECTIONS` — static registry of legal sections by Act.

Includes BNS 1–358 (criminal law) for multi-domain support. Used by `CitationAdapter`, `CrossRefAdapter`, and `identifier.detect_act()`.

### 3.26 Sparse Embedding (`app/rag/sparse_embedding.py`, 145 lines)

**Class:** `SparseEmbeddingService` (detailed in §2.4 above).

---

## 4. Phase 2 — Grounded Generation

### 4.1 Pipeline Flow (`app/rag/tasks.py::run_generation_pipeline`)

```
run_generation_pipeline(query, chunks, query_type, top_k, collection_name, filters, pipeline)
    │
    ├── 1. If chunks is None → run_retrieval_pipeline() (Phase 1)
    ├── 2. KG fusion (cfg.kg_fusion) — RRF-fuse query→graph provisions
    ├── 3. KG expansion (cfg.kg_expansion) — expand chunk IDs → graph provisions
    │       (mutually exclusive with fusion — if fusion injected, skip expansion)
    ├── 4. GroundedGenerationService.generate(query, chunks, query_type)
    │       ├── ContextBuilder (max_context_chars=12000, max_chunks=10)
    │       ├── PromptTemplate (domain-parameterized)
    │       ├── GroundedLLMClient (OpenRouter/stub)
    │       ├── CitationTracker (assigns &[n] brackets)
    │       ├── ResponseSanitizer (_GROUNDEDNESS_THRESHOLD=0.50)
    │       └── GenerationLogger (audit trail)
    ├── 5. HallucinationDetector (cfg.hallucination_detector, default true)
    │       Claims → Evidence → Citations → GroundednessScorer → escalate
    └── 6. Return dict: {query, answer, citations, groundedness_score, ...}
```

### 4.2 Grounded Generation Service (`app/rag/generation/grounded_service.py`, 290 lines)

**Class:** `GroundedGenerationService`

Orchestrates the generation pipeline: context building → LLM call → citation → sanitization → logging.

**Key method:** `generate(query, chunks, query_type)` → `GroundedLLMResponse`

Returns a `GroundedLLMResponse` dataclass with: `query`, `query_type`, `answer`, `citations`, `retrieved_chunks`, `groundedness_score`, `llm_model`, `prompt_tokens`, `completion_tokens`, `generation_latency_ms`, `debug`.

### 4.3 Context Builder (`app/rag/generation/context_builder.py`, 140 lines)

**Class:** `ContextBuilder`

Builds the LLM context window from retrieved chunks.

| Attribute | Default | Configurable |
|-----------|---------|--------------|
| `max_context_chars` | 12,000 | No (hardcoded) |
| `max_context_chunks` | 10 | No (hardcoded) |
| `max_chunks` | 10 | via `to_dict()` param |

**Key methods:**
- `build(chunks, query_type)` → `BuiltContext` — trims chunks to fit `max_context_chars`, assigns `[Source n]` citation indices.
- `trim_to_budget(chunks, max_chars)` — greedy truncation that respects section boundaries.

### 4.4 Prompt Template (`app/rag/generation/prompt_template.py`, 161 lines)

**Class:** `PromptTemplate`

Domain-parameterized prompt construction.

**Template layers:**
1. **System prompt** — instructions for grounded legal response, citation format, refusal thresholds.
2. **Context block** — `[Source 1]`, `[Source 2]`, ... `[Source n]` from `BuiltContext`.
3. **Query block** — user question + `query_type` annotation.
4. **Domain-specific suffix** — Act-specific guidance (FSSAI, environmental, criminal, etc.).

**Key method:** `render(context, query, query_type, citations)` → `str` (full prompt string).

Domain parameterization threads `legal_domain` from `app/rag/collections.py` — the prompt suffix changes based on which Act/domain the query targets.

### 4.5 LLM Client (`app/rag/generation/llm_client.py`, 233 lines)

**Class:** `GroundedLLMClient`

HTTP client for LLM inference via OpenRouter (primary) or OpenAI (secondary).

**Configuration:**
| Setting | Default | Purpose |
|---------|---------|---------|
| `RAG_LLM_PROVIDER` | `openrouter` | Provider selection |
| `OPENROUTER_API_KEY` | — | Required for real LLM |
| `OPENAI_API_KEY` | — | Fallback if OpenRouter unset |
| `RAG_USE_STUB_LLM` | false / test-true | Stub mode — synthetic response, no API call |

**Default model:** `poolside/laguna-s-2.1:free` (OpenRouter free tier).

**Stub mode:** When `OPENROUTER_API_KEY` is unset or `RAG_USE_STUB_LLM=true`, returns a deterministic synthetic response with `answer="Stub response for: {query}"`, `llm_model="stub"`.

**Key methods:**
- `_call_llm(messages, temperature, max_tokens)` — sends request to provider.
- `_normalize_response(raw)` → `GroundedLLMResponse` — parses provider response into the unified dataclass.
- `stream` support via provider streaming API (optional).

**Retry policy:** 3 attempts with `2**attempt` exponential backoff. `RETRYABLE_STATUSES={408, 429, 500, 502, 503, 504}`.

### 4.6 Citation Tracker (`app/rag/generation/citation_tracker.py`, 189 lines)

**Class:** `CitationTracker`

Assigns `[Source n]` citation brackets to retrieved chunks and tracks which chunks support each sentence in the LLM response.

**Key methods:**
- `assign_citations(chunks)` → `dict[chunk_id, int]` — assigns 1-indexed citation numbers.
- `extract_citations_from_response(answer)` → `list[Citation]` — parses `[Source n]` / `[n]` references from the generated answer.
- `validate_citations(answer, citations)` → `bool` — checks all cited sources exist in the provided chunks.

### 4.7 Response Sanitizer (`app/rag/generation/sanitizer.py`, 180 lines)

**Class:** `ResponseSanitizer`

Post-generation response cleaning and grounding check.

| Constant | Value | Purpose |
|----------|-------|---------|
| `_GROUNDEDNESS_THRESHOLD` | 0.50 | Min fraction of answer sentences with citations to be considered grounded |
| `_MAX_ANSWER_LENGTH` | 2048 | Max answer characters |

**Key methods:**
- `sanitize(response)` → `SanitizedResponse` — strips PII patterns, truncates, flags ungrounded responses.
- `check_groundedness(answer, citations)` → `float` — fraction of answer sentences with at least one citation.
- `extract_claims(answer)` → `list[str]` — sentence-level claim extraction (feeds Phase 3 HallucinationDetector).

### 4.8 Generation Logger (`app/rag/generation/logger.py`, 153 lines)

**Class:** `GenerationLogger`

Records generation events (LLM calls, token usage, prompts) for audit and cost tracking.

- `log_generation(query, response, tokens)` — writes to `RAGQueryLog` or a dedicated generation log table.
- Records `prompt_tokens`, `completion_tokens`, `llm_model`, `generation_latency_ms`.

### 4.9 Token Counter (`app/rag/verification/token_counter.py`, 166 lines)

**Class:** `TokenCounter`

Shared by generation and verification for token usage estimation.

| Constant | Value |
|----------|-------|
| `_TOKENS_PER_WORD` | 1.3 |
| `_CHARS_PER_TOKEN` | 4.0 |

**Methods:**
- `count_tokens(text)` — uses `tiktoken` when available, falls back to `len(text) / _CHARS_PER_TOKEN` estimation.
- `count_messages(messages)` — token count for chat-format message lists.
- Populates `RAGQueryLog.context_length` (introduced in Phase 5 M5 checkpointing).

---

## 5. Phase 3 — Verification & Hallucination Detection

### 5.1 Architecture

```
GroundedLLMResponse (answer + citations)
    │
    ▼
HallucinationDetector.detect(answer, chunks, citations)
    │
    ├── 1. ClaimExtractor.extract(answer) → list[str] claims
    ├── 2. EvidenceVerifier.verify(claims, chunks, citations)
    │       ├── claim ↔ chunk overlap scoring
    │       ├── section consistency check
    │       └── confidence assignment
    ├── 3. CitationValidator.validate(citations, chunks)
    │       ├── chunk existence check
    │       ├── section match check
    │       └── snippet overlap check
    ├── 4. GroundednessScorer.score(verified_claims) → float
    └── 5. Assemble HallucinationReport
```

### 5.2 Claim Extractor (`app/rag/verification/claim_extractor.py`, 186 lines)

**Class:** `ClaimExtractor`

Extracts discrete factual claims from an LLM-generated answer for verification.

- Splits the answer into sentences (NLTK / regex fallback).
- Groups sentences into compound claims (multiple sentences on the same topic).
- Returns `list[str]` — one string per claim.

**Key method:** `extract(text)` → `list[str]`.

### 5.3 Evidence Verifier (`app/rag/verification/evidence_verifier.py`, 187 lines)

**Class:** `EvidenceVerifier`

Verifies each extracted claim against the retrieved evidence chunks.

- Computes claim-to-chunk similarity (TF-IDF + cosine, or exact string match fallback).
- Checks section consistency (claim mentions a section that the chunk supports).
- Assigns a `confidence` score (0.0–1.0) per claim.

**Key method:** `verify(claims, chunks, citations)` → `VerificationResults` (lists of verified/unverified claims + confidence scores).

### 5.4 Citation Validator (`app/rag/verification/citation_validator.py`, 126 lines)

**Class:** `CitationValidator`

Validates that every `[Source n]` citation in the answer maps to an actual retrieved chunk.

**Checks:**
- Chunk existence (does the cited `chunk_id` exist in the provided chunks?).
- Section match (does the cited chunk's section align with what the answer references?).
- Snippet overlap (cosine similarity between the cited chunk text and the answer context around the citation).

**Key method:** `validate(citations, chunks)` → `ValidationReport` (valid/invalid citations + mismatch details).

### 5.5 Groundedness Scorer (`app/rag/verification/scorer.py`, 130 lines)

**Class:** `GroundednessScorer`

Aggregates verification results into a single groundedness score (0.0–1.0).

- `score(verified_claims, total_claims)` → `GroundednessScore`.
- Weighted by claim confidence scores.
- Used by the agent pipeline: `groundedness < 0.7` triggers retry (max 2 retries).
- **Separate from the sanitizer threshold** (`_GROUNDEDNESS_THRESHOLD = 0.50`) which is a simpler heuristic on the generation path.

### 5.6 Hallucination Detector (`app/rag/verification/hallucination_detector.py`, 273 lines)

**Class:** `HallucinationDetector`

Orchestrates the full verification chain: `ClaimExtractor` → `EvidenceVerifier` → `CitationValidator` → `GroundednessScorer`.

**Key method:** `detect(answer, chunks, citations)` → `HallucinationReport`

**`HallucinationReport` fields:**
- `detected: bool` — whether hallucination was detected.
- `claims: list[str]` — all extracted claims.
- `verified_claims: list[str]` — claims supported by evidence.
- `unverified_claims: list[str]` — claims with insufficient evidence.
- `hallucinated_claims: list[str]` — claims contradicted or unsupported.
- `groundedness_score: float` — aggregate 0.0–1.0 score.
- `confidence: float` — confidence in the detection verdict.
- `llm_verified: bool` — whether LLM-assisted verification was used (optional).

**Integration:** Wired into `run_generation_pipeline()` behind `RAG_HALLUCINATION_DETECTOR` (default true, opt-out). Runs only when `answer` and `chunk_objects` are non-empty. Best-effort: failures are caught and logged as `verification = {"enabled": True, "error": str(exc)}` — never breaks the query. Escalates claim-level hallucinations the sanitizer missed into the top-level `hallucinated_claims` list.

### 5.7 Verification Package Init (`app/rag/verification/__init__.py`, 52 lines)

Exports: `ClaimExtractor`, `EvidenceVerifier`, `CitationValidator`, `GroundednessScorer`, `HallucinationDetector`, `TokenCounter`, `GroundednessScore`, `HallucinationReport`, `VerificationResults`, `ValidationReport`.

---

## 6. Phase 4 — Evaluation Framework

### 6.1 Architecture

```
EvalRunner (runner.py)
    │
    ├── Dataset: list[EvalEntry]  (query, gold_provisions, sources, rubric)
    ├── For each entry:
    │   ├── pipeline_fn(query) → RAGResponse
    │   ├── Run all 6 metrics against gold
    │   └── Accumulate EvalScore
    ├── EvalStorage (storage.py)
    │   ├── Persist EvalResult / EvalSummary to DB
    │   └── Query historical runs
    └── EvalReport (report.py)
        ├── Aggregate metrics across entries
        ├── Generate HTML/JSON report
        └── Print EvalSummary
```

### 6.2 Metrics (`app/rag/evaluation/metrics.py`, 502 lines)

Six metric types, each implementing a common `Metric` interface with `compute(gold, prediction)` → `float`:

| Metric | Class | Measures |
|--------|-------|----------|
| **Faithfulness** | `FaithfulnessMetric` | Fraction of generated claims supported by retrieved context (no hallucination). |
| **Answer Relevance** | `AnswerRelevanceMetric` | Semantic similarity between answer and query. |
| **Context Precision** | `ContextPrecisionMetric` | Fraction of retrieved chunks that are relevant to the query. |
| **Context Recall** | `ContextRecallMetric` | Fraction of gold chunks that were retrieved. |
| **Citation Recall** | `CitationRecallMetric` | Fraction of gold provisions that appear in citations. |
| **Groundedness** | `GroundednessMetric` | Proportion of answer sentences with supporting evidence (uses HallucinationDetector). |

**Common interface:** Each metric has `compute(gold: dict, pred: dict) -> EvalScore`. Some require the full `RAGResponse` (answer + retrieved_chunks + citations); others operate on lists alone.

**EvalScore dataclass:** `metric_name`, `score`, `details` (dict of sub-scores).

### 6.3 Eval Runner (`app/rag/evaluation/runner.py`, 313 lines)

**Class:** `EvalRunner`

**Key method:** `evaluate_batch(entries, eval_run_id, persist)` → `EvalSummary`

- Iterates over `entries` (list of `EvalEntry` dicts with `query`, `gold_provisions`, `gold_sources`, `rubric`).
- Calls `pipeline_fn(query)` — defaults to `run_generation_pipeline()`.
- Runs all 6 metrics per entry.
- `eval_run_id` — stamps the run for historical comparison.
- `persist=True` — writes `EvalResult` rows via `EvalStorage`.
- Error-isolated: a failing pipeline call doesn't abort the batch.

### 6.4 Eval Storage (`app/rag/evaluation/storage.py`, 181 lines)

**Class:** `EvalStorage`

Persists evaluation results to PostgreSQL via the `RAGEvalResult` model.

| Method | Purpose |
|--------|---------|
| `save_result(result, run_id)` | Inserts an `EvalResult` row. |
| `save_summary(summary, run_id)` | Inserts an `EvalSummary` row. |
| `get_history(run_id)` | Retrieves historical runs. |
| `query_summary(metric, threshold)` | Queries past results by metric performance. |

### 6.5 Eval Report (`app/rag/evaluation/report.py`, 65 lines)

**Classes:** `EvalReport`, `EvalSummary`

- `EvalReport`: full per-entry breakdown (query, per-metric scores, gold vs predicted, citations).
- `EvalSummary`: aggregate statistics (mean, min, max, standard deviation per metric).
- `generate_html(summary)` / `generate_json(summary)` — output formats.

### 6.6 Evaluation Entry Points

| Entry point | File | Purpose |
|-------------|------|---------|
| `run_evaluate()` | `app/rag/tasks.py:606` | Plain function — runs `EvalRunner.evaluate_batch()`. |
| `evaluate_task` | `app/rag/tasks.py:630` | Celery `bind=True` wrapper. |
| `POST /api/v2/rag/eval` | `app/api/routers.py` | FastAPI v2 route. |

### 6.7 Benchmark v1.0

**Frozen benchmark:** `benchmark/` package (7 content modules + 8 artifact files).

- 150-question frozen multi-domain JSONL benchmark.
- Gold provisions, sources, evaluation rubric.
- Review-conflict report (identifies questions where human judgment disagrees with automated scoring).
- `scripts/benchmark_rag.py` — custom timing harness.

---

## 7. Phase 5 — Resilience & Integration

### 7.1 Resilient Pipeline (`app/rag/resilient.py`, 193 lines)

**Class:** `ResilientRAGPipeline`

Wraps `run_generation_pipeline()` with a circuit breaker for fault tolerance.

**Circuit breaker state machine:**

```
CLOSED ──(3 consecutive failures)──→ OPEN
  │                                    │
  │ (30s cooldown)                    │
  │                                    │
  └──────── HALF_OPEN ──(success)──→ CLOSED
                    │
                    ├─(failure)──→ OPEN
```

| State | Behavior |
|-------|----------|
| `CLOSED` | Requests pass through normally. Failures counted. |
| `OPEN` | Requests fail fast — returns cached/stale response or raises `CircuitBreakerError`. |
| `HALF_OPEN` | One trial request passes through. Success → CLOSED; failure → OPEN. |

**Constants:**
- `failure_threshold = 3` — consecutive failures to trip.
- `cooldown_seconds = 30.0` — half-open wait.

**Key method:** `query(query, chunks=None, **kwargs)` → `dict` — runs the pipeline behind the breaker, with fallback to the last known-good response or a degraded (stub) response.

### 7.2 Retryable Embedding Client (`app/rag/retryable_embedding_client.py`, 226 lines)

**Class:** `RetryableEmbeddingClient`

Wraps the embedding call (remote or local) with retry logic.

| Attribute | Value |
|-----------|-------|
| `max_attempts` | 3 |
| `backoff_base` | 0.5 (exponential: 0.5, 1.0, 2.0 seconds) |
| `RETRYABLE_STATUSES` | `{408, 429, 500, 502, 503, 504}` |

**Key method:** `embed_text(text)` / `embed_batch(texts)` — retries on transient HTTP failures or embedding service errors. Degrades to local `SentenceTransformer` after remote failures if `RAG_EMBED_REMOTE_FALLBACK` is true.

### 7.3 Redis/Celery URL Fix (`celery_app.py::_normalize_redis_url`)

Appends `?ssl_cert_reqs=CERT_REQUIRED` to `rediss://` URLs, fixing `ValueError: A rediss:// URL must have parameter ssl_cert_reqs...` from Celery's `RedisBackend.__init__`. Also includes `_run_task_inline()` in `app/utils/qstash_client.py` — sync fallback that bypasses the Celery result backend entirely by calling `task.run()` directly. 11 tests in `test_sync_fallback_fix.py` (2026-08-26).

### 7.4 Remote Embedding (`app/rag/retrieval/remote_embedder.py`, 157 lines)

**Class:** `RemoteEmbedClient`

HTTP client for hosted dense embedding inference (Modal `/embed` endpoint).

| Attribute | Value |
|-----------|-------|
| `endpoint` | Modal `https://<ws>--<app>-<label>.modal.run` (root-served, no `/embed` suffix) |
| `mode` | `tei` (default) |
| `timeout` | 5.0s |

**Key method:** `embed(texts)` → `list[list[float]]` — batched POST to `/embed`, returns 768-dim vectors.

**URL normalization:** `_embed_url()` appends `/embed` to the endpoint — **except** for `.modal.run` URLs which serve at root (POSTing to a `/embed` sub-path 404s).

**Fallback chain:** remote → local `SentenceTransformer` (`RAG_EMBED_REMOTE_FALLBACK`, default opt-out=true). On Render Free, must be `false` (torch OOM — 512MB RAM).

**Injection:** `RemoteEmbedClient` is injected into `DenseRetriever.embed_query` when `RAG_EMBED_ENDPOINT` is set.

### 7.5 Pipeline Stamping (M5 A/B)

`RAGQueryLog.pipeline` column — stamps `"legacy"` or `"agent"` per query for the rollout A/B comparison.

| Pipeline | Route | Flag |
|----------|-------|------|
| Legacy | `POST /api/rag/query` | `run_generation_pipeline()` (always) |
| Agent | `POST /api/rag/query/agent` | `RAG_USE_AGENT_PIPELINE` (default false → delegates to legacy) |

Live A/B script: `scripts/ab_agent_vs_legacy.py` (15-question benchmark). Results: gold-hit@10 parity 0.233, latency agent 10.22s vs legacy 10.85s (stub LLM).

---

## 8. LangGraph Agent Pipeline (M3–M5)

### 8.1 Architecture

The agent pipeline (2026-08-16, M3+M4 from `docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md` Part C) is a self-correcting `StateGraph`:

```
classify_query
    │
    ▼
retrieve_chunks
    │
    ▼
generate_answer ──────────────────┐
    │                             │
    ▼                             │
verify_claim ── groundedness ≥ 0.7? ──▶ finalize
    │                             │    (no retry)
    │ NO (groundedness < 0.7)     │
    ▼                            YES
expand_and_retry              ┌─────┘
    │                         │
    ├── retry_count ≥ max_retries? ──► finalize (exhausted)
    │                         │
    │ NO (retry budget left)  │
    ▼                         │
generate_answer (retry) ──────┘
```

### 8.2 State (`app/rag/agent/state.py`, 110 lines)

**Type:** `RAGState` (TypedDict)

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `query` | `str` | — | User question |
| `top_k` | `int` | 10 | Retrieval depth |
| `collection_name` | `str \| None` | None | Per-domain collection override |
| `query_type` | `str` | `"legal"` | Initial query type |
| `retry_count` | `int` | 0 | Current retry iteration |
| `max_retries` | `int` | 3 | Retry budget |
| `groundedness` | `float` | 0.0 | Latest groundedness score |
| `hallucination_detected` | `bool` | False | Whether hallucination was flagged |
| `chunks` | `list[RetrievedChunk]` | `[]` | Retrieved chunks |
| `answer` | `str` | `""` | Generated answer |
| `citations` | `list[Citation]` | `[]` | Answer citations |
| `groundedness_history` | `list[float]` | `[]` | Scores across retries |
| `expanded_queries` | `list[str]` | `[]` | Query expansions attempted |
| `pipeline` | `str` | `"agent"` | Pipeline stamp for A/B |
| `review_payload` | `dict \| None` | None | M5: HITL review data |

`RAGState` is JSON-serializable (required for checkpointers).

### 8.3 Nodes (`app/rag/agent/nodes.py`, 378 lines)

| Node | Input State | Output State | Description |
|------|-------------|--------------|-------------|
| `classify_query_node` | RAGState | RAGState | Runs `QueryClassifier` + `classify_legal_query`. Fallback to `"general"` on failure. |
| `retrieve_chunks_node` | RAGState | RAGState | Runs retrieval pipeline. Uses expanded query if `expanded_queries` present. Retains `query_type`. |
| `evidence_node` | RAGState | RAGState | Applies `apply_stages()` enrichment. Gated by `cfg.evidence_selector`. Flag in `debug`. Error-isolated. |
| `generate_node` | RAGState | RAGState | `GroundedGenerationService().generate()`. Builds context, calls LLM, tracks citations. |
| `verify_node` | RAGState | RAGState | `HallucinationDetector().detect()`. Updates `groundedness`, `hallucination_detected`, `hallucinated_claims`. Appends to `groundedness_history`. |
| `expand_query_node` | RAGState | RAGState | Query expansion on retry: appends `_RETRY_SUFFIXES` (e.g. "explain", "define", "example"). Increments `retry_count`. Adds to `expanded_queries`. |
| `finalize_node` | RAGState | RAGState | Assembles final `RAGResponse` dict. Stamps `pipeline="agent"`. |
| `multi_hop_retrieve_node` | RAGState | RAGState | Multi-hop retrieval for disambiguation queries. *(Note: dead code at lines 353–377 — function body and docstring duplicated after the first `return`. Pre-existing artifact.)* |

### 8.4 Graph (`app/rag/agent/graph.py`, 376 lines)

**Class:** `RAGAgentGraph` — builds and compiles the `StateGraph`.

**Key function:** `build_graph(checkpointer=None)` → `CompiledGraph`

**Routing logic:** `route_after_verify(state)`:
- If `groundedness >= 0.7` → `finalize`.
- If `groundedness < 0.7` AND `retry_count < max_retries` → `expand_and_retry`.
- If `groundedness < 0.7` AND `retry_count >= max_retries` → `finalize` (exhausted retries).

**Groundedness threshold:** `0.7` (agent retry trigger). Separate from sanitizer threshold (`0.50`) and `HallucinationDetector` default behavior.

**Retry config:** `max_retries=3` (from RAGState default), retry increments `retry_count`, re-expands query, re-runs retrieve → generate → verify.

**Checkpointers (M5):**
- `MemorySaver` (default) — in-process, singleton. `RAG_AGENT_CHECKPOINTER=memory`.
- `PostgresSaver` — `RAG_AGENT_CHECKPOINTER=postgres` (uses `DATABASE_URL`). Requires `langgraph-checkpoint-postgres>=3.0` + `psycopg-binary`.

### 8.5 Routes (`app/rag/agent/routes.py`, 218 lines)

| Route | Method | Description |
|-------|--------|-------------|
| `POST /api/rag/query/agent` | FastAPI | **Primary agent entry.** Builds `RAGState`, runs graph. |
| `POST /api/rag/query/agent/resume` | FastAPI | M5: resumes a paused HITL review. Body: `{thread_id, approved}`. `approved=true` → finalize; `approved=false` → expand-and-retry. |

**Route behavior (`POST /api/rag/query/agent`):**
1. `RAG_USE_AGENT_PIPELINE=false` (default) → delegates to `run_generation_pipeline()` (legacy path). `/api/rag/query` is never affected.
2. `RAG_USE_AGENT_PIPELINE=true` → runs the LangGraph `StateGraph`.
3. `RAG_AGENT_HITL=true` → the `review` node (interrupt) pauses the graph between verify and finalize. Route returns `202` with `{"status": "awaiting_review", "thread_id": ..., "review_payload": {...}}`.
4. Resume via `POST /api/rag/query/agent/resume` with `{thread_id, approved}`.

### 8.6 RAG Agent `__init__.py` (`app/rag/agent/__init__.py`, 15 lines)

Exports: `RAGState`, `RAGAgentGraph`, `build_graph`, `route_after_verify`.

---

## 9. Knowledge Graph Integration

### 9.1 KG Schema (`kg/schema.py`, 255 lines)

Defines the Neo4j graph schema for the legal KG:

| Node Labels | Relationships |
|-------------|---------------|
| `Instrument` (58) — Acts, Rules, Notifications | `CONTAINS` → `Provision` |
| `Provision` (1,861) | `SOURCE_OF` → `Chunk` |
| `Chunk` (27,343) | `SUPPORTED_BY`, `CITES`, `CITED_BY` |
| `Concept` (36) | `BELONGS_TO_DOMAIN` → `Domain` |
| `Authority` (18) | `ISSUED_BY` → `Authority` |
| `Domain` (8) | `HAS_CONCEPT`, `HAS_PROVISION` |
| `SupersessionEdge` | `REPEALED_BY`, `SUPERSEDED_BY` |

**Constraints:** UNIQUE on `provision_id`, `chunk_id`, `document_id`, `instrument_id`, `concept_id`, `authority_id`, `domain_name`.
**Range indexes:** on `legal_domain`, `status`, `act_name`, `section_number`.

### 9.2 KG Corpus Ingestion (`kg/corpus_ingestion.py`, 81 lines)

**Class:** `KGCorpusIngestionEngine`

Rebuilds the legal KG from the manifest + Qdrant + FSS DB. Produces:
- 58 instruments / 1,861 provisions / 27,343 chunks.
- Full domain edges + provenance + temporal status.
- Payload ↔ Neo4j `_id_` ↔ Qdrant `chunk_id` verified 1:1.

**Script:** `scripts/build_kg_corpus.py` (71 lines) — CLI entry point. Requires `NEO4J_ALLOW_WRITE=1` (fail-closed write guard).

### 9.3 KG Semantic Enrichment (`kg/enrichment.py`, 101 lines)

**Class:** `LegalSemanticEnricher`

Deterministic rule-based semantic tagging: duty, offence, penalty, prohibition, power.

- 751 evidence-backed edges on 591 provisions.
- Token-scoped prohibition precedence (a `prohibition` tag on a token level overrides broader `duty`/`power` tags at the same scope).
- `--min-confidence` gate filters low-confidence tags.

**Script:** `scripts/enrich_kg_semantics.py` — CLI entry point.

### 9.4 KG Hybrid Expansion (`kg/hybrid.py`, 121 lines)

**Class:** `KGContextExpander` + functions

**Two KG integration modes in `run_generation_pipeline()`:**

| Mode | Flag | Input → Output | Purpose |
|------|------|----------------|---------|
| **Contract fusion** | `RAG_KG_FUSION` | query → provisions → RRF-fused chunks | Query-to-graph (independent of chunk IDs). Significant Recall@10 gain. Equivalent to eval arm G. |
| **Chunk expansion** | `RAG_KG_EXPANSION` | chunk IDs → provisions → RRF-fused chunks | Chunk-to-graph. Supplements retrieved chunks with KG-derived provisions. |

**Mutual exclusion:** If fusion injected provisions (`kg_contract.injected > 0`), the expansion path is skipped — re-fusing both would muddle ordering.

**Functions:**
- `provisions_for_query(query, LegalKGQueries(), limit)` → provisions from the graph (contract mode).
- `provisions_to_retrieved_chunks(provisions, limit)` → `RetrievedChunk` list.
- `KGContextExpander.expand_chunks(chunk_ids)` → `{matched_chunks, provisions, domains, statuses, authorities, error}`.
- `rrf_fuse_chunks(list_of_chunk_lists, rrf_k, top_k)` → RRF-fused `RetrievedChunk` list.

**Best-effort:** Both modes catch all exceptions and return an `error` dict — a missing or unreachable Neo4j keeps the pipeline functional.

### 9.5 KG Queries (`kg/queries.py`)

**Class:** `LegalKGQueries` — Neo4j query builder for the legal KG.

| Method | Cypher pattern |
|--------|----------------|
| `provisions_for_query(query, limit)` | Match provisions by section number / full text, ordered by relevance. |
| `provision_for_section(section_number)` | Direct section lookup. |
| `related_provisions(provision_id, depth)` | Graph traversal (cites, cited_by, supersession). |
| `provisions_by_domain(domain)` | Domain-filtered provision set. |

### 9.6 KG `__init__.py` (`kg/__init__.py`, 54 lines)

Exports: `KGCorpusIngestionEngine`, `LegalSemanticEnricher`, `KGContextExpander`, `LegalKGQueries`, plus all schema constants.

### 9.6 Test Coverage

| Test File | Tests | Covers |
|-----------|-------|--------|
| `test_kg_semantic_enricher.py` | 11 | Deterministic rule tagging, prohibition precedence, min-confidence gate |
| `test_kg_hybrid_expander.py` | 6 | Chunk→provision expansion, RRF fusion, error isolation |

Full KG + multi-domain sweep: 100/100 green (2026-08-16).

---

## 10. Remote Inference Layer

### 10.1 Modal Hosted Inference (2026-08-16, deployed)

Modal hosts zero local models on the Render free tier:

| Service | URL | Output |
|---------|-----|--------|
| Dense embeddings | `https://sumanksaha--embed.modal.run` | 768-dim vectors (`/embed` endpoint, but serves at root — see §10.3) |
| Legal cross-encoder | `https://sumanksaha--rerank.modal.run` | Rerank scores (`/rerank` endpoint, serves at root — see §10.3) |

**Parity verification:** Remote `/rerank` vs local `sumanksaha/Foodmultidomain` checkpoint — correlation −0.821.

**Environment variables:**
- `RAG_EMBED_ENDPOINT` → injected into `DenseRetriever.embed_query` via `RemoteEmbedClient`.
- `RAG_RERANKER_ENDPOINT` → injected into `Reranker`/`EnsembleReranker` via `RemoteRerankClient` as the `encoder`.
- `RAG_RERANKER_MODE` = `tei` (default). **HF Serverless Inference API is decommissioned** (api-inference.huggingface.co → 410/404 since late 2025) — `mode="serverless"` is a dead end.
- `RAG_RERANKER_TOKEN` / `RAG_EMBED_TOKEN` — Bearer tokens.
- `RAG_RERANKER_TIMEOUT` / `RAG_EMBED_TIMEOUT` — per-request timeouts (default 5.0s).
- `RAG_RERANKER_REMOTE_FALLBACK` / `RAG_EMBED_REMOTE_FALLBACK` — lazy local fallback (default opt-out=true; **must be `false` on Render Free** to prevent torch OOM).

### 10.2 Qdrant-side BM25 (2026-08-16, deployed)

**Flag:** `RAG_QDRANT_BM25=true`

Uses Qdrant's `Qdrant/bm25` text model via:
- `QdrantStore.search_sparse_text(query, top_k, filters)` — text-only BM25 search (Qdrant computes the sparse vector in-cluster).
- `QdrantStore.hybrid_search_text(dense_vector, query_text, top_k, filters)` — single-round-trip dense + server-BM25 RRF.
- `SparseRetriever.search_sparse_text()` — thin wrapper when `server_bm25=True`.

**Requirements:** qdrant-client >= 1.12, cluster with BM25-in-cluster support. Verified live: penalty query → §50/§51/§58. Free on Qdrant Cloud free tier — no local `fastembed` at query time.

### 10.3 URL Normalization for Modal

Both `RemoteEmbedClient` and `RemoteRerankClient` perform URL normalization:

| URL pattern | Normalization |
|-------------|---------------|
| Ends with `/embed` or `/rerank` | Use as-is |
| Ends with `.modal.run` | **Use as-is** — Modal serves function-specific endpoints at root; POSTing to `/embed` or `/rerank` sub-paths 404s |
| Bare base URL | Append `/embed` or `/rerank` |

This is the `_embed_url()` / `_rerank_url()` logic in `remote_embedder.py` and `remote_reranker.py`.

### 10.4 Fallback Chain

```
Remote embed/rerank call
    │
    ├── Success → return scores
    │
    ├── HTTP 408/429/500/502/503/504 → retry (RetryableEmbeddingClient: 3 attempts, 0.5×2^n backoff)
    │
    ├── Remote endpoint unreachable → local fallback (if RAG_*_REMOTE_FALLBACK=true)
    │     ├── RemoteEmbedClient → SentenceTransformer (all-mpnet-base-v2)
    │     └── RemoteRerankClient → CrossEncoder (cross-encoder/ms-marco-MiniLM-L-6-v2)
    │
    └── Local fallback also unavailable → degrade
          ├── Embedder unavailable → sparse-only retrieval
          └── Reranker unavailable → sec_act features-only ranking
```

---

## 11. Configuration & Feature Flags

All RAG configuration resolves through **`app/shared/config.py`** (`cfg`), the single declaration table. Pattern A: Flask config wins inside an app context; `os.environ` outside; else the declared default. `create_app()` calls `seed_config_from_env(app)` so env vars behave identically in-context.

### 11.1 Feature Flag Reference

| Flag | `cfg.attr` | Type | Default | Boolean Convention | Purpose |
|------|-----------|------|---------|---------------------|---------|
| `RAG_ENABLED` | `rag_enabled` | bool | `True` | opt-out | Master switch for the RAG module (503/404 when off). |
| `RAG_USE_AGENT_PIPELINE` | `use_agent_pipeline` | bool | `False` | opt-in | LangGraph agent pipeline on `POST /api/rag/query/agent` (M3). |
| `RAG_AGENT_HITL` | `agent_hitl` | bool | `False` | opt-in | M5 review interrupt before finalize. |
| `RAG_AGENT_CHECKPOINTER` | `agent_checkpointer` | str | `"memory"` | — | M5 checkpointer: `memory` or `postgres`. |
| `RAG_HALLUCINATION_DETECTOR` | `hallucination_detector` | bool | `True` | opt-out | Phase 3 claim-level `HallucinationDetector` in `run_generation_pipeline`. |
| `RAG_RETRIEVAL_CACHE` | `retrieval_cache` | bool | `False` | opt-in | Memoize deterministic retrieval (TTL+LRU). |
| `RAG_QDRANT_BM25` | `qdrant_bm25` | bool | `False` | opt-in | Qdrant-side BM25 sparse inference at query time. |
| `RAG_LEGAL_QUERY_TYPING` | `legal_query_typing` | bool | `True` | opt-out | Rule-based legal query-type classifier feeding reranker weights. |
| `RAG_IDENTIFIER_ROUTE` | `identifier_route` | bool | `True` | opt-out | Lexical `{Act} section {N}` parallel retrieval arm (V5-validated, +13.3pp). |
| `ENABLE_EVIDENCE_SELECTOR` | `evidence_selector` | bool | `False` | opt-in | Evidence-set selection over top-K. |
| `ENABLE_REFERENCE_EXPANSION` | `reference_expansion` | bool | `False` | opt-in | Reference-graph candidate expansion. |
| `ENABLE_LEGAL_IDENTITY` | `legal_identity` | bool | `True` | opt-out | Canonical legal-identity parsing of retrieved chunks. |
| `RAG_CE_SECTION_PREFIX` | `ce_section_prefix` | bool | `False` | opt-in | Prefix CE passages with `§<section>` identity before scoring (CV2 P1). |
| `RAG_ENSEMBLE_RERANK` | `ensemble_rerank` | bool | `True` | opt-out | sec_act + CE ensemble reranker (default on; false = plain `Reranker`). |
| `RAG_RERANKER_MODEL` | `reranker_model` | str | `"cross-encoder/ms-marco-MiniLM-L-6-v2"` | — | Cross-encoder model name. |
| `RAG_RERANKER_ENDPOINT` | `reranker_endpoint` | str | `""` | — | Remote TEI `/rerank` URL; empty = local CE. |
| `RAG_RERANKER_TOKEN` | `reranker_token` | str | `""` | — | Bearer token for remote `/rerank`. |
| `RAG_RERANKER_MODE` | `reranker_mode` | str | `"tei"` | — | Remote CE backend: `tei` (default). Never `serverless` (decommissioned). |
| `RAG_RERANKER_TIMEOUT` | `reranker_timeout` | float | `5.0` | — | Per-request `/rerank` timeout (seconds). |
| `RAG_RERANKER_REMOTE_FALLBACK` | `remote_rerank_fallback` | bool | `True` | opt-out | Lazy local-CE fallback when remote endpoint fails. |
| `RAG_ENSEMBLE_CE_HEAD` | `ensemble_ce_head` | int | `30` | — | Post-sec_act head size the CE scores. |
| `RAG_ENSEMBLE_CE_WEIGHT` | `ensemble_ce_weight` | float | `0.5` | — | Bonus weight for normalized CE head scores. |
| `RAG_EMBED_ENDPOINT` | `embed_endpoint` | str | `""` | — | Remote `/embed` URL (Modal); empty = local `SentenceTransformer`. |
| `RAG_EMBED_TOKEN` | `embed_token` | str | `""` | — | Bearer token for remote `/embed`. |
| `RAG_EMBED_TIMEOUT` | `embed_timeout` | float | `5.0` | — | Per-request `/embed` timeout (seconds). |
| `RAG_EMBED_REMOTE_FALLBACK` | `embed_remote_fallback` | bool | `True` | opt-out | Lazy local-embedder fallback when remote fails. |
| `RAG_EMBEDDING_MODEL` | `embedding_model` | str | `"sentence-transformers/all-mpnet-base-v2"` | — | Dense embedding model (768-dim). |
| `RAG_QDRANT_URL` | `qdrant_url` | str | `""` | — | Qdrant server URL. |
| `RAG_QDRANT_API_KEY` | `qdrant_api_key` | str | `""` | — | Qdrant Cloud API key. |
| `RAG_QDRANT_COLLECTION` | `qdrant_collection` | str | `"fssai_legal_768"` | — | Default Qdrant collection. |
| `RAG_QDRANT_COLLECTION_ENV` | `qdrant_collection_env` | str | `"env_legal_768"` | — | Per-domain collection override (environmental). |
| `RAG_QDRANT_COLLECTION_COMMERCIAL` | `qdrant_collection_commercial` | str | `"commercial_legal_768"` | — | Per-domain collection (commercial). |
| `RAG_QDRANT_COLLECTION_ANIMAL` | `qdrant_collection_animal` | str | `"animal_legal_768"` | — | Per-domain collection (animal husbandry). |
| `RAG_QDRANT_COLLECTION_WB_STATE` | `qdrant_collection_wb_state` | str | `"wb_state_legal_768"` | — | Per-domain collection (WB state laws). |
| `RAG_QDRANT_COLLECTION_CRIMINAL` | `qdrant_collection_criminal` | str | `"criminal_legal_768"` | — | Per-domain collection (criminal / BNS). |
| `RAG_VECTOR_SIZE` | `vector_size` | int | `768` | — | Embedding vector dimension. |
| `RAG_SPARSE_MODEL` | `sparse_model` | str | `"Qdrant/bm25"` | — | Fastembed sparse model for BM25 vectors. |
| `ENABLE_SPARSE` | `enable_sparse` | bool | `True` | opt-out | Upsert BM25 sparse vectors at ingestion. |
| `RAG_FULL_ENRICHMENT` | `full_enrichment` | bool | `False` | opt-in | Full Phase 2 enrichment adapter chain at ingestion. |
| `RAG_USE_STUB_LLM` | `use_stub_llm` | bool | `False` | opt-in | Stub LLM mode for grounded generation (tests / offline). |
| `RAG_KG_EXPANSION` | `kg_expansion` | bool | `False` | opt-in | Expand retrieved chunk IDs through Neo4j KG. |
| `RAG_KG_FUSION` | `kg_fusion` | bool | `False` | opt-in | RRF-fuse KG provisions into retrieved context (query→graph). |
| `RAG_KG_MAX_PROVISIONS` | `kg_max_provisions` | int | `5` | — | Max KG provisions injected into LLM context. |
| `RAG_TORCH_THREADS` | `torch_threads` | int | `4` | — | Torch intra/inter-op thread cap for RAG inference. |
| `RAG_ENABLE_INGESTION_SCHEDULE` | `enable_ingestion_schedule` | bool | `False` | opt-in | Register daily QStash corpus-ingestion schedule at startup. |
| `RAG_INGESTION_CRON` | `ingestion_cron` | str | `"0 3 * * *"` | — | Cron expression for corpus ingestion (daily 03:00 UTC). |

### 11.2 Boolean Convention Semantics

The `cfg` resolution engine fixes the historical `bool("false") is True` trap:
- **opt-in** (default `True`): the raw string must be exactly `"true"` to enable. `"1"`, `"yes"`, `"false"` → `False`.
- **opt-out** (default `False`): any string except `"false"` enables. `"false"` → `False`.

Each `Setting` in `_TABLE` declares its own convention via the `opt_in` field. String `"false"` in Flask config always parses to `False` regardless of convention.

### 11.3 Dynamic Key Resolution

For keys not in the static table, `cfg.get_str(key, default)` and `cfg.get_bool(key, default, opt_in=...)` resolve dynamically using the same Pattern A rule. Used for per-category plugin keys (e.g. `{CATEGORY}_PROVIDER`).

---

## 12. Data Model

### 12.1 ORM Models (`app/models/rag.py`, 192 lines)

| Model | Table | Purpose |
|-------|-------|---------|
| `LegalDocument` | `legal_documents` | Source document metadata (title, act_name, type, domain, content_hash, is_current). |
| `LegalChunk` | `legal_chunks` | Indexed chunk rows. FK→`LegalDocument`. `source_hash` (SHA-256). UNIQUE on `chunk_id`. |
| `RAGQueryLog` | `rag_query_log` | Hash-chained audit of every query: query_hash, result_hash, previous_hash, retrieval_latency_ms, token_count, pipeline (`"legacy"`/`"agent"`), collection_name. |
| `RAGEvalResult` | `rag_eval_results` | Per-query evaluation results from `EvalStorage`. |
| `RAGEvalDataset` | `rag_eval_datasets` | Named evaluation datasets (frozen benchmarks). |

**Re-export:** All models are importable from `app.models` (`app/models/__init__.py` re-exports).

### 12.2 Dataclasses (Plain, non-ORM)

| Dataclass | File | Purpose |
|-----------|------|---------|
| `Chunk` | `chunker.py` | §5.1 payload schema — input to the indexer. |
| `RetrievedChunk` | `result.py` | Chunk from retrieval — `chunk_id`, `score`, `text`, `section_number`, `clause_number`, `document_title`, `act_name`, `document_type`, `authority`, `chunk_index`, `hierarchy_level`, `parent_chunk_id`. |
| `SearchResult` | `result.py` | Retrieval result: `query`, `query_type`, `chunks`, `total`, `latency_ms`, `source`, `error`. |
| `Citation` | `result.py` | Citation: `chunk_id`, `section_number`, `document_title`, `document_type`, `authority`, `url`, `snippet`, `confidence`. |
| `RAGResponse` | `result.py` | Full response: `query`, `query_type`, `answer`, `citations`, `retrieved_chunks`, `groundedness_score`, `hallucination_detected`, `hallucinated_claims`, `confidence`, latencies, `token_usage`, `debug`. |
| `BuiltContext` | `context_builder.py` | Assembled context: `chunks`, `citations_map`, `total_chars`. |
| `GroundedLLMResponse` | `llm_client.py` | LLM response: `query`, `query_type`, `answer`, `citations`, `retrieved_chunks`, `groundedness_score`, `llm_model`, `prompt_tokens`, `completion_tokens`, `generation_latency_ms`, `debug`. |
| `SanitizedResponse` | `sanitizer.py` | Sanitized answer + flags. |
| `GroundednessScore` | `scorer.py` | `score` + `details`. |
| `HallucinationReport` | `hallucination_detector.py` | `detected`, `claims`, `verified_claims`, `unverified_claims`, `hallucinated_claims`, `groundedness_score`, `confidence`, `llm_verified`. |
| `EvalScore` | `metrics.py` | `metric_name`, `score`, `details`. |
| `EvalReport` | `report.py` | Full per-entry evaluation breakdown. |
| `EvalSummary` | `report.py` | Aggregate statistics (mean/min/max/std per metric). |
| `RAGState` | `agent/state.py` | TypedDict — LangGraph agent state (JSON-serializable). |
| `ReviewPayload` | `agent/state.py` | M5 HITL review data (`query`, `answer`, `chunks`, `citations`, `groundedness`). |
| `ChunkIngestionResult` | `chunker.py` | `chunk_count`, `points_upserted`, `errors`. |
| `QueryTypeConfig` | `legal_query_classifier.py` | Per-type reranker weight overrides. |

### 12.3 §5.1 Chunk Payload Schema

Every chunk in Qdrant carries this payload (emitted by `Chunk.to_payload()`):

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | str (UUID) | Unique chunk identifier. |
| `document_id` | str (UUID) | FK to source `LegalDocument`. |
| `document_uri` | str | Source document URI/path. |
| `document_title` | str | Title (e.g. "Food Safety and Standards Act, 2006"). |
| `document_type` | str | Act / Rule / Notification / etc. (enum). |
| `authority` | str | Issuing authority. |
| `jurisdiction` | str | Jurisdiction (FSSAI, State, Central). |
| `state` | str | State code (for state laws). |
| `act_name` | str | Act name (added Phase 1). |
| `effective_date` | date | When the provision takes effect. |
| `enactment_date` | date | When the provision was enacted. |
| `amended_date` | date | Last amendment date. |
| `is_current` | bool | Whether the provision is in force. |
| `chunk_index` | int | Sequential index within document. |
| `chunk_text` | str | The chunk text content. |
| `chunk_char_count` | int | Character count. |
| `section_number` | str | Section number (e.g. "33", "39(b)"). |
| `section_title` | str | Section title. |
| `subsection` | str | Subsection reference. |
| `hierarchy_level` | int | 1=document root, 2=chapter, 3=section, 4=subsection, 5=clause. |
| `parent_chunk_id` | str | Parent chunk (for tree navigation). |
| `citations` | list[str] | §-citation references within the chunk. |
| `references` | list[str] | Cross-reference section numbers. |
| `entities` | list[str] | Entity names (plain string list on payload; structured form in model). |
| `confidence` | float | Extraction confidence (0.0–1.0). |
| `created_at` | datetime | Timestamp. |
| `embedding_model` | str | Model used for embedding. |
| `content_hash` | str (SHA-256) | Normalized content hash for dedup. |

**Identity stamping (2026-08-11):** Additionally carries `provision_id`, `instrument_id`, `legal_domain`, `status` — stamped onto 100% of live points. Payload ↔ Neo4j `provision_id` verified 1:1. 24 payload keyword indexes created.

---

## 13. Task Orchestration

### 13.1 Celery Task Wrappers (`app/rag/tasks.py`, 647 lines)

All RAG background tasks are registered with Celery (`bind=True`, name `rag.<name>`) when the Celery instance is available; otherwise they remain plain functions (graceful degradation).

| Task Function | Plain Entry | Celery Name | Purpose |
|---------------|-------------|-------------|---------|
| `retrieve_task` | `run_retrieval_pipeline()` | `rag.retrieve_task` | Async retrieval pipeline (QStash dispatchable). |
| `embed_and_index_task` | `run_embed_and_index()` | `rag.embed_and_index_task` | Async chunk→embed→index for a single document. |
| `ingest_corpus_task` | `run_ingest_corpus()` | `rag.ingest_corpus_task` | Async batch corpus ingestion (scans `corpus_dir`). |
| `generate_task` | `run_generation_pipeline()` | `rag.generate_task` | Async generation pipeline. |
| `evaluate_task` | `run_evaluate()` | `rag.evaluate_task` | Async batch evaluation. |

### 13.2 Plain (Non-Celery) Entry Points

| Function | Purpose |
|----------|---------|
| `run_retrieval_pipeline(query, top_k, collection_name, filters, pipeline, cache)` | Phase 1 retrieval orchestration (QueryClassifier → HybridRetriever → Reranker → Logger → Stages). Returns JSON-serializable dict. |
| `run_generation_pipeline(query, chunks, query_type, top_k, collection_name, filters, pipeline)` | Phase 2–5 generation orchestration. If `chunks=None`, runs retrieval first. Handles KG fusion/expansion. Runs HallucinationDetector. Returns response dict. |
| `run_embed_and_index(document_id, text, document)` | Single-document ingestion via `QdrantIndexer`. |
| `run_ingest_corpus(corpus_dir, document)` | Batch directory ingestion via `ingest_corpus_dir()`. |
| `run_evaluate(dataset, pipeline_fn, eval_run_id, top_k)` | Batch evaluation via `EvalRunner`. |

### 13.3 Scheduled Jobs

| Schedule | Flag | Registered By | Purpose |
|----------|------|---------------|---------|
| Daily 03:00 UTC | `RAG_ENABLE_INGESTION_SCHEDULE` | `app/__init__.py` via `ScheduledJobs` | Corpus ingestion (`rag.ingest_corpus_task`). |

### 13.4 Retrieval Cache Shims

| Function | Purpose |
|----------|---------|
| `clear_retrieval_cache()` | Drops all cached retrieval results (admin re-ingest, tests). |
| `_retrieval_cache_enabled()` | Backward-compat shim — delegates to `_default_cache.max_size > 0 and cfg.retrieval_cache`. |
| `_retrieval_cache_key(...)` | Builds a hashable cache key from query inputs. |

---

## 14. Corpus Inventory

### 14.1 Live Corpus (Qdrant Cloud)

| Collection | Act/Domain | Documents | Chunks | Status |
|------------|-----------|-----------|--------|--------|
| `fssai_legal_768` | Food Safety & Standards Act | 29 | 12,819 | ✅ Rebuilt 2026-08-11 from FSS DB (P1-4 re-ingest) |
| `criminal_legal_768` | BNS (sections 1–358) | — | — | ✅ Multi-domain Phase 1 (2026-08-20) |
| `env_legal_768` | Environmental laws | — | — | ✅ Multi-domain Phase 1 |
| `commercial_legal_768` | Commercial law | — | — | ✅ Multi-domain Phase 1 |
| `animal_legal_768` | Animal husbandry | — | — | ✅ Multi-domain Phase 1 |
| `wb_state_legal_768` | West Bengal state laws | — | — | ✅ Multi-domain Phase 1 |

**Total indexed:** 27,343 chunks (full corpus, identity-stamped with `provision_id`/`instrument_id`/`legal_domain`/`status` on 100% of points).

### 14.2 Identity Verification (Post-Re-ingest Audit)

| Check | Result |
|-------|--------|
| `FOOD_SAFETY` matched | 12,819 / 12,819 |
| Failed | 0 |
| Unexplained | 0 |
| `fssai_db_in_fssai_qdrant` | 12,819 |
| `fssai_qdrant_not_in_db` | 0 |
| Cross-hash collisions | 0 |
| `provision_id` ↔ Neo4j | 1:1 verified |

### 14.3 Neo4j Legal KG

| Node | Count |
|------|-------|
| Instrument | 58 |
| Provision | 1,861 |
| Chunk | 27,343 |
| Concept | 36 |
| Authority | 18 |
| Domain | 8 |

| Relationship | Count |
|--------------|-------|
| Total legal KG rels | 40,081 |
| Semantic enrichment edges | 751 |

**Readiness:** 69/100 (Operational, READY for controlled hybrid retrieval) per `KG_READINESS_AUDIT_POST_REBUILD.md` (was 32/100).

### 14.4 Original Corpus (Audit-Time)

The audit-time snapshot (now superseded by the full rebuild):
- **Documents:** 24 FSSAI domain documents (PDF + DOCX + TXT).
- **Chunks generated:** ~13,104 (1,097 indexed at audit time — now 12,819 after P1-4 re-ingest).

---

## 15. Test Coverage Inventory

> **Reconciliation (2026-08-26):** `pytest --collect-only` yields ~1,970 tests total, of which ~694 are RAG-related. Per-file counts below are from `RAG_AUDIT_REPORT.md` §2 (authoritative audit: 695 RAG tests) and AGENTS.md; some have drifted as phases landed. The live collect is authoritative.

### 15.1 Core RAG Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_qdrant_client.py` | 55 | QdrantStore: connect, upsert, search, delete, scroll_all, health, batch |
| `test_qdrant_indexer.py` | 21 | QdrantIndexer: after_flush hook, retry-once upsert, ChunkIngestion |
| `test_embedding_service.py` | 17 | EmbeddingService: embed_text, batch, dimension validation |
| `test_chunker.py` | 19 | Chunker: LegalParagraphEngine → LegalChunk (§5.1 payload schema) |
| `test_chunk_quality.py` | 12 | ChunkQualityValidator: A-F grading, score aggregation |
| `test_legal_document_model.py` | 8 | LegalDocument/LegalChunk models, UNIQUE constraint, DB hook registration |
| `test_dedup.py` | 12 | ChunkDeduper: SHA-256 normalized hashing, doc/chunk dedup, batch |
| `test_metadata_adapter.py` | 19 | MetadataAdapter: LegalMetadataEngine → §5.1 payload (enum, dates) |
| `test_citation_adapter.py` | 18 | CitationAdapter: §2.3 fixed extractor → §5.1/§5.2 citations |
| `test_crossref_adapter.py` | 14 | CrossRefAdapter: full Act sections → §5.1/§5.2 references |
| `test_entity_extractor.py` | 30 | LegalEntityExtractor: rule → spaCy NER → LLM (entity types, fallback) |

### 15.2 Retrieval Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_query_classifier.py` | 27 | QueryClassifier: section/authority/case_law/jurisdiction parsing |
| `test_dense_retriever.py` | 14 | DenseRetriever: Qdrant search, score_threshold, top-k, filters |
| `test_sparse_retriever.py` | 20 | SparseRetriever: rapidfuzz fuzzy + BM25 fallback, query preprocessing |
| `test_hybrid_retriever.py` | 16 | HybridRetriever: RRF fusion (k=60), score interpolation, ranking |
| `test_reranker.py` | 10 | Reranker: cross-encoder reranking, deterministic fallback, top-k reorder |
| `test_retrieval_logger.py` | 8 | RAGQueryLog: persistence, hash chain, token/latency tracking |
| `test_rag_e2e.py` | 9 | Query → retrieve → log, hash chain, audit chain, end-to-end |

### 15.3 Remote Inference Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_remote_embedder.py` | 18 | RemoteEmbedClient: batched /embed, auth, URL normalization, fallback + DenseRetriever wiring |
| `test_remote_reranker.py` | 24 | RemoteRerankClient: TEI + serverless modes, auth, URL normalization (.modal.run), lazy fallback, factory wiring |
| `test_qdrant_bm25.py` | 13 | Qdrant-side BM25: search_sparse_text/hybrid_search_text, SparseRetriever server_bm25, HybridRetriever text fusion, RAG_QDRANT_BM25 flag |

### 15.4 Generation, Verification & Evaluation Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_rag_generation.py` | 43 | GroundedGenerationService: stub LLM, prompts, citations, sanitization |
| `test_hallucination_detector.py` | 28 | ClaimExtractor, EvidenceVerifier, CitationValidator, GroundednessScorer, HallucinationDetector |
| `test_citation_validator.py` | 6 | CitationValidator standalone (valid/invalid/section-mismatch) |
| `test_token_counter.py` | 10 | TokenCounter: tiktoken + fallback, RAGQueryLog.context_length |
| `test_eval_framework.py` | 37 | All 6 metrics + EvalRunner + EvalStorage + EvalReport/EvalSummary |
| `test_eval_batch.py` | 10 | Batch evaluation: MRR, error isolation, summary aggregation |
| `test_rag_e2e_verification.py` | 6 | Generation → verification → evaluation integration flow |

### 15.5 Resilience Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_resilient_pipeline.py` | 10 | Circuit breaker state machine (closed→open→half-open→closed), fallback |
| `test_retryable_embedding_client.py` | — | RetryableEmbeddingClient: retry on 408/429/5xx, fallback |
| `test_hybrid_vs_dense.py` | 7 | RRF hybrid vs dense-only retrieval quality comparison |

### 15.6 Ingestion Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_ingestion_pipeline.py` | 27 | IngestionPipeline: full e2e, real-loader, corpus batch, fault isolation |
| `test_ingest_corpus_cli.py` | 11 | Corpus CLI: schedule wiring, batch progress, config validation |
| `test_batch_ingestion.py` | 6 | QStash ingest_corpus schedule + batch progress tracking |
| `test_reindexing.py` | 3 | Delete + re-index after content changes (version-aware) |
| `test_rag_benchmarks.py` | 17 | Benchmark harness: chunking, embedding, Qdrant search latency |
| `test_legal_ocr.py` | 18 | LegalDocumentOCR: PDF image extraction, EasyOCR, text reconstruction |
| `test_rag_backup.py` | 0 | *(exists but file is a backup script — not a test)* |

### 15.7 Agent Pipeline Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_rag_agent_state.py` | 5 | RAGState TypedDict schema, initial_state defaults, JSON-serializability |
| `test_rag_agent_nodes.py` | 17 | classify (fallback→general), retrieve, evidence (flag on/off/error), generate, verify, expand_query (retry count, stub-LLM, failure), finalize merge |
| `test_rag_agent_graph.py` | 12 | compile, node set, evidence-node flag, route_after_verify (threshold/retry budget), e2e grounded/retry/exhaust-retries flows |
| `test_rag_agent_routes.py` | 7 | /api/rag/query/agent 400 validation, 503 RAG-disabled, flag-off delegation to legacy, flag-on agent path, collection/filters forwarding |
| `test_rag_agent_m5.py` | 15 | review interrupt payload/resume value, checkpointer selection (memory/none/postgres-degrades), approved→finalize, rejected→retry→finalize, route 202→resume 200, resume validation/flag-off 400s |

### 15.8 KG / Enrichment Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_kg_semantic_enricher.py` | 11 | Deterministic rule tagging, prohibition precedence, min-confidence gate |
| `test_kg_hybrid_expander.py` | 6 | Chunk→provision expansion, RRF fusion, error isolation |
| `test_enrichment_deterministic.py` | 23 | Enrichment: deterministic §5.1 field extraction |
| `test_enrichment_eval.py` | 21 | Enrichment evaluation: F1/precision/recall against ground truth |
| `test_enrichment_audit.py` | 10 | Enrichment audit trail: field-level tracking, hash chain |
| `test_neo4j_kg_sync.py` | 15 | Neo4j: config detection, real connection, APOC push, sync task, route async/sync |
| `test_multidomain_phase1.py` | 37 | Multi-domain Phase 1: legal_sections registry, collections, act_name payload, act-aware crossrefs, domain prompts, generic claims, collection threading |

### 15.9 Re-ingestion Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_reingest_fssai.py` | 15 | Re-ingest script: load_corpus, build_payload identity, FSS-scope/backup guards, CLI exit semantics (offline, fakes) |

### 15.10 Route & Smoke Tests

| Test File | Tests | Component |
|-----------|-------|-----------|
| `test_rag_routes.py` | 15 | Route: /api/rag/query, /api/rag/generate, /api/rag/eval, /api/rag/health |
| `test_rag_tasks.py` | 7 | Celery tasks: retrieve_task, embed_and_index_task, ingest_corpus_task |
| `test_rag_smoke.py` | 9 | Smoke: Qdrant ping, embed dims, chunk schema, search, classify |
| `test_shared_config.py` | — | Config seam: Pattern A resolution, boolean conventions, docs parity |

### 15.11 A/B Testing

**`scripts/ab_agent_vs_legacy.py`** — Live 15-question A/B benchmark:
- Gold-hit@10 parity: 0.233
- Latency: agent 10.22s vs legacy 10.85s (stub LLM).
- Quality gate: needs `OPENROUTER_API_KEY` post-deploy for meaningful quality comparison.

---

## 16. Infrastructure & Deployment

### 16.1 External Services

| Service | Purpose | Required Env Vars |
|---------|---------|-------------------|
| Qdrant Cloud | Vector + sparse (BM25) store | `RAG_QDRANT_URL`, `RAG_QDRANT_API_KEY` |
| Neo4j Aura | Legal KG graph | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` |
| PostgreSQL | Primary DB (ORM models) | `DATABASE_URL` |
| Redis | Celery broker | `REDIS_URL` |
| Modal | Hosted embed/rerank inference | `RAG_EMBED_ENDPOINT`, `RAG_RERANKER_ENDPOINT`, `RAG_EMBED_TOKEN`, `RAG_RERANKER_TOKEN` |
| OpenRouter | LLM generation | `OPENROUTER_API_KEY` |
| Google Sheets / Airtable | Sync redundancy (Priority 7) | `ENABLE_AIRTABLE_SYNC`, `SPREADSHEET_ID`, `GOOGLE_CREDENTIALS_JSON` |
| R2 / Cloudinary | Photo storage | `CLOUDINARY_*`, `R2_*` |

### 16.2 Deployments

| Component | Platform | Port | Command |
|-----------|----------|------|---------|
| Flask app + RAG blueprint | Render | 8000 | `python app.py` |
| FastAPI ASGI gateway | Render | 8000 | `uvicorn asgi:app --reload` |
| Modal embed/rerank | Modal | — | `modal_deploy/app.py` |
| Celery workers | Render | — | `celery -A celery_app worker` |

### 16.3 RAG in Production (Render)

- RAG routes are live at `/api/rag/*`.
- Celery tasks are wired into `celery_app.py`.
- QStash schedules daily corpus ingestion at 03:00 UTC via `app/__init__.py` (`ScheduledJobs`).
- `RAG_USE_STUB_LLM=false` in production (real LLM via OpenRouter).
- `RAG_EMBED_REMOTE_FALLBACK=false` and `RAG_RERANKER_REMOTE_FALLBACK=false` in production (remote inference is the primary path; local fallback disabled to save memory).

### 16.4 Render Free Compatibility

The full RAG system **cannot run on Render Free** (512MB RAM) because:
- `sentence-transformers` + `torch` ≈ 480MB
- `easyocr` + `onnxruntime` ≈ 120MB
- `fastembed` (sparse) ≈ 80MB
- `spacy` + model ≈ 100MB
- **Total:** ~800MB+ vs 512MB limit.

Additionally, RAG requires Qdrant Cloud, Neo4j Aura, PostgreSQL, and Redis — none available on Render Free. The remote inference layer (Modal) mitigates the embedding/reranking memory cost, but local fallbacks still require the full dependency stack.

### 16.5 Environment Variables (`.env.example`)

The RAG-specific subset:

```
RAG_ENABLED=true
RAG_QDRANT_URL=
RAG_QDRANT_API_KEY=
RAG_QDRANT_COLLECTION=fssai_legal_768
RAG_QDRANT_COLLECTION_ENV=env_legal_768
RAG_QDRANT_COLLECTION_COMMERCIAL=commercial_legal_768
RAG_QDRANT_COLLECTION_ANIMAL=animal_legal_768
RAG_QDRANT_COLLECTION_WB_STATE=wb_state_legal_768
RAG_QDRANT_COLLECTION_CRIMINAL=criminal_legal_768
RAG_VECTOR_SIZE=768
RAG_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
RAG_EMBED_ENDPOINT=
RAG_EMBED_TOKEN=
RAG_EMBED_TIMEOUT=5.0
RAG_EMBED_REMOTE_FALLBACK=true
RAG_RERANKER_ENDPOINT=
RAG_RERANKER_TOKEN=
RAG_RERANKER_MODE=tei
RAG_RERANKER_TIMEOUT=5.0
RAG_RERANKER_REMOTE_FALLBACK=true
RAG_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RAG_ENSEMBLE_RERANK=true
RAG_ENSEMBLE_CE_HEAD=30
RAG_ENSEMBLE_CE_WEIGHT=0.5
RAG_CE_SECTION_PREFIX=false
RAG_LEGAL_QUERY_TYPING=true
RAG_IDENTIFIER_ROUTE=true
RAG_QDRANT_BM25=true
RAG_RETRIEVAL_CACHE=false
RAG_QDRANT_BM25=true
RAG_SPARSE_MODEL=Qdrant/bm25
ENABLE_SPARSE=true
RAG_FULL_ENRICHMENT=false
RAG_USE_STUB_LLM=true
RAG_USE_AGENT_PIPELINE=false
RAG_AGENT_HITL=false
RAG_AGENT_CHECKPOINTER=memory
RAG_HALLUCINATION_DETECTOR=true
RAG_KG_FUSION=false
RAG_KG_EXPANSION=false
RAG_KG_MAX_PROVISIONS=5
RAG_TORCH_THREADS=4
RAG_ENABLE_INGESTION_SCHEDULE=false
RAG_INGESTION_CRON=0 3 * * *
NEO4J_URI=
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
NEO4J_ALLOW_WRITE=
OCR_PROVIDER=easyocr
OCR_LANGUAGES=english,hindi
OCR_USE_GPU=false
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=
OPENAI_API_KEY=
```

---

## 17. Known Issues & Caveats

### 17.1 Dead Code in `app/rag/agent/nodes.py`

The `multi_hop_retrieve_node` function body and its docstring are **duplicated** at lines 353–377 — the second copy is unreachable dead code (inside no indentation block after the first `return`). Pre-existing artifact; noted here for maintainers but not a blocker for understanding the pipeline.

### 17.2 HF Serverless Inference API Decommissioned

The `mode="serverless"` path in `RemoteRerankClient` targets the HF Serverless Inference API (`api-inference.huggingface.co`), which returned 410/404 since late 2025. `RAG_RERANKER_MODE` defaults to `"tei"` — **never set to `"serverless"`**. Use Modal (`/rerank`) or a self-hosted TEI endpoint.

### 17.3 Render Free Tier Constraints

Both `RAG_EMBED_REMOTE_FALLBACK` and `RAG_RERANKER_REMOTE_FALLBACK` must be `false` on Render Free (512MB RAM) — a local `SentenceTransformer` or `CrossEncoder` would OOM the instance. The pipeline degrades to sparse-only retrieval in that configuration.

### 17.4 RAG_USE_STUB_LLM Default

`RAG_USE_STUB_LLM` defaults to `False` (production). For local development and CI without `OPENROUTER_API_KEY`, set `RAG_USE_STUB_LLM=true` to run end-to-end without API calls. All 695 RAG tests run in stub mode (no network required).

### 17.5 AGENTS.md Drift

AGENTS.md states "437 RAG tests" and describes `app/rag/routes.py` as a Flask blueprint. The actual state (confirmed via `pytest --collect-only` and source reading):
- ~694 RAG tests (authoritative: `RAG_AUDIT_REPORT.md` §2 counts 695).
- `app/rag/routes.py` is now 71 lines of FastAPI `APIRouter` with `/query` GET+POST routes calling `route_after_verify(state)` with `RAGState`.

This document is authoritative; AGENTS.md is a high-level status snapshot only.

### 17.6 Ceiling Analysis

The original corpus (audit-time) had ~13,104 chunks generated from 24 documents, but only 1,097 were indexed. After the P1-4 FSSAI re-ingest (2026-08-11), `fssai_legal_768` contains 12,819 chunks. The Qdrant-side BM25 and server-side RRF fusion (2026-08-16) represent the current production retrieval path — the client-side rapidfuzz fallback exists only for environments without a sparse-capable Qdrant store.

---

## 18. Key Constants Reference

| Name | Location | Value |
|------|----------|-------|
| `DEFAULT_RRF_K` | `app/rag/retrieval/rrf.py:30` | `60.0` |
| `UPSERT_BATCH_SIZE` | `app/rag/qdrant_indexer.py` | `100` |
| `RAG_VECTOR_SIZE` | `app/shared/config.py` | `768` |
| `RAG_SPARSE_MODEL` | `app/shared/config.py` | `"Qdrant/bm25"` |
| `_RETRIEVAL_CACHE_TTL` | `app/rag/tasks.py:52` | `600` (seconds) |
| `_RETRIEVAL_CACHE_MAX` | `app/rag/tasks.py:53` | `512` (entries) |
| `_TOKENS_PER_WORD` | `app/rag/verification/token_counter.py` | `1.3` |
| `_CHARS_PER_TOKEN` | `app/rag/verification/token_counter.py` | `4.0` |
| `_GROUNDEDNESS_THRESHOLD` | `app/rag/generation/sanitizer.py` | `0.50` |
| Agent groundedness retry threshold | `app/rag/agent/graph.py` | `0.7` |
| `max_retries` | `app/rag/agent/state.py` (RAGState) | `3` |
| Circuit breaker `failure_threshold` | `app/rag/resilient.py` | `3` |
| Circuit breaker `cooldown_seconds` | `app/rag/resilient.py` | `30.0` |
| `max_context_chars` | `app/rag/generation/context_builder.py` | `12000` |
| `max_context_chunks` | `app/rag/generation/context_builder.py` | `10` |
| EnsembleReranker weights | `app/rag/retrieval/reranker.py` | `_W_SEC=2.0`, `_W_ACT=1.5`, `_W_EXACT=1.0`, `_W_HIERARCHY=0.2` |
| EnsembleReranker CE head/weight | `app/shared/config.py` | `ce_head=30` (`RAG_ENSEMBLE_CE_HEAD`), `ce_weight=0.5` (`RAG_ENSEMBLE_CE_WEIGHT`) |
| `RetryableEmbeddingClient.max_attempts` | `app/rag/retryable_embedding_client.py` | `3` |
| `RetryableEmbeddingClient.backoff_base` | `app/rag/retryable_embedding_client.py` | `0.5` |
| `RetryableEmbeddingClient.RETRYABLE_STATUSES` | `app/rag/retryable_embedding_client.py` | `{408, 429, 500, 502, 503, 504}` |
| Default LLM model | `app/rag/generation/llm_client.py` | `"poolside/laguna-s-2.1:free"` (OpenRouter) |
| Default embedding model | `app/shared/config.py` | `"sentence-transformers/all-mpnet-base-v2"` |
| Default cross-encoder model | `app/shared/config.py` | `"cross-encoder/ms-marco-MiniLM-L-6-v2"` |

---

## 19. Quick Start (Developer)

```bash
# 1. Install dependencies
pip install -e .

# 2. Copy env and configure for local stub-mode RAG
cp .env.example .env
# Set: RAG_USE_STUB_LLM=true (already default for tests)
# Set: RAG_QDRANT_URL + RAG_QDRANT_API_KEY (for live Qdrant)
# Set: OPENROUTER_API_KEY (for real LLM; unset = stub mode)

# 3. Run the full RAG test suite
python -m pytest tests/test_qdrant_client.py tests/test_qdrant_indexer.py \
  tests/test_embedding_service.py tests/test_chunker.py tests/test_dedup.py \
  tests/test_query_classifier.py tests/test_dense_retriever.py \
  tests/test_sparse_retriever.py tests/test_hybrid_retriever.py \
  tests/test_reranker.py tests/test_rag_e2e.py tests/test_rag_generation.py \
  tests/test_hallucination_detector.py tests/test_eval_framework.py \
  tests/test_resilient_pipeline.py tests/test_rag_agent_graph.py \
  tests/test_rag_agent_routes.py -v

# 4. Run retrieval pipeline standalone
python -c "
from app.rag.tasks import run_retrieval_pipeline
result = run_retrieval_pipeline('What are the penalties under FSS Act section 33?')
print(result)
"

# 5. Run generation pipeline standalone (stub LLM)
python -c "
from app.rag.tasks import run_generation_pipeline
result = run_generation_pipeline('What are the penalties under FSS Act section 33?')
print(result['answer'])
"
```

---

## 20. File Tree (RAG-specific)

```
app/rag/
├── __init__.py                      # Package init, lazy loaders
├── chunker.py                       # Chunker + LegalParagraphEngine + Chunk dataclass
├── embedding_service.py            # EmbeddingService (remote + local)
├── qdrant_client.py                # QdrantStore (low-level Qdrant client)
├── qdrant_indexer.py               # QdrantIndexer (after_flush hook, retry-once)
├── ingestion.py                    # IngestionPipeline (full dir ingestion)
├── sparse_embedding.py             # SparseEmbeddingService (BM25 sparse vectors)
├── dedup.py                        # ChunkDeduper (SHA-256 normalized hashing)
├── document_classifier.py          # DocumentClassifier (Act vs Rule)
├── metadata_adapter.py             # MetadataAdapter (§5.1 payload mapping)
├── citation_adapter.py             # CitationAdapter (§2.3 → §5.1 citations)
├── crossref_adapter.py             # CrossRefAdapter (full Act section refs)
├── entity_extractor.py             # LegalEntityExtractor (3-tier: rule→NER→LLM)
├── legal_sections.py               # LEGAL_SECTIONS registry (BNS 1–358)
├── collections.py                  # Domain→collection map (multi-domain Phase 1)
├── legal_ocr.py                    # LegalDocumentOCR (EasyOCR, lazy)
├── chunk_quality.py                # ChunkQualityValidator (A–F grading)
├── ingestion_logger.py             # IngestionLogger (progress + hash chain)
├── backup.py                       # export/import/corpus_backup/restore
├── resilient.py                    # ResilientRAGPipeline (circuit breaker)
├── retryable_embedding_client.py   # RetryableEmbeddingClient (3 retries, backoff)
├── routes.py                       # FastAPI APIRouter (/rag/query GET+POST)
├── tasks.py                        # Celery task wrappers + pipeline entry points
├── torch_runtime.py                # cap_torch_threads() (RAG_TORCH_THREADS)
├── retrieval/
│   ├── __init__.py                 # Exports QueryClassifier, QueryParser, QueryType
│   ├── dense_retriever.py          # DenseRetriever (Qdrant vector search)
│   ├── sparse_retriever.py         # SparseRetriever (BM25 + rapidfuzz fallback)
│   ├── hybrid_retriever.py         # HybridRetriever (RRF fusion, identifier arm)
│   ├── reranker.py                 # Reranker + EnsembleReranker (sec_act + CE)
│   ├── rrf.py                      # reciprocal_rank_fuse (k=60)
│   ├── result.py                   # RetrievedChunk, SearchResult, Citation, RAGResponse
│   ├── identifier.py               # detect_act, detect_section, identifier_query
│   ├── legal_identity.py           # resolve_legal_identity
│   ├── legal_hierarchy.py          # LegalHierarchyExtractor (section tree)
│   ├── provision_versions.py       # ProvisionVersionResolver (amendments)
│   ├── evidence_selector.py        # EvidenceSelector (top-K evidence set)
│   ├── evidence_metrics.py         # EvidenceMetrics (coverage/density/diversity)
│   ├── reference_extractor.py      # ReferenceExtractor (§-citations)
│   ├── reference_graph.py          # ReferenceGraph (cross-ref expansion)
│   ├── temporal_validity.py        # TemporalValidityFilter (repeal/supersession)
│   ├── section_prefix.py           # prefix_passage (§-identity for CE)
│   ├── stages.py                   # apply_stages (post-retrieval enrichment registry)
│   ├── logger.py                   # RetrievalLogger (hash-chained RAGQueryLog)
│   ├── cache.py                    # RetrievalCache (LRU + TTL)
│   ├── factory.py                  # build_hybrid_retriever, build_reranker
│   ├── legal_query_classifier.py   # classify_legal_query, QueryTypeConfig
│   ├── remote_embedder.py          # RemoteEmbedClient (Modal /embed)
│   └── remote_reranker.py          # RemoteRerankClient (Modal /rerank)
├── generation/
│   ├── __init__.py                 # Exports GroundedGenerationService
│   ├── context_builder.py          # ContextBuilder (max 12k chars, 10 chunks)
│   ├── prompt_template.py          # PromptTemplate (domain-parameterized)
│   ├── llm_client.py               # GroundedLLMClient (OpenRouter/stub)
│   ├── grounded_service.py         # GroundedGenerationService (pipeline orchestration)
│   ├── citation_tracker.py         # CitationTracker ([Source n] assignment)
│   ├── sanitizer.py                # ResponseSanitizer (_GROUNDEDNESS_THRESHOLD=0.50)
│   ├── token_counter.py            # TokenCounter (tiktoken + fallback)
│   └── logger.py                   # GenerationLogger (audit + token tracking)
├── verification/
│   ├── __init__.py                 # Exports all verification classes
│   ├── claim_extractor.py          # ClaimExtractor (sentence→claims)
│   ├── evidence_verifier.py        # EvidenceVerifier (claim↔chunk)
│   ├── citation_validator.py       # CitationValidator (chunk/section/snippet)
│   ├── scorer.py                   # GroundednessScorer (aggregate 0–1)
│   ├── hallucination_detector.py   # HallucinationDetector (full chain)
│   └── token_counter.py            # TokenCounter (shared, tiktoken + fallback)
├── evaluation/
│   ├── __init__.py                 # Exports EvalRunner, EvalStorage, metrics
│   ├── metrics.py                  # 6 metrics (Faithfulness, Relevance, etc.)
│   ├── runner.py                   # EvalRunner (batch evaluation orchestration)
│   ├── storage.py                  # EvalStorage (DB persistence)
│   └── report.py                   # EvalReport, EvalSummary (HTML/JSON)
├── agent/
│   ├── __init__.py                 # Exports RAGState, RAGAgentGraph, build_graph
│   ├── state.py                    # RAGState (TypedDict, JSON-serializable)
│   ├── nodes.py                    # 7 node functions (classify→finalize)
│   ├── graph.py                    # RAGAgentGraph (StateGraph + circuit breaker)
│   └── routes.py                   # /api/rag/query/agent + /resume (HITL)
├── retrieval/                      # (see above)
├── rag_enrichment/                 # (Phase 2 enrichment — deterministic.py)
└── ...

kg/
├── __init__.py                     # Exports KG engine classes
├── schema.py                       # Neo4j graph schema (nodes, rels, constraints)
├── corpus_ingestion.py             # KGCorpusIngestionEngine (manifest→graph)
├── enrichment.py                   # LegalSemanticEnricher (750 semantic edges)
├── hybrid.py                       # KGContextExpander + RRF fusion functions
├── queries.py                      # LegalKGQueries (Cypher query builder)
├── validation.py                   # KG validation checks
├── concept_linking.py              # Concept→provision linking
├── domain_manifest.py              # Multi-domain manifest registry
├── payload_identity.py             # Qdrant payload identity stamping
└── ingestion.py                    # KG ingestion orchestration

app/models/rag.py                   # LegalDocument, LegalChunk, RAGQueryLog, RAGEvalResult, RAGEvalDataset
app/shared/config.py                # cfg — single config seam (_TABLE declaration, Pattern A)
scripts/
├── ingest_corpus.py                # CLI: single-document ingestion
├── reingest_fssai_from_db.py       # P1-4: rebuild fssai_legal_768 from DB
├── build_kg_corpus.py              # CLI: rebuild legal KG from manifest+Qdrant+DB
├── enrich_kg_semantics.py          # CLI: semantic enrichment (duty/offence/etc.)
├── export_fssai_backup.py          # Export pre-reingest Qdrant backup
├── benchmark_rag.py                # Timing harness (chunking/embedding/search)
└── ab_agent_vs_legacy.py           # A/B pipeline comparison (agent vs legacy)
```

---

## 21. Phase Timeline

| Phase | Date | Key Deliverables | Test Count |
|-------|------|-----------------|------------|
| Phase 1 (Retrieval Foundation) | 2026-08-08 | Qdrant store, chunker, embedding, indexer, ingestion, dedup, adapters, document classifier | 117 |
| Phase 2 (Grounded Generation) | 2026-08-09 | ContextBuilder, PromptTemplate, GroundedLLMClient, CitationTracker, ResponseSanitizer, GroundedGenerationService | 40 (`test_rag_generation.py`) |
| Phase 3 (Verification) | 2026-08-09 | ClaimExtractor, EvidenceVerifier, CitationValidator, GroundednessScorer, HallucinationDetector, TokenCounter | 48 (28+6+10) |
| Phase 4 (Evaluation) | 2026-08-09 | Faithfulness, AnswerRelevance, ContextPrecision, ContextRecall, CitationRecall, Groundedness metrics; EvalRunner, EvalStorage, EvalReport | 49 (39+10) |
| Phase 5 (Integration) | 2026-08-09 | ResilientRAGPipeline, circuit breaker, RetryableEmbeddingClient, full pipeline, token counting on hot path | 31 (6+10+7+6+2) |
| Agent A §6.2 | 2026-08-09 | Corpus ingestion e2e, batch ingestion, reindexing tests | 17 (8+5+3+1) |
| Remote Inference | 2026-08-16 | Modal /embed + /rerank, URL normalization, Qdrant-side BM25 | 55 (18+24+13) |
| LangGraph Agent M3 | 2026-08-16 | StateGraph: classify → retrieve → generate → verify → retry | 41 |
| LangGraph Agent M5 | 2026-08-16 | HITL review interrupt, checkpointing (memory/postgres), A/B stamping | 15 |
| Multi-Domain Phase 1 | 2026-08-20 | De-FSSAI: per-act section registry, domain collections, act-aware prompts | 37 |
| KG Option B + Semantic + Hybrid | 2026-08-11 | Corpus KG rebuild (27,343 chunks), 751 semantic edges, hybrid expansion | 17 |
| P1-4 FSSAI Re-ingest | 2026-08-11 | Identity-preserving rebuild from DB (12,819 chunks) | 15 |
| CE_RERANK_REVIEW | 2026-08-14 | EnsembleReranker (sec_act + CE), query-type-aware weights | 10+ |
| Identifier Route (V5.5) | 2026-08-13 | Lexical identifier-query arm (+13.3pp) | — |
| Claim-level Hallucination Detector | 2026-08-23 | Phase 3 on live generation path | — |

**Total RAG tests (authoritative):** ~694–695 (per `RAG_AUDIT_REPORT.md` §2 and `pytest --collect-only` 2026-08-20). All passing.

---

## 22. External Documentation References

| Document | Path | Purpose |
|----------|------|---------|
| AGENTS.md | `AGENTS.md` | High-level status (may be drifted — use this doc as authoritative) |
| RAG Audit Report | `RAG_AUDIT_REPORT.md` | Full test inventory (695 tests), corpus audit, feature matrix |
| FastAPI Implementation Plan | `FASTAPI_IMPLEMENTATION_PLAN.md` | ASGI gateway phases 1–5 |
| Multi-Domain Integration | `docs/MULTIDOMAIN_INTEGRATION.md` | Phase 1 multi-domain plan |
| RAG Agent A Scope | `RAG_AGENT_A_SCOPE.md` | §5.1 payload schema, retrieval contract |
| RAG Agent B Scope | `RAG_AGENT_B_SCOPE.md` | §12.4 stages, retrieval architecture |
| HF Hosting + LangGraph Plan | `docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md` | Part A/B/C: Modal hosting, remote clients, agent pipeline |
| KG Readiness Audit | `KG_READINESS_AUDIT_POST_REBUILD.md` | Post-rebuild readiness (69/100) |
| FSSAI Re-ingest Plan | `docs/FSSAI_REINGEST_PLAN.md` | P1-4 re-ingest methodology |
| Corpus Identity Report | `CORPUS_IDENTITY_REPORT.md` | §8 identity verification (12,819/12,819) |
| CE RERANK Review | `docs/CE_RERANK_REVIEW.md` | EnsembleReranker evaluation findings |
| RAG Current Architecture | `RAG_CURRENT_ARCHITECTURE.md` | 2026-08-07 reconstruction (legacy context) |
| Legal AI Implementation | `Legal_AI_implementation.md` | §12.1 cache, §12.4 stages design |
| Work Diary Research | `docs/WORKDIARY_RESEARCH.md` | (FSO work diary — not RAG-specific) |

---

*This document was compiled by direct source code reading of 40+ files across `app/rag/`, `kg/`, `app/models/rag.py`, `app/shared/config.py`, `app/rag/tasks.py`, `app/rag/routes.py`, `scripts/`, `RAG_AUDIT_REPORT.md`, and `RAG_CURRENT_ARCHITECTURE.md`. It is faithful to the code as of commit `2b98a82` (2026-08-26).*


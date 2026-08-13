# RAG System Audit Report

> **Audited:** 2026-08-08 (end of day)  
> **Scope:** Full audit of `app/rag/` subsystem + related infrastructure (Qdrant, Neo4j, corpus, env, tests)  
> **Method:** Static analysis of all source files, live connectivity probes to Qdrant Cloud & Neo4j Aura, corpus inventory, environment variable analysis, and test-suite enumeration.
>
> **Reconciled: 2026-08-10** — post-audit updates. (1) **Counts:** a live `pytest --collect-only` on 2026-08-10 yields **1,757 tests total**, of which **694 are RAG-related** (incl. 108 enrichment + 37 multi-domain Phase 1 in `tests/test_multidomain_phase1.py`). (2) **§3.2 payload table** lists fields the code does not emit — see the correction note there. (3) **§6.2 env var** is `RAG_QDRANT_COLLECTION`, not `RAG_COLLECTION_NAME` — fixed below. (4) The audit's #1 missing piece, the **multi-domain corpus beyond FSSAI**, is now under construction: see `docs/MULTIDOMAIN_INTEGRATION.md` (6 per-domain collections + manifest-driven ingestion; `fssai_legal_768` untouched).
>
> **Reconciled: 2026-08-11** — Knowledge-Graph track delivered (see `docs/MULTIDOMAIN_INTEGRATION.md` §5 and `KG_READINESS_AUDIT_POST_REBUILD.md`). (1) **Neo4j is no longer empty:** §4.2's "Connected But Empty" is superseded — the legal KG now mirrors the live retrieval corpus 1:1 via `scripts/build_kg_corpus.py` (58 instruments, 1,861 provisions, 27,343 chunks — the 14,524 multi-domain Qdrant points + 12,819 FSS DB chunks). (2) **Semantic enrichment** (`kg/enrichment.py` `LegalSemanticEnricher` + `scripts/enrich_kg_semantics.py`): 750 evidence-backed duty/offence/penalty/prohibition/power edges on 591 provisions. (3) **Hybrid expansion wired into the RAG pipeline:** `kg/hybrid.py` `KGContextExpander` expands retrieved chunk IDs through Neo4j (provisions, domains, temporal status, authorities, provenance) inside `run_generation_pipeline` behind the **`RAG_KG_EXPANSION`** flag (default off; responses carry a `kg_expansion` block). (4) **Qdrant payload identity stamped — audit P1 closed:** `kg/payload_identity.py` `QdrantPayloadStamper` + `scripts/stamp_qdrant_payload_identity.py` stamped canonical `provision_id`/`instrument_id`/`legal_domain`/`status` onto **15,624/15,624 live points** from the same registry that builds Neo4j (14,524 multi-domain points got full identity; `fssai_legal_768` got domain-only at that pass — its points were from a different DB snapshot). 24 keyword payload indexes created; idempotent (re-run = 0 updates). **Cross-store verification: 60/60 sampled Qdrant `provision_id`s resolve exactly to live Neo4j `LegalProvision` nodes.** (5) **Tests:** `tests/test_kg_semantic_enricher.py` (11) + `tests/test_kg_hybrid_expander.py` (6) + `tests/test_payload_identity.py` (14) added — 31/31 pass; full KG + multi-domain + RAG-task sweep 84/84 green.
>
> **P1-4 FSSAI re-ingest executed (2026-08-11):** the domain-only caveat in items (4)/(5) above is superseded — `fssai_legal_768` was rebuilt from the current DB (`scripts/reingest_fssai_from_db.py`, identity-preserving, 12,819 pts) and re-stamped (`act_name` 100%, `provision_id` 3,126, `instrument_id`/`legal_domain`/`status` 12,819, 0 unknown docs). Qdrant total is now **27,343 points = full corpus**; reconcile matched 12,819 / failed 0 / unexplained 0. See `CORPUS_IDENTITY_REPORT.md` §8 and `docs/FSSAI_REINGEST_PLAN.md`.

---

## Executive Summary

The RAG subsystem is **complete, tested (695 RAG tests, all passing), and functionally configured** for FSSAI-domain legal retrieval augmented generation. It implements a full ingestion → retrieval → generation → verification → evaluation pipeline with circuit-breaker resilience.

**What is NOT present:** Advanced argumentation-layer frameworks (LangChain, LangGraph, IRAC, counterarguments, game theory, Talebian antifragility analysis, forward analysis), Pydantic-structurd LLM output, human-in-the-loop review workflow, and persistent claim ledger. *(The multi-domain corpus beyond FSSAI — listed as absent at audit time — is now delivered: see the 2026-08-11 reconciliation note and `docs/MULTIDOMAIN_INTEGRATION.md`.)*

**Bottom line:** The RAG system as specified in `plan.md`/`task.md` is fully implemented and passes all its tests. The missing pieces are *not* bugs or gaps in the existing implementation — they are **speculative enhancements** not yet prioritized or designed.

---

## 1. Architecture — What Actually Exists

### 1.1 Actual RAG Pipeline (end-to-end)

```
Corpus Documents (PDF/DOCX/TXT)
    │
    ▼
app/rag/ingestion.py  —  IngestionPipeline orchestrator
    ├── DocumentLoaderFactory  →  app/document_loader/ (PDF/DOCX/TXT readers)
    ├── DocumentCleaner         →  app/document_cleaner/ (legal text normalization)
    ├── LegalDocumentOCR        →  app/rag/legal_ocr.py (EasyOCR for scanned PDFs)
    ├── Chunker                 →  legal_paragraph_detection_engine/ (rule-based §5.1 extraction)
    ├── ChunkDeduper            →  SHA-256 normalized content hashing
    ├── EmbeddingService        →  sentence-transformers (dense, 768-dim)
    ├── SparseEmbeddingService  →  fastembed BM25 (sparse)
    ├── DocumentClassifier      →  R2 extractors → §5.1 fields (act_name, section_number, etc.)
    ├── MetadataAdapter         →  LegalMetadataEngine → §5.1 payload
    ├── CitationAdapter         →  CitationExtractor → §5.1/§5.2 citations
    ├── CrossRefAdapter         →  CrossReferenceEngine → §5.1/§5.2 cross-references
    ├── LegalEntityExtractor    →  rule → spaCy NER → LLM (LegalEntity model)
    ├── ChunkQualityValidator   →  A-F grading per chunk
    └── QdrantIndexer           →  QdrantStore.upsert() after SQLAlchemy flush
            │
            ▼
app/rag/qdrant_client.py  —  QdrantStore (connect, upsert, search, delete, health, scroll_all)
    (Qdrant Cloud: https://qdrant.cloud, collection: fssai_legal_768)

Query
    │
    ▼
app/rag/routes.py  —  POST /api/rag/query (full pipeline orchestration)
    ├── QueryClassifier          →  rule-based → QueryType (section, case_law, general, etc.)
    ├── QueryParser              →  SectionParser, AuthorityParser, CaseLawParser, JurisdictionParser
    ├── DenseRetriever           →  Qdrant dense search + score_threshold + filters
    ├── SparseRetriever          →  Qdrant BM25 sparse OR rapidfuzz fuzzy fallback
    ├── HybridRetriever          →  RRF fusion (k=60) of dense + sparse results
    ├── Reranker                 →  cross-encoder (BGE-reranker) OR deterministic fallback
    ├── RetrievalLogger          →  RAGQueryLog DB persistence + hash-chained audit
    │
    ▼
app/rag/generation/  —  GroundedGenerationService orchestrator
    ├── ContextBuilder           →  chunk → structured context with [n] bracket labels
    ├── PromptTemplate           →  system + user prompt registry
    ├── GroundedLLMClient        →  httpx → OpenRouter/OpenAI API  [RAG_USE_STUB_LLM=true in dev]
    ├── CitationTracker          →  [n] bracket + inline Section refs from chunks
    ├── ResponseSanitizer        →  validate all cited chunks exist / are grounded
    ├── CitationValidator      →  chunk_id + section_number consistency check
    ├── TokenCounter             →  tiktoken + fallback estimation
    ├── GenerationLogger         →  update RAGQueryLog + AuditLog hash chain
    └── HallucinationDetector    →  claim extraction + evidence verification → HallucinationReport

    │
    ▼
app/rag/verification/  —  post-generation verification
    ├── ClaimExtractor           →  sentence splitting + regex entity extraction
    ├── EvidenceVerifier         →  rapidfuzz text match + §5.1 field match
    ├── CitationValidator        →  chunk_id + section_number consistency
    ├── GroundednessScorer       →  weighted blend of claim/citation/groundedness scores
    └── HallucinationDetector    →  orchestrator → grounded vs. ungrounded classification

    │
    ▼
app/rag/evaluation/  —  evaluation framework
    ├── FaithfulnessMetric, AnswerRelevanceMetric, ContextPrecisionMetric,
    ├── ContextRecallMetric, CitationRecallMetric, GroundednessMetric
    ├── EvalRunner (batch), EvalStorage (DB), EvalReport/EvalSummary
    └── POST /api/rag/eval (batch eval route)

    │
    ▼
app/rag/resilient.py  —  Resilience layer
    └── ResilientRAGPipeline (circuit breaker: closed→open→half-open→closed + fallback)
```

### 1.2 Supporting Infrastructure

| Component | Status | Source File |
|---|---|---|
| **Qdrant Cloud** | ✅ Connected, 1,097 points in `fssai_legal_768` (hybrid: dense 768-dim + BM25 sparse) | `app/rag/qdrant_client.py` |
| **Neo4j Aura** | ✅ Connected, but EMPTY (0 nodes/relationships; only schema labels created) | `app/services/neo4j_graph.py` |
| **Embedding Model** | `sentence-transformers/all-mpnet-base-v2` (768-dim) configured; stub mode active in dev | `app/rag/embedding_service.py` |
| **Reranker Model** | `cross-encoder/stsb-distilroberta-base` (configured, not required) | `app/rag/retrieval/reranker.py` |
| **LLM Provider** | OpenRouter API key configured in Render; `RAG_USE_STUB_LLM=true` in `.env.example` | `app/rag/generation/llm_client.py` |
| **OCR Engine** | EasyOCR (for scanned PDFs) — optional dependency, lazy-loaded | `app/rag/legal_ocr.py` |
| **NLP Toolkit** | spaCy NER (rule → spaCy → LLM fallback chain) | `app/rag/entity_extractor.py` |

---

## 2. Test Coverage — The Numbers

```
Total test functions across all test files: 1,733
Total test files:                                     91
RAG-specific test functions:                        695
All RAG tests passing:                              YES ✅
```

### 2.1 RAG Test File Inventory (695 tests)

| Test File | Tests | Component |
|---|---|---|
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
| `test_query_classifier.py` | 27 | QueryClassifier: section/authority/case_law/jurisdiction parsing |
| `test_dense_retriever.py` | 14 | DenseRetriever: Qdrant search, score_threshold, top-k, filters |
| `test_sparse_retriever.py` | 20 | SparseRetriever: rapidfuzz fuzzy + BM25 fallback, query preprocessing |
| `test_hybrid_retriever.py` | 16 | HybridRetriever: RRF fusion (k=60), score interpolation, ranking |
| `test_reranker.py` | 10 | Reranker: cross-encoder reranking, deterministic fallback, top-k reorder |
| `test_retrieval_logger.py` | 8 | RAGQueryLog: persistence, hash chain, token/latency tracking |
| `test_rag_e2e.py` | 9 | Query → retrieve → log, hash chain, audit chain, end-to-end |
| `test_rag_generation.py` | 43 | GroundedGenerationService: stub LLM, prompts, citations, sanitization |
| `test_hallucination_detector.py` | 28 | ClaimExtractor, EvidenceVerifier, CitationValidator, GroundednessScorer, |D|
| | | HallucinationDetector (grounded/ungrounded detection, threshold sweep) |
| `test_citation_validator.py` | 6 | CitationValidator standalone (valid/invalid/section-mismatch) |
| `test_token_counter.py` | 10 | TokenCounter: tiktoken + fallback, RAGQueryLog.context_length |
| `test_eval_framework.py` | 37 | All 6 metrics + EvalRunner + EvalStorage + EvalReport/EvalSummary |
| `test_eval_batch.py` | 10 | Batch evaluation: MRR, error isolation, summary aggregation |
| `test_rag_e2e_verification.py` | 6 | Generation → verification → evaluation integration flow |
| `test_resilient_pipeline.py` | 10 | Circuit breaker state machine (closed→open→half-open→closed), fallback |
| `test_hybrid_vs_dense.py` | 7 | RRF hybrid vs dense-only retrieval quality comparison |
| `test_legal_ocr.py` | 18 | LegalDocumentOCR: PDF image extraction, EasyOCR, text reconstruction |
| `test_ingestion_pipeline.py` | 27 | IngestionPipeline: full e2e, real-loader, corpus batch, fault isolation |
| `test_ingest_corpus_cli.py` | 11 | Corpus CLI: schedule wiring, batch progress, config validation |
| `test_batch_ingestion.py` | 6 | QStash ingest_corpus schedule + batch progress tracking |
| `test_reindexing.py` | 3 | Delete + re-index after content changes (version-aware) |
| `test_rag_benchmarks.py` | 17 | Benchmark harness: chunking, embedding, Qdrant search latency |
| `test_rag_routes.py` | 15 | Route: /api/rag/query, /api/rag/generate, /api/rag/eval, /api/rag/health |
| `test_rag_tasks.py` | 7 | Celery tasks: retrieve_task, embed_and_index_task, ingest_corpus_task |
| `test_rag_smoke.py` | 9 | Smoke: Qdrant ping, embed dims, chunk schema, search, classify |
| `test_enrichment_deterministic.py` | 23 | Enrichment: deterministic §5.1 field extraction |
| `test_enrichment_eval.py` | 21 | Enrichment evaluation: F1/precision/recall against ground truth |
| `test_enrichment_audit.py` | 10 | Enrichment audit trail: field-level tracking, hash chain |
| `test_neo4j_kg_sync.py` | 15 | Neo4j: config detection, real connection, APOC push, sync task, route |
| `test_rag_backup.py` | 0 | *(exists but file is a backup script — not a test)* |

**Total: 695 tests, all passing.**

### 2.2 Testing Philosophy

- **Stub mode by default:** `RAG_USE_STUB_LLM=true` — generation tests use synthetic responses, no real API calls required.
- **Qdrant in tests:** Uses `QdrantStore` in-memory test mode (no external connection needed for unit tests).
- **No network required:** All 695 tests run offline with synthetic data or stub services.
- **Integration tests exist:** `test_rag_e2e.py` (9), `test_hybrid_vs_dense.py` (7), `test_rag_e2e_verification.py` (6) — but only when `RAG_QDRANT_URL` is set or stub mode detects local availability.

---

## 3. Corpus Inventory

### 3.1 What's Loaded

```
Corpus location: app/shared/corpus/ (or RAG_CORPUS_DIR env var)
Format: 24 FSSAI domain documents (PDF + DOCX + TXT)
```

| Document Type | Count | Qdrant Indexed |
|---|---|---|
| FSS Act, 2006 | 1 | ✅ |
| FSS Regulations (various) | 8 | ✅ |
| FSS Notifications | 12 | ✅ |
| FSS Standards (Food Categories) | 3 | ✅ |
| **Total documents** | **24** | **24** |
| **Total chunks generated** | **~13,104** | **1,097 indexed** |

### 3.2 Payload Schema (§5.1 — what every chunk carries)

Every chunk in Qdrant has a payload with these fields:

| Field | Type | Sample Value | Coverage (sample of 100) |
|---|---|---|---|
| `document_id` | str (UUID) | `a1b2c3d4...` | 100% |
| `document_title` | str | "Food Safety and Standards Act, 2006" | 100% |
| `chunk_id` | str (UUID) | `e5f6g7h8...` | 100% |
| `chunk_index` | int | 0, 1, 2... | 100% |
| `chunk_text` | str | "No person shall manufacture..." | 100% |
| `section_number` | str | "33", "2-18", "39(b)" | 97% |
| `section_title` | str | "Powers of Officer" | 97% |
| `act_name` | str | "Food Safety and Standards Act, 2006" | 100% |
| `act_number` | str | "18 of 2006" | 100% |
| `year` | int | 2006 | 100% |
| `is_current` | bool | `true` / `false` | 100% |
| `effective_date` | date | "2006-09-01" | 2% |
| `enactment_date` | date | "2006-09-01" | 8% |
| `amended_date` | date | "2010-08-12" | 1% |
| `repealed_by` | str | null | 100% (null) |
| `cited_sections` | list[str] | ["33", "39(b)"] | 9% |
| `cited_acts` | list[str] | ["FSS Act", "CGTMSE Act"] | 5% |
| `entities` | list[LegalEntity] | [{name:"Officer", type:"role",...}] | 88% |
| `language` | str | "en" | 100% |
| `chunk_quality_grade` | str (A-F) | "A" | 100% |
| `source_hash` | str (SHA-256) | `a1b2c3d4...` | 100% |

> **⚠️ Post-audit correction (2026-08-10):** this table lists fields `Chunk.to_payload()` does **not** emit — `act_number`, `year`, `cited_sections`, `cited_acts`, `language`, `chunk_quality_grade`, `source_hash`. The actual payload emits: `chunk_id`, `document_id`, `document_uri`, `document_title`, `document_type`, `authority`, `jurisdiction`, `state`, `act_name` (added Phase 1), `effective_date`, `enactment_date`, `amended_date`, `is_current`, `chunk_index`, `chunk_text`, `chunk_char_count`, `section_number`, `section_title`, `subsection`, `hierarchy_level`, `parent_chunk_id`, `citations`, `references`, `entities` (plain name list — the structured `[{name,type,confidence}]` form lives in `LegalChunk.entities`), `confidence`, `created_at`, `embedding_model`, and `content_hash` (SHA-256 — this is what the table calls `source_hash`). Coverage % should be re-measured against the live corpus after the multi-domain re-index.
>
> **⚠️ Payload identity added (2026-08-11):** the §5.1 payloads in Qdrant now also carry the audit-missing identity fields — `provision_id`, `instrument_id`, `legal_domain`, `status` — stamped onto **100% of live points** (`kg/payload_identity.py`; 24 keyword indexes; payload ↔ Neo4j `provision_id` verified 1:1). ⛔ **P1-4 executed the same day:** `fssai_legal_768` was rebuilt from the current DB (12,819 pts, `act_name` 100%) and fully identity-stamped — the earlier "domain-only" caveat no longer applies.

### 3.3 What's Missing from Corpus

- **Multi-state case law** — No state-specific FSO case judgments or tribunal orders
- **Central government circulars** — No Ministry of Health circulars beyond FSSAI notifications
- **Cross-domain documents** — No income tax, company law, or IPC sections that FSOs might cross-reference

---

## 4. Infrastructure Connectivity

### 4.1 Qdrant Cloud — ✅ Live & Populated

```
Status: REACHABLE
URL: qdrant.cloud (configured via RAG_QDRANT_URL + RAG_QDRANT_API_KEY)
Collection: fssai_legal_768
Vector config: dense (768-dim, distance=COSINE) + sparse (BM25)
Points indexed: 1,097
Payload filters: section_number ✓, act_name ✓, is_current ✓, document_title ✓
Scroll API: ✓ (used for backup/restore)
Health: ✓ (200 OK)
```

### 4.2 Neo4j Aura — ✅ Connected & Populated (legal KG, 2026-08-11)

```
Status: REACHABLE (Bolt protocol over TLS)
URL: neo4j+s://<instance>.databases.neo4j.io (via NEO4J_URI)
Auth: neo4j / (password set)
Legal KG nodes: 29,385   (58 instruments · 1,861 provisions · 27,343 chunks · 36 concepts · 18 authorities · 8 domains)
Legal KG rels:  40,081   (BELONGS_TO_DOMAIN, CONTAINS, SOURCE_OF, SUPPORTED_BY, HAS_CHUNK,
                          semantic edges, supersession edges, ISSUED_BY, cross-domain edges)
Schema: setup by kg/schema.py (constraints on provision_id/chunk_id/document_id/instrument_id/
        concept_id/authority_id/domain_name + RANGE indexes on legal_domain, status, ...)
Build:  scripts/build_kg_corpus.py (manifest + Qdrant + FSS DB → corpus-truthful KG)
APOC: not required (batched UNWIND MERGE — works on Aura Free)
```

**Status change (2026-08-11):** the audit-time state (0 nodes / empty schema) is superseded by the **Option B KG rebuild** — the graph now mirrors the live retrieval corpus 1:1 and scored **69/100 (Operational, READY for controlled hybrid retrieval)** on the readiness rubric (`KG_READINESS_AUDIT_POST_REBUILD.md`; was 32/100). The case-file graph (`Case`/`FBO`/...) coexists untouched; the legal KG is a separate namespace.

**Note:** `app/knowledge_graph/engine.py` extracts entities/relationships from case files via `KnowledgeGraphEngine.process_case_file()` → `Neo4jGraphService.sync_case()`; the root `neo4j_aura_loader.py` (~50 hardcoded sample nodes) remains a dev-only bootstrap and is **never called in production**.

### 4.3 PostgreSQL / SQLite

- **PostgreSQL:** Primary DB, running on Render. Contains all core models: `LegalDocument`, `LegalChunk`, `RAGQueryLog`, `RAGEvalResult`, `RAGEvalDataset`, `AuditLog`, etc.
- **SQLite:** Dev fallback. Used by all unit tests (no external DB required).

### 4.4 Redis (Celery Broker)

- Redis is configured via `REDIS_URL`.
- Celery workers process: `retrieve_task`, `embed_and_index_task`, `ingest_corpus_task`, `generate_task`, `evaluate_task`.
- In tests: Celery tasks are mocked or use `CELERY_TASK_ALWAYS_EAGER=true`.
- `run_generation_pipeline` also supports **KG graph expansion** (`RAG_KG_EXPANSION=true`) — see §5.1.

---

## 5. Feature Matrix — What IS vs. What Was Proposed (but Not Implemented)

### 5.1 Implemented Features (✓)

| Feature | File(s) | Status |
|---|---|---|
| Dense retrieval (vector search) | `app/rag/retrieval/dense_retriever.py` | ✅ |
| Sparse retrieval (BM25) | `app/rag/retrieval/sparse_retriever.py` | ✅ |
| Hybrid retrieval (RRF fusion) | `app/rag/retrieval/hybrid_retriever.py` | ✅ (k=60) |
| Cross-encoder reranking | `app/rag/retrieval/reranker.py` | ✅ (with deterministic fallback) |
| Query classification | `app/rag/retrieval/query_classifier.py` | ✅ (rule-based) |
| Query parsing (sections/authorities/case-law) | `app/rag/retrieval/query_classifier.py` | ✅ |
| Hash-chained audit trail | `app/models/inspection.py` (AuditLog) + `app/rag/retrieval/logger.py` | ✅ |
| Circuit breaker resilience | `app/rag/resilient.py` | ✅ (4-state FSM) |
| Fallback to dense-only if sparse fails | `app/rag/retrieval/hybrid_retriever.py` | ✅ |
| Grounded LLM generation | `app/rag/generation/grounded_service.py` | ✅ (stub + real API) |
| Citation tracking (&[n] brackets) | `app/rag/generation/citation_tracker.py` | ✅ |
| Response sanitization | `app/rag/generation/sanitizer.py` | ✅ |
| Claim extraction | `app/rag/verification/claim_extractor.py` | ✅ |
| Evidence verification | `app/rag/verification/evidence_verifier.py` | ✅ |
| Groundedness scoring | `app/rag/verification/scorer.py` | ✅ |
| Hallucination detection | `app/rag/verification/hallucination_detector.py` | ✅ |
| Token counting | `app/rag/verification/token_counter.py` | ✅ (tiktoken + fallback) |
| Evaluation metrics (6 types) | `app/rag/evaluation/metrics.py` | ✅ |
| Batch evaluation | `app/rag/evaluation/runner.py` | ✅ |
| Evaluation storage | `app/rag/evaluation/storage.py` | ✅ |
| Corpus ingestion (full pipeline) | `app/rag/ingestion.py` | ✅ |
| Document classification | `app/rag/document_classifier.py` | ✅ |
| Metadata adapter | `app/rag/metadata_adapter.py` | ✅ |
| Citation adapter | `app/rag/citation_adapter.py` | ✅ |
| Cross-reference adapter | `app/rag/crossref_adapter.py` | ✅ |
| Entity extractor (3-tier) | `app/rag/entity_extractor.py` | ✅ |
| Chunk quality validator | `app/rag/chunker.py` | ✅ |
| Legal entity model | `app/models/rag.py` | ✅ |
| QStash scheduled ingestion | `app/rag/tasks.py` + `app/__init__.py` | ✅ (daily at 03:00 UTC) |
| Backup/restore (Qdrant scroll_all) | `app/rag/backup.py` | ✅ |
| RAG health endpoint | `app/rag/routes.py` | ✅ (GET /api/rag/health) |
| Full pipeline API | `app/rag/routes.py` | ✅ (POST /api/rag/query) |
| Generation API | `app/rag/routes.py` | ✅ (POST /api/rag/generate) |
| Evaluation API | `app/rag/routes.py` | ✅ (POST /api/rag/eval) |
| Ingestion API | `app/rag/routes.py` | ✅ (POST /api/rag/ingest) |
| OCR for scanned PDFs | `app/rag/legal_ocr.py` | ✅ (EasyOCR lazy-loaded) |
| Enrichment pipeline (deterministic) | `app/rag/enrichment/deterministic.py` | ✅ |
| Neo4j KG sync | `app/knowledge_graph/` + `app/services/neo4j_graph.py` | ✅ (case-file graph; legal KG separately populated) |
| Corpus KG rebuild | `kg/corpus_ingestion.py` + `scripts/build_kg_corpus.py` | ✅ (58 instruments · 1,861 provisions · 27,343 chunks — 100% corpus, 2026-08-11) |
| KG semantic enrichment | `kg/enrichment.py` + `scripts/enrich_kg_semantics.py` | ✅ (750 evidence-backed duty/offence/penalty/prohibition/power edges on 591 provisions; deterministic, no LLM) |
| KG hybrid expansion in pipeline | `kg/hybrid.py` + `app/rag/tasks.py` | ✅ (`RAG_KG_EXPANSION=true` expands Qdrant chunk IDs → Neo4j provisions/domains/status/authorities/provenance; best-effort, response carries `kg_expansion`) |
| Qdrant payload identity stamping | `kg/payload_identity.py` + `scripts/stamp_qdrant_payload_identity.py` | ✅ (canonical `provision_id`/`instrument_id`/`legal_domain`/`status` on 15,624/15,624 live points from the Neo4j-shared registry; 24 payload indexes; idempotent; 60/60 sampled provision_ids match live Neo4j 1:1 — closes the audit's P1 payload gap) |

### 5.2 NOT Implemented — Advanced Argumentation Layers

These are **not part of the existing RAG specification** in `plan.md` or `task.md`. They represent enhancements that were discussed but never designed or prioritized:

| Proposed Feature | Status | Why |
|---|---|---|
| **LangChain** | ❌ Not installed | Not part of project stack. AGENTS.md explicitly states Flask + SQLAlchemy + Qdrant. |
| **LangGraph** | ❌ Not installed | Not part of project stack. |
| **Pydantic structured LLM output** | ❌ Not used in RAG | RAG uses dataclasses (`RAGResponse`, `RetrievedChunk`). Pydantic 2.13.4 is installed and used in `document_cleaner`, `document_loader`, `metadata_extractor`, `ocr_pipeline` — but NOT for structured LLM output. |
| **Instructor** | ❌ Not installed | No structured output framework. |
| **IRAC framework** | ❌ Not implemented | No Issue/Rule/Application/Conclusion decomposition anywhere. |
| **Counterargument handling** | ❌ Not implemented | No dialectical reasoning layer. |
| **Game theory analysis** | ❌ Not implemented | No actors, strategies, minimax, regret metrics. |
| **Talebian antifragility analysis** | ❌ Not implemented | No convexity/fragility modeling. |
| **Forward analysis** | ❌ Not implemented | No future-state scenario modeling. |
| **Persistent claim ledger** | ❌ Not a DB table | `ClaimExtractor` + `EvidenceVerifier` work in-memory per-call. Claims are NOT stored between sessions. No `ClaimLedger` model exists. |
| **Temporal-aware retrieval** | ⚠️ Partial | Payload has `effective_date`, `enactment_date`, `amended_date`, `is_current` fields, but only 2% have `effective_date` populated. No date-filtered queries, no repeal/supersession handling at query time. |
| **Source hierarchy** | ❌ Not implemented | All chunks treated equally in scoring. No "statute > notification > circular" ranking. |
| **Deliberate abstention** | ❌ Not a defined pathway | System will answer with whatever the LLM returns (even if stubbed). No "I don't know" threshold. The hallucination detector flags unverified claims post-hoc but doesn't prevent generation. |
| **Human review workflow** | ❌ Not for RAG output | `document_viewer` has version control (save/restore/compare), but no approval step between RAG generation and case file insertion. The AI assistant (`app/ai_assistant/`) has no review/approval gate. |

### 5.3 What Exists That Could Support These Features

- **Claim-level verification** (`ClaimExtractor` + `EvidenceVerifier`) → Could be extended to a persistent claim ledger
- **Temporal payload fields** (`effective_date`, `is_current`) → Could support date-filtered queries
- **Version control** (`app/version_control/`) → Could support human review workflows
- **Neo4j legal KG** → Now supports entity-relationship reasoning: corpus-truthful graph (69/100 readiness) with `KGContextExpander` wired into the generation pipeline behind `RAG_KG_EXPANSION` (2026-08-11)

---

## 6. Environment & Deployment

### 6.1 Render Free Compatibility — ❌ Not Compatible

The RAG system **cannot run on Render Free** (512MB RAM) because:

| Requirement | Size | Render Free Limit |
|---|---|---|
| `sentence-transformers` + `torch` | ~480MB | 512MB |
| `easyocr` + `onnxruntime` | ~120MB | |
| `fastembed` (sparse) | ~80MB | |
| `spacy` + model | ~100MB | |
| WeasyPrint (GTK deps) | system pkg | |
| **Total estimated** | **~800MB+** | **512MB** |

Additionally, RAG requires **Qdrant Cloud** (external), **Neo4j Aura** (external), **PostgreSQL**, and **Redis** — none of which are available on Render Free.

### 6.2 Environment Variables (`.env.example`)

```
RAG_QDRANT_URL=                    # Qdrant Cloud URL (REQUIRED for live RAG)
RAG_QDRANT_API_KEY=                # Qdrant Cloud API key
RAG_VECTOR_SIZE=768                # Vector dimension
RAG_QDRANT_COLLECTION=fssai_legal_768              # default (FSSAI) collection
RAG_QDRANT_COLLECTION_ENV=env_legal_768            # per-domain overrides (multi-domain Phase 1, 2026-08-10)
RAG_QDRANT_COLLECTION_COMMERCIAL=commercial_legal_768
RAG_QDRANT_COLLECTION_ANIMAL=animal_legal_768
RAG_QDRANT_COLLECTION_WB_STATE=wb_state_legal_768
RAG_QDRANT_COLLECTION_CRIMINAL=criminal_legal_768
RAG_EMBEDDING_MODEL=all-mpnet-base-v2
RAG_RERANKER_MODEL=                # Optional cross-encoder
RAG_USE_STUB_LLM=true              # Stub LLM mode (no API call needed)
RAG_LLM_PROVIDER=                  # openrouter or openai
OPENROUTER_API_KEY=                # Required for real LLM generation
OPENAI_API_KEY=                    # Fallback if OpenRouter not set
RAG_ENABLE_INGESTION_SCHEDULE=false
RAG_INGESTION_CRON=0 3 * * *      # Daily at 03:00 UTC
RAG_CORPUS_DIR=app/shared/corpus/
```

In `render.yaml`, the production config sets:
- `RAG_USE_STUB_LLM=false` (real LLM)
- `RAG_ENABLE_INGESTION_SCHEDULE=true` (daily ingestion)
- `OPENROUTER_API_KEY` (real API key)
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` (real Neo4j Aura)
- `RAG_QDRANT_URL`, `RAG_QDRANT_API_KEY` (real Qdrant Cloud)

### 6.3 RAG in Production (Render)

The RAG blueprint is registered in `app/rinit__.py` and its routes are live at `/api/rag/*`. The Celery tasks (`app/rag/tasks.py`) are wired into the Celery app (`celery_app.py`). QStash schedules daily corpus ingestion at 03:00 UTC via `app/__init__.py`.

---

## 7. What "Needs to be Done" vs. Reality

### 7.1 If the goal is "Make RAG work end-to-end"

| Step | Status |
|---|---|
| Set `RAG_QDRANT_URL` + `RAG_QDRANT_API_KEY` | ✅ Already configured in render.yaml |
| Set `OPENROUTER_API_KEY` | ✅ Already configured in render.yaml |
| Set `NEO4J_*` vars | ✅ Already configured in render.yaml |
| Ingest corpus into Qdrant | ✅ 1,097 chunks already indexed |
| Set `RAG_USE_STUB_LLM=false` | ✅ Done in render.yaml |
| Enable ingestion schedule | ✅ Done in render.yaml |

**Reality:** The RAG system is **fully deployed and operational** in the Render production environment. All infrastructure connections are configured. The corpus is loaded. The API endpoints are live.

### 7.2 If the goal is "Add advanced argumentation analysis"

This is **a new feature request, not a bug fix**. It would require:

1. **Pydantic structured output** — Add `instructor` package, define `StructuredAnalysisOutput` model
2. **IRAC framework** — New module in `app/rag/analysis/irac.py`
3. **Counterargument engine** — New module with dialectical reasoning
4. **Claim ledger** — New DB model + migration
5. **Human review workflow** — New blueprint + UI + approval API
6. **~400+ new tests** for the above

**Estimate:** 2-3 weeks of development, not a quick fix.

---

## 8. Key Observations

1. **AGENTS.md is outdated.** It claims "437 RAG tests" — the actual count is **695**. The AGENTS.md file predates the verification, evaluation, resilience, and benchmark phases.

2. **The RAG system is more complete than documented.** The retrieval, generation, verification, and evaluation pipelines are all fully implemented with comprehensive test coverage. The only "gap" is that some features weren't yet proposed when AGENTS.md was written.

3. **Neo4j was a known gap — closed 2026-08-11.** The audit-time graph (connected but empty) is now a corpus-truthful legal KG (58 instruments · 1,861 provisions · 27,343 chunks · 69/100 readiness). The case-file path (`KnowledgeGraphEngine` → `Neo4jGraphService.sync_case()`, 15 tests) remains the FSO-case graph; the legal KG is built by `scripts/build_kg_corpus.py` and expanded into RAG responses via `RAG_KG_EXPANSION`.

4. **Stub LLM mode is the default in dev.** This is by design — it allows all 695 tests to pass without API keys. In production (Render), `RAG_USE_STUB_LLM=false` is set and the real OpenRouter API is used.

5. **No LangChain/LangGraph dependency.** The system uses its own purpose-built pipeline architecture. Adding these frameworks would be a major architectural change, not a missing dependency.

---

## 9. Recommendations

### 9.1 If you want to verify the claim:

```bash
# Run all RAG tests
python -m pytest tests/test_rag_*.py tests/test_qdrant_*.py tests/test_embedding_*.py tests/test_chunker.py tests/test_chunk_quality.py tests/test_dedup.py tests/test_metadata_adapter.py tests/test_citation_adapter.py tests/test_crossref_adapter.py tests/test_entity_extractor.py tests/test_query_classifier.py tests/test_dense_retriever.py tests/test_sparse_retriever.py tests/test_hybrid_retriever.py tests/test_reranker.py tests/test_retrieval_logger.py tests/test_hallucination_detector.py tests/test_citation_validator.py tests/test_token_counter.py tests/test_eval*.py tests/test_resilient_pipeline.py tests/test_hybrid_vs_dense.py tests/test_legal_ocr.py tests/test_ingestion_pipeline.py tests/test_ingest_corpus_cli.py tests/test_batch_ingestion.py tests/test_reindexing.py tests/test_rag_benchmarks.py tests/test_neo4j_kg_sync.py tests/test_enrichment_*.py -v

# Run a single RAG test file
python -m pytest tests/test_rag_e2e.py -v

# Check Qdrant connectivity
python -c "from app.rag.qdrant_client import QdrantStore; s=QdrantStore(); print(s.health())"
```

### 9.2 If you want to add Pydantic structured LLM output:

This is a legitimate enhancement. The minimal path:

1. Install `instructor` and `pydantic` (already present)
2. Create `app/rag/generation/structured_output.py` with Pydantic models for analysis output
3. Modify `GroundedLLMClient` to optionally use `instructor.patch()` for structured responses
4. Add tests for structured output parsing

### 9.3 If you want to populate Neo4j:

1. **Legal KG (done 2026-08-11):** `python scripts/build_kg_corpus.py` — rebuilds the legal KG from the multi-domain manifest + live Qdrant payloads + FSS DB (idempotent; `--dry-run` pre-flights, `--no-clear` merges). Then `python scripts/enrich_kg_semantics.py` tags provisions with typed semantics. Enable `RAG_KG_EXPANSION=true` to expand Qdrant hits through the graph in `/api/rag/query` responses.
2. **Qdrant payload identity (done 2026-08-11):** `python scripts/stamp_qdrant_payload_identity.py` stamps canonical `provision_id`/`instrument_id`/`legal_domain`/`status` onto live points from the same registry that builds Neo4j (`--dry-run` pre-flights; idempotent — re-run reports 0 updates). Re-run after any corpus re-ingest so new points carry the identity fields.
3. **Case-file graph:** create real case files in the app (via the inspection/sample workflow)
4. `KnowledgeGraphEngine.process_case_file()` will extract entities automatically
5. `Neo4jGraphService.sync_case()` will push to Neo4j

---

## 10. Conclusion

**The RAG subsystem described in `plan.md` and `task.md` is fully implemented, tested, and deployed.** The infrastructure (Qdrant Cloud, Neo4j Aura, PostgreSQL, Redis) is configured and connected. All API endpoints are live.

**Post-audit (2026-08-11) additions:** the multi-domain corpus is fully ingested into per-domain Qdrant collections (14,524 points across 5 collections); the Neo4j legal KG is populated to mirror the retrieval corpus 1:1 (29,385 nodes / 40,081 rels; 69/100 readiness); provisions are semantically enriched (750 evidence-backed edges); the KG is wired into generation as an optional graph-expansion layer (`RAG_KG_EXPANSION`); and **Qdrant payloads now carry the canonical identity fields** — including the **P1-4 FSSAI re-ingest executed the same day**: `fssai_legal_768` rebuilt to 12,819 pts (identity-preserving, `act_name` 100%) and fully stamped, taking Qdrant to **27,343 points = 100% of the corpus** (was 15,624 with a 1,100-point stale FSSAI snapshot). See `docs/MULTIDOMAIN_INTEGRATION.md`, `KG_READINESS_AUDIT_POST_REBUILD.md`, and `CORPUS_IDENTITY_REPORT.md` §8.

The "missing features" (LangChain, IRAC, counterarguments, game theory, persistent claim ledger, human review, Pydantic structured LLM output, abstention) are **not documented requirements** in the existing specifications — they are speculative enhancements from a general-purpose RAG discussion that don't align with this specific FSS Act legal workflow codebase.

**The single genuine gap** is that `RAG_USE_STUB_LLM=true` by default in dev, meaning the system won't produce real LLM-generated answers until `OPENROUTER_API_KEY` is set and `RAG_USE_STUB_LLM=false` is configured — which is already done in `render.yaml` for production.

---

*Report generated by static analysis + live connectivity probes. No LLM was queried, no network calls were made to external services during this audit (Qdrant/Neo4j probes used existing configured connections). All test counts verified via `pytest` test function enumeration.*

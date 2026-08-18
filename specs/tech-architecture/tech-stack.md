# NSA Webservice — Tech Stack & Architecture

## 1. Stack Overview

| Layer             | Technology                          | Version | Notes                                                                             |
| ----------------- | ----------------------------------- | ------- | --------------------------------------------------------------------------------- |
| **Runtime**       | Python                              | 3.12+   | Strictly typed, modern syntax                                                     |
| **Web Framework** | Flask 2.x                           | -       | Server-rendered (Jinja2), no React build pipeline                                 |
| **Database**      | PostgreSQL                          | -       | Primary; SQLite fallback (dev)                                                    |
| **ORM**           | SQLAlchemy 2.x                      | -       | Declarative models, optimistic concurrency via `version_id`                       |
| **Migrations**    | Alembic                             | >=1.13  | 27+ migration files                                                               |
| **Task Queue**    | Celery 5.x + QStash                 | >=5.6.3 | QStash for webhook-based (no worker needed on free tier); Celery for heavier jobs |
| **Cache**         | Redis                               | >=5.0.0 | Task result backend + Celery broker                                               |
| **Search**        | SQLite FTS5 / Qdrant                | -       | FTS5 for local; Qdrant for RAG vector search                                      |
| **RAG**           | Qdrant + sentence-transformers      | -       | Dense embeddings (all-mpnet-base-v2), BM25 via fastembed                          |
| **KG**            | Neo4j Aura                          | -       | Multi-domain legal knowledge graph                                                |
| **PDF**           | WeasyPrint                          | -       | HTML→PDF via `app/utils/pdf_utils.py`                                             |
| **Images**        | Pillow, OCR                         | >=9.0.0 | PaddleOCR + Tesseract for OCR pipeline                                            |
| **Cloud Storage** | R2/B2 (S3-compatible) or Cloudinary | -       | Configured via env vars                                                           |
| **Auth**          | Flask-Login + CSRF                  | -       | Session-based auth, RBAC (Phase 18)                                               |
| **Security**      | Flask-Talisman                      | >=1.1.0 | CSP, HSTS, secure cookies                                                         |

## 2. Architecture

### High-Level Pattern

**Layered Feature-First Architecture** — Each domain (case_file_generator, adjudication, sample, inspection, etc.) is a Flask Blueprint with its own routes, templates, and minimal shared logic. Business logic lives in:

- `app/services/` — Service classes with clear interfaces
- `app/shared/` — Cross-cutting abstractions (CaseResolver, DocumentCaseManager, etc.)
- `app/utils/` — Utility functions

### Data Flow

```
Request → Blueprint Route → Service/Manager → SQLAlchemy Model → Database
                                    ↓
                              [QStash webhook for async tasks]
                                    ↓
                              [Neo4j for KG sync]
                              [Qdrant for RAG vector search]
                              [R2/B2 for photo storage]
```

### Key Architectural Decisions (from AGENTS.md)

1. **Keep Flask** — No React build pipeline; server-rendered Jinja2 templates
2. **Canonical Key Contract** — `app/shared/case_keys.py` defines uniform field names across modules
3. **Hash-chained audit** — `AuditLog` uses SHA-256 chaining for tamper evidence
4. **Optimistic concurrency** — `version_id` columns + `StaleDataError` → 409 on conflict
5. **Storage abstraction** — `app/utils/storage.py` branches to Cloudinary/R2/local per env vars
6. **Race-safe sequences** — `CodeSequence` table + PostgreSQL advisory locks

### Entry Points

| Entry Point   | Location        | Purpose                                                |
| ------------- | --------------- | ------------------------------------------------------ |
| WSGI          | `app.py`        | `create_app()` factory, registers 23+ blueprints       |
| Celery Worker | `celery_app.py` | Factory with ContextTask wrapper                       |
| CLI Scripts   | `scripts/*.py`  | One-off utilities (backup, build_kg, reindexing, etc.) |
| Tests         | `tests/*.py`    | Pytest suite with SQLite in-memory DB                  |

## 3. Error Handling Patterns

### API Error Responses

All routes return JSON errors with consistent structure:

```json
{ "error": "Description of the error" }
```

Returns appropriate HTTP status codes (400, 404, 500, 503, etc.)

### Global Error Handling

- **Login gate**: `@app.before_request` → `require_login()` redirects unauthenticated users
- **Exception handling**: Routes catch specific exceptions, log via `app.logger`, return 500
- **503 for disabled features**: AI Assistant, RAG disabled → `{"error": "..."}`; 503 status

### Service Layer Errors

- `log_audit()` → catches `Exception`, rolls back, re-raises
- Storage clients → raise `RuntimeError` with descriptive message when not configured
- Neo4j sync → graceful degradation when not configured

## 4. API Design

### REST Patterns

- **Snake_case** for JSON keys and query parameters
- **Blueprints** provide URL prefixes (e.g., `/api/rag`, `/knowledge-graph`, `/ai-assistant`)
- **Public endpoints**: Listed in `public_endpoints` set in `create_app()`

### Response Serialization

- **Models**: `model_to_dict_fn` callbacks for JSON serialization
- **Paginated lists**: Return dict with `items`, `pages`, `has_next`, etc.
- **Status endpoints**: Return `{"status": "ok", ...}` for health checks

### Async Task API

```
POST /api/task → publishes to QStash or Celery
GET /api/task/<task_id> → check status
Webhook → QStash calls task endpoint, returns result
```

## 5. Type Safety

### Static Typing

- **mypy** configured with `strict=false`, `warn_unused_ignores=true`
- **Type stubs** available for Flask, SQLAlchemy via `types-*` packages
- **TypedDict** used for complex response shapes

### Runtime Typing

- **Pydantic** models for request validation (via `pydantic>=2.0.0`)
- **SQLAlchemy 2.x** type annotations on models
- **Return type hints** on all service methods

## 6. Observability

### Logging

- **Structured logging** via Python `logging` module
- **App logger** accessible via `app.logger`
- **Log levels**: INFO for normal ops, WARNING for expected issues, ERROR for failures

### Health Checks

- **Public endpoint**: `GET /health` (from `app/health/`)
- **RAG health**: `GET /api/rag/health` (public, no auth)
- **Audit chain verification**: `verify_audit_chain()` available for integrity checks

### Metrics Storage

- **Task logs**: `RAGQueryLog`, `GenerationLogger` for RAG observability
- **Evaluation results**: `RAGEvalResult`, `EvalSummary` models
- **Sync status**: `AirtableBaseMap` tracks Sheets/Airtable sync state

## 7. Testing Strategy

### Test Organization

```
tests/                          # Main test suite (pytest)
├── conftest.py                 # Session fixtures, test DB setup
├── test_*.py                   # Feature tests (~400 test files)
├── js/                         # Jest tests for static JS
```

### Test Patterns

- **Fixture setup**: `conftest.py` sets `DATABASE_URL` before app import
- **RAG tests**: Stub LLM mode by default (`RAG_USE_STUB_LLM=true`)
- **Test categories**:
    - Unit tests for services, models, adapters
    - Integration tests for routes
    - E2E tests for RAG pipeline
    - Regression tests for CE-v2 evaluation

### Tools

| Tool       | Version  | Purpose              |
| ---------- | -------- | -------------------- |
| pytest     | >=9.1.1  | Main test runner     |
| pytest-cov | >=7.1.0  | Coverage reporting   |
| mypy       | >=2.3.0  | Static type checking |
| ruff       | >=0.16.3 | Linting              |
| black      | >=26.5.1 | Formatting           |

### CI/CD

- **GitHub Actions**: validation.yml, deploy.yml, lint.yml, security scans
- **Python 3.12** matrix (Ubuntu, Windows)
- **Node.js 22** for JS linting (ESLint, Prettier)

## 8. Consistency Gaps & Debt Hotspots

### Identified Patterns

1. **API Shapes**: Mixed response structures across routes
    - Some use `jsonify({"error": "..."})`, others use custom error objects
    - Pagination varies between `paginate()` and manual dict construction

2. **Date/Disambiguation inconsistencies**:
    - `inspection_date` vs `first_inspection_date` vs `followup_inspection_date`
    - Canonical keys in `case_keys.py` but some routes still use old keys

3. **Document Case Duplication** (addressed in deepening D5):
    - `case_file_generator/routes.py` (~700 lines)
    - `adjudication/routes.py` (~800 lines)
    - Now consolidated via `DocumentCaseManager` in `app/shared/`

4. **Async sync patterns**:
    - Some routes use QStash (webhook-based)
    - Others use Celery directly
    - Both supported for different latency requirements

5. **Auth consistency**:
    - Most routes protected by `require_login()` global gate
    - Exceptions: health probes, public lookup endpoints, QStash webhooks

## 9. Integration Points & Future Considerations

### External Services

| Service             | Integration                 | Status                                               |
| ------------------- | --------------------------- | ---------------------------------------------------- |
| Google Sheets API   | `gspread` + Service Account | Active                                               |
| Airtable            | `pyairtable`                | Active                                               |
| Microsoft Graph API | `msal` client credentials   | Configured (dormant unless `ENABLE_EXCEL_SYNC=true`) |
| Neo4j Aura          | Bolt protocol               | Active (KG)                                          |
| Qdrant Cloud        | HTTP API                    | Active (RAG)                                         |
| Upstash QStash      | REST API                    | Active (webhook tasks)                               |
| Cloudflare R2       | S3-compatible               | Active (photo storage)                               |

### Dependencies to Avoid Adding

- **React**: Codebase uses server-rendered Jinja2; no build pipeline
- **GraphQL**: All APIs are REST/JSON
- **Additional ORMs**: SQLAlchemy already configured

## 10. Quick Reference: Common Operations

### Run Tests

```bash
pytest tests/ -v
pytest tests/test_rag*.py -v  # RAG-specific
```

### Run App

```bash
python app.py  # port 8000
# Or via Flask CLI
FLASK_APP=app.py flask run
```

### Format & Lint

```bash
ruff check .        # lint
ruff format .       # format
mypy .              # type check
npm run lint        # JS lint
```

### Database Migrations

```bash
flask db migrate -m "description"
flask db upgrade
```

### Environment Setup

```bash
cp .env.example .env
# Set DATABASE_URL, SECRET_KEY, and any service credentials
```

## 11. RAG Pipeline Architecture

### Overview

The RAG system is organized as a 5-phase pipeline with strict separation of concerns:

```
┌─────────────────────────────────────────────────────────────────┐
│                        Ingestion (Phase 1)                       │
│  Document Loader → Chunker → EmbeddingService → QdrantIndexer   │
│                                                                 │
│                        Retrieval (Phase 1)                       │
│  QueryClassifier → DenseRetriever → HybridRetriever → Reranker  │
│                                                                 │
│                     Generation (Phase 2)                       │
│  ContextBuilder → PromptTemplate → GroundedLLMClient →          │
│  CitationTracker → ResponseSanitizer → GroundedGenerationSvc    │
│                                                                 │
│                   Verification (Phase 3)                        │
│  ClaimExtractor → EvidenceVerifier → CitationValidator →        │
│  GroundednessScorer → HallucinationDetector                     │
│                                                                 │
│                     Evaluation (Phase 4)                        │
│  FaithfulnessMetric, AnswerRelevanceMetric, ContextPrecision,   │
│  ContextRecall, CitationRecall, GroundednessMetric → EvalRunner   │
│                                                                 │
│                   Integration (Phase 5)                        │
│  ResilientRAGPipeline (circuit breaker) → /api/rag/query        │
│  [Agent Pipeline (M3) → /api/rag/query/agent (behind flag)]     │
└─────────────────────────────────────────────────────────────────┘
```

### RAG Sub-Package Structure

| Sub-package             | Purpose                        | Key Classes/Functions                                                                                                                                                     |
| ----------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/rag/`              | Root blueprint + orchestration | `rag_bp`, `routes.py`, `tasks.py`, `resilient.py`                                                                                                                         |
| `app/rag/retrieval/`    | Retrieval foundation           | `DenseRetriever`, `SparseRetriever`, `HybridRetriever`, `EnsembleReranker`, `QueryClassifier`                                                                             |
| `app/rag/generation/`   | Grounded generation            | `ContextBuilder`, `PromptTemplate`, `GroundedLLMClient`, `GroundedGenerationService`, `ResponseSanitizer`, `CitationTracker`, `GenerationLogger`                          |
| `app/rag/verification/` | Hallucination detection        | `ClaimExtractor`, `EvidenceVerifier`, `CitationValidator`, `GroundednessScorer`, `HallucinationDetector`, `TokenCounter`                                                  |
| `app/rag/evaluation/`   | Evaluation framework           | `FaithfulnessMetric`, `AnswerRelevanceMetric`, `ContextPrecisionMetric`, `ContextRecallMetric`, `CitationRecallMetric`, `GroundednessMetric`, `EvalRunner`, `EvalStorage` |
| `app/rag/agent/`        | LangGraph agent pipeline       | `RAGState`, `graph.py`, `nodes.py`, `routes.py`                                                                                                                           |
| `app/rag/enrichment/`   | Legal entity enrichment        | `deterministic.py` (LegalSemanticEnricher), `store.py`, `validation.py`                                                                                                   |

### RAG Data Model

| Model            | Table              | Purpose                                     |
| ---------------- | ------------------ | ------------------------------------------- |
| `LegalDocument`  | `legal_document`   | Corpus document registry (file_hash unique) |
| `LegalChunk`     | `legal_chunk`      | Per-chunk metadata + content hash           |
| `RAGQueryLog`    | `rag_query_log`    | Per-query retrieval log (hash-keyed)        |
| `RAGEvalResult`  | `rag_eval_result`  | Per-query evaluation scores                 |
| `RAGEvalDataset` | `rag_eval_dataset` | Ground-truth queries for batch evaluation   |

### RAG Task Queue

| Task                      | Module             | Purpose                      |
| ------------------------- | ------------------ | ---------------------------- |
| `run_generation_pipeline` | `app/rag/tasks.py` | retrieve → generate → verify |
| `retrieve_task`           | `app/rag/tasks.py` | Retrieval-only for query     |
| `generate_task`           | `app/rag/tasks.py` | Grounded generation          |
| `verify_task`             | `app/rag/tasks.py` | Hallucination detection      |
| `evaluate_task`           | `app/rag/tasks.py` | Batch evaluation             |
| `run_evaluate`            | `app/rag/tasks.py` | Evaluation runner            |
| `embed_and_index_task`    | `app/rag/tasks.py` | Embed + Qdrant upsert        |
| `ingest_corpus_task`      | `app/rag/tasks.py` | Corpus directory ingestion   |

### RAG Configuration (Feature Flags)

| Flag                     | Default           | Purpose                           |
| ------------------------ | ----------------- | --------------------------------- |
| `RAG_ENABLED`            | `true`            | Overall pipeline enabled          |
| `RAG_FULL_ENRICHMENT`    | `false`           | Full Phase 2 adapter chain        |
| `RAG_QDRANT_URL`         | `""`              | Qdrant server URL                 |
| `RAG_QDRANT_COLLECTION`  | `fssai_legal_768` | Primary collection name           |
| `RAG_ENABLE_SPARSE`      | `true`            | BM25 sparse embeddings            |
| `RAG_QDRANT_BM25`        | `false`           | Qdrant-side BM25 (server-side)    |
| `RAG_KG_EXPANSION`       | `false`           | KG graph expansion in generation  |
| `RAG_KG_FUSION`          | `false`           | KG contract fusion in retrieval   |
| `RAG_USE_AGENT_PIPELINE` | `false`           | LangGraph agent pipeline          |
| `RAG_AGENT_HITL`         | `false`           | Human-in-the-loop review node     |
| `RAG_AGENT_CHECKPOINTER` | `memory`          | Checkpointing backend             |
| `RAG_EMBED_ENDPOINT`     | `""`              | Remote dense embedding URL        |
| `RAG_RERANKER_ENDPOINT`  | `""`              | Remote cross-encoder reranker URL |

### RAG API Routes

| Method | Path                          | Purpose                   |
| ------ | ----------------------------- | ------------------------- |
| GET    | `/api/rag/health`             | Public health probe       |
| GET    | `/api/rag/`                   | Query UI page             |
| POST   | `/api/rag/ingest`             | Ingest a single document  |
| POST   | `/api/rag/ingest/corpus`      | Ingest a corpus directory |
| POST   | `/api/rag/query`              | Full pipeline (legacy)    |
| POST   | `/api/rag/generate`           | Grounded generation only  |
| POST   | `/api/rag/eval`               | Batch evaluation          |
| POST   | `/api/rag/query/agent`        | LangGraph agent pipeline  |
| POST   | `/api/rag/query/agent/resume` | Resume HITL paused thread |

## 12. Knowledge Graph Architecture

### Overview

Two separate KG namespaces on one Neo4j Aura instance:

1. **Case-file KG** (`app/knowledge_graph/`) — extracts entity-relationship graphs from case documents (Case, FBO, Inspector, Sample, Section nodes)
2. **Legal KG** (`kg/`) — multi-domain legal provisions, acts, concepts, and relationships

### Case-File KG

| Component              | Purpose                                     |
| ---------------------- | ------------------------------------------- |
| `KnowledgeGraphEngine` | Entity extraction + relationship mapping    |
| `routes.py`            | Flask routes for visualizer page + JSON API |
| Neo4j sync task        | Celery task for pushing to Neo4j Aura       |
| Cytoscape.js           | Frontend visualization                      |

### Legal KG (`kg/` package)

| Module                | Purpose                                                 |
| --------------------- | ------------------------------------------------------- |
| `ingestion.py`        | `LegalKGIngestionEngine` — provisions, acts, domains    |
| `corpus_ingestion.py` | `KGCorpusIngestionEngine` — corpus-derived edges        |
| `enrichment.py`       | `LegalSemanticEnricher` — duty/offence/penalty tags     |
| `hybrid.py`           | `KGContextExpander` — Qdrant→provision expansion        |
| `queries.py`          | `LegalKGQueries` — Cypher retrieval + contract building |
| `schema.py`           | Schema setup + teardown                                 |
| `validation.py`       | `KGValidator` — structural + legal validation           |
| `payload_identity.py` | `QdrantPayloadStamper` — payload backfill               |

### KG Integration with RAG

- `RAG_KG_EXPANSION` — chunk IDs → provisions/domains/status
- `RAG_KG_FUSION` — query → graph provisions RRF-fused into context
- Fail-closed: `NEO4J_ALLOW_WRITE=1` gate prevents accidental KG wipes

## 13. Service Layer Architecture

### Service Classes (`app/services/`)

| Service                   | Module                    | Purpose                                   |
| ------------------------- | ------------------------- | ----------------------------------------- |
| `DocumentSaveCoordinator` | `document_lifecycle.py`   | Save → version → audit pipeline           |
| `VersionService`          | `version_control.py`      | Compare/restore/branching of versions     |
| `SyncOrchestrator`        | `sync_orchestrator.py`    | Multi-target sync (Sheets/Airtable/Excel) |
| `SheetsSync`              | `sheets_sync.py`          | Google Sheets sync                        |
| `AirtableSync`            | `airtable_sync.py`        | Airtable redundant sync                   |
| `ExcelSync`               | `excel_sync.py`           | Excel Online sync (dormant)               |
| `Neo4jGraph`              | `neo4j_graph.py`          | Neo4j Aura connection + push              |
| `LegalEngine`             | `legal_engine.py`         | Legal engine wrapper                      |
| `AIAssistantService`      | `ai_assistant/service.py` | LLM-powered assistant actions             |
| `VersionService`          | `version_control.py`      | Document versioning (snapshot/restore)    |
| `AuditLogService`         | `services/audit.py`       | Hash-chained audit trail                  |

### Shared Abstractions (`app/shared/`)

| Module                     | Purpose                                                              |
| -------------------------- | -------------------------------------------------------------------- |
| `case_keys.py`             | Canonical key naming contract across modules                         |
| `case_resolver.py`         | `CaseResolver` — disambiguate CaseFile vs Adjudication IDs           |
| `document_case_manager.py` | `DocumentCaseManager` — parameterized CRUD for CaseFile/Adjudication |
| `case_query_service.py`    | `CaseQueryService` — lookup queries for case resolution              |
| `context_derivers.py`      | Derived fields (applicable_sections, violations, case_track)         |

### Utility Functions (`app/utils/`)

| Module             | Purpose                                               |
| ------------------ | ----------------------------------------------------- |
| `storage.py`       | S3-compatible storage (R2/B2/Cloudinary)              |
| `pdf_utils.py`     | WeasyPrint HTML→PDF, photo embedding, post-processing |
| `fso_data.py`      | FSO (Food Safety Officer) data sync from markdown     |
| `lookup.py`        | FSSAI/CE license lookups                              |
| `suggester.py`     | Section suggestion logic                              |
| `filters.py`       | Jinja2 custom filters                                 |
| `qstash_client.py` | QStash webhook-based task publishing                  |
| `sync.py`          | Multi-target sync restore functions                   |

## 14. Concurrency & Data Integrity Patterns

### Optimistic Concurrency Control

- `version_id` columns on `CaseFile`, `Adjudication` (SQLAlchemy `version_id_col`)
- `StaleDataError` → HTTP 409 Conflict responses
- Tested via `test_concurrency_inspection.py`

### Audit Chain

- Primary: `AuditLog` (hash-chained SHA-256, PostgreSQL advisory locks)
- Secondary: `RecordAudit` (change capture on CaseFile/Adjudication/Bill)
- `verify_audit_chain()` method for integrity verification

### Unique Code Generation

- `CodeSequence` table for sequence-based IDs
- PostgreSQL advisory locks for race-safe allocation
- `generate_sample_code()` uses this pattern

### Transaction Boundaries

- `log_audit()` wraps read-compute-insert in a single transaction
- Advisory locks on PostgreSQL for same-entity serialization
- SQLite uses DB-level write lock (no advisory locks)

## 15. Environment Variables Summary

### Core Infrastructure

| Variable       | Default                    | Purpose                           |
| -------------- | -------------------------- | --------------------------------- |
| `DATABASE_URL` | SQLite                     | PostgreSQL or SQLite database URL |
| `SECRET_KEY`   | Auto-generated             | Flask session signing             |
| `REDIS_URL`    | `redis://localhost:6379/0` | Celery broker + cache             |

### External Services

| Variable                               | Default | Purpose                      |
| -------------------------------------- | ------- | ---------------------------- |
| `SPREADSHEET_ID`                       | -       | Google Sheets spreadsheet ID |
| `GOOGLE_CREDENTIALS_JSON`              | -       | Service account credentials  |
| `NEO4J_URI/USER/PASSWORD`              | -       | Neo4j Aura connection        |
| `NEO4J_ALLOW_WRITE`                    | unset   | Fail-closed write gate       |
| `R2_ACCESS_KEY/SECRET/BUCKET/ENDPOINT` | -       | S3-compatible storage        |

### Sync Configuration

| Variable                        | Default | Purpose                      |
| ------------------------------- | ------- | ---------------------------- |
| `ENABLE_AIRTABLE_SYNC`          | `false` | Airtable sync active         |
| `ENABLE_EXCEL_SYNC`             | `false` | Excel Online sync dormant    |
| `ENABLE_BACKUP_SCHEDULE`        | `false` | QStash daily backup schedule |
| `AIRTABLE_API_KEY/BASE_ID`      | -       | Airtable credentials         |
| `MS_TENANT_ID/CLIENT_ID/SECRET` | -       | Azure AD credentials         |

### RAG Configuration

| Variable                | Default                                | Purpose                    |
| ----------------------- | -------------------------------------- | -------------------------- |
| `RAG_ENABLED`           | `true`                                 | Overall RAG enable/disable |
| `RAG_QDRANT_URL`        | `""`                                   | Qdrant server URL          |
| `RAG_QDRANT_API_KEY`    | -                                      | Qdrant Cloud API key       |
| `RAG_QDRANT_COLLECTION` | `fssai_legal_768`                      | Primary collection         |
| `RAG_VECTOR_SIZE`       | `768`                                  | Embedding dimensionality   |
| `RAG_EMBEDDING_MODEL`   | `all-mpnet-base-v2`                    | Embedding model            |
| `RAG_RERANKER_MODEL`    | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker model             |

### Feature Flags

| Variable                 | Default     | Purpose                    |
| ------------------------ | ----------- | -------------------------- |
| `RAG_KG_EXPANSION`       | `false`     | KG graph expansion         |
| `RAG_USE_AGENT_PIPELINE` | `false`     | LangGraph agent pipeline   |
| `RAG_AGENT_HITL`         | `false`     | Human-in-the-loop review   |
| `RAG_AGENT_CHECKPOINTER` | `memory`    | Checkpoint backend         |
| `RAG_KG_FUSION`          | `false`     | KG contract fusion         |
| `AI_ASSISTANT_PROVIDER`  | `""`        | AI assistant LLM provider  |
| `PDF_ENABLE_HYPERLINKS`  | -           | Toggle PDF link annotation |
| `PDF_ENABLE_QR_CODES`    | -           | Toggle QR in PDFs          |
| `DISABLE_PDF_GENERATION` | `1` (in CI) | Skip actual PDF generation |

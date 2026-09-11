# RAG Implementation

Legal intelligence retrieval-augmented generation pipeline for the NSA Webservice — corpus ingestion, hybrid retrieval, grounded generation, verification, evaluation, and the LangGraph agent pipeline.

## 1. Architecture Overview

The RAG pipeline is organized by phase, each building on the previous:

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 1: Ingestion                       │
│  Source → Loader → Clean → Dedup → Chunk → Embed → Index   │
│  Files: app/rag/ingestion.py, app/rag/qdrant_indexer.py    │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                   Phase 1: Retrieval                        │
│  Query → Classify → Hybrid Retrieve → Rerank → Log         │
│  Files: app/rag/retrieval/, app/rag/tasks.py               │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                   Phase 2: Generation & Intelligence        │
│  Generation: chunks → Grounded answer                      │
│  Intelligence: QueryPlanner → Evidence Tasks → DAG         │
│  Files: app/rag/generation/, app/rag/planning/             │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                  Phase 3: Verification                      │
│  Claims → Verify → Citations → Score → Report              │
│  Files: app/rag/verification/                              │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                  Phase 4: Evaluation                        │
│  Dataset → Pipeline → Metrics → Report → Storage           │
│  Files: app/rag/evaluation/                                │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                 Phase 5: Resilience                         │
│  Circuit breaker, stub fallback, retry, degraded mode      │
│  Files: app/rag/resilient.py, app/rag/circuit_breaker.py  │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│             LangGraph Agent Pipeline (M3–M5)                │
│  Classify → Plan → [Linear | DAG] → Verify → Finalize     │
│  Files: app/rag/agent/                                     │
└─────────────────────────────────────────────────────────────┘
```

### Key design principles

- **Single retrieval composition root** (`app/rag/retrieval/factory.py`): all construction decisions centralized — cannot drift across consumers.
- **Injectable cache** (`app/rag/retrieval/cache.py`): `RetrievalCache` (LRU + TTL) with optional parameter on `run_retrieval_pipeline` — testable in isolation (ADR-0002).
- **Lazy imports**: optional dependencies (LangGraph, Qdrant, sentence-transformers) are never imported at module load. The legacy pipeline and the rest of the app are untouched when they are absent.
- **Plain-function entry points**: every Celery task (`retrieve_task`, `generate_task`, `evaluate_task`, `ingest_corpus_task`, `embed_and_index_task`) wraps a plain function — tests and routes call the plain function directly.
- **Shared config seam** (`app/shared/config.py`): all feature flags resolved through `cfg.*` (Pattern A — Flask config in-context, env var out-of-context).

---

## 2. Phase 1 — Ingestion & Corpus Pipeline

**Status: Complete**

**Entry points:**

- `app/rag/ingestion.py` — `run_ingest_document()`, `ingest_corpus_dir()`, `make_ingestion_pipeline()`
- `app/rag/tasks.py` — Celery wrappers (`embed_and_index_task`, `ingest_corpus_task`)

**Pipeline flow:**

```
source file/text
  → DocumentLoaderFactory (R0 — files: pdf/docx/txt)
  → DocumentCleaner (clean text)
  → ChunkDeduper (SHA-256 dedup at document + chunk level)
  → Chunker → EmbeddingService → QdrantStore (Day 1–2)
  → QdrantIndexer (Day 3 — chunk → embed → upsert with retry)
```

**Components:**

| Component             | Module                           | Purpose                                  |
| --------------------- | -------------------------------- | ---------------------------------------- |
| DocumentLoaderFactory | `app.document_loader`            | Load PDF/DOCX/TXT files                  |
| DocumentCleaner       | `app.document_cleaner`           | Clean extracted text                     |
| ChunkDeduper          | `app/rag/dedup.py`               | Deduplicate by SHA-256 fingerprint       |
| Chunker               | `app/rag/chunker.py`             | Split text into chunks with metadata     |
| EmbeddingService      | `app/rag/embedding_service.py`   | Generate dense embeddings                |
| QdrantStore           | `app/rag/qdrant_client.py`       | Qdrant vector store client               |
| QdrantIndexer         | `app/rag/qdrant_indexer.py`      | Chunk → embed → upsert pipeline          |
| LegalDocumentOCR      | `app/rag/legal_ocr.py`           | OCR scanned/image-only PDFs              |
| DocumentClassifier    | `app/rag/document_classifier.py` | Classify §5.1 metadata (type, authority) |

**Optional enrichment** (wired in when `RAG_FULL_ENRICHMENT=true`):

- MetadataAdapter — enrich document metadata
- CitationAdapter — per-chunk citation extraction
- CrossRefAdapter — per-chunk cross-reference extraction
- LegalEntityExtractor — legal entity extraction (§3.4)
- ChunkQualityValidator — per-chunk quality grading

**Ingestion routes:**

- `POST /api/rag/ingest` — ingest one document (raw text OR file path)
- `POST /api/rag/ingest/corpus` — ingest all supported files in a directory
- `GET /api/rag/health` — health probe with LLM mode, HITL durability signals

---

## 3. Phase 1 — Retrieval Pipeline

**Status: Complete**

**Entry points:**

- `app/rag/tasks.py` — `run_retrieval_pipeline()`, `retrieve_task`
- `app/rag/retrieval/factory.py` — `build_hybrid_retriever()`, `build_dense_retriever()`, `build_sparse_retriever()`, `build_reranker()`

**Pipeline flow:**

```
Query
  → QueryClassifier (intent/complexity classification)
  → QueryParser (parse filters, etc.)
  → HybridRetriever (dense + sparse + reranker fusion)
  → RetrievalLogger (audit log — runs on every call, cache hits included)
  → RetrievalCache (LRU + TTL — skips Qdrant on cache hit)
```

**Retrieval components:**

| Component               | Module                                  | Purpose                              |
| ----------------------- | --------------------------------------- | ------------------------------------ |
| QueryClassifier         | `app/rag/retrieval/query_classifier.py` | Classify query into legal query type |
| QueryParser             | `app/rag/retrieval/`                    | Parse query into filters             |
| DenseRetriever          | `app/rag/retrieval/dense_retriever.py`  | Dense vector search (Qdrant)         |
| SparseRetriever         | `app/rag/retrieval/sparse_retriever.py` | Sparse BM25 search (Qdrant)          |
| EnsembleReranker        | `app/rag/retrieval/reranker.py`         | Ensemble: CE head + lightweight head |
| Reranker                | `app/rag/retrieval/reranker.py`         | Single CE-head reranker              |
| HybridRetriever         | `app/rag/retrieval/hybrid_retriever.py` | Fuse dense + sparse + reranker       |
| RetrievalCache          | `app/rag/retrieval/cache.py`            | LRU + TTL memoization (injectable)   |
| RetrievalLogger         | `app/rag/retrieval/logger.py`           | Audit logging for every query        |
| RetrievalStage registry | `app/rag/retrieval/stages.py`           | Post-retrieval enrichment stages     |

**Feature flags:**

- `RAG_RETRIEVAL_CACHE` — enable retrieval cache (LRU + TTL, 512 entries, 600s TTL)
- `RAG_QDRANT_BM25` — use Qdrant in-cluster BM25 (no local fastembed)
- `RAG_ENSEMBLE_RERANK` — enable ensemble reranker (CE head + lightweight head)
- `RAG_LEGAL_QUERY_TYPING` — enable legal query type classification
- `RAG_IDENTIFIER_ROUTE` — enable identifier-based retrieval routing

**Identifier routing** (`app/rag/retrieval/identifier.py`): When enabled, builds a lexical "{Act} section {N}" query from detected identifiers in the question text, run as a parallel additive arm. Production form of the single decisive lever measured offline (+13.3pp candidate-pool ceiling → 100% after section-stamp backfill).

**Enrichment stages** (`app/rag/retrieval/stages.py`): Post-retrieval enrichment via `RetrievalStage` registry — `legal_identity`, `reference_expansion`, `evidence_selector`. Each is independently feature-gated and error-isolated.

---

## 4. Phase 2 — Grounded Generation

**Status: Complete**

**Entry points:**

- `app/rag/tasks.py` — `run_generation_pipeline()` (with chunk override), `generate_task`
- `app/rag/routes.py` — `POST /api/rag/generate`

**Pipeline flow:**

```
Query + Chunks (or run retrieval first)
  → SubQueryDecomposer (decompose compound queries)
  → [KG Contract Fusion — if RAG_KG_FUSION] (query → graph provisions → RRF-fuse)
  → [KG Expansion — if RAG_KG_EXPANSION] (chunks → Neo4j → RRF-fuse)
  → GroundedGenerationService.generate()
  → [Hallucination Detection — if RAG_HALLUCINATION_DETECTOR]
  → [Citation Validation]
```

**Components:**

| Component                 | Module                                   | Purpose                            |
| ------------------------- | ---------------------------------------- | ---------------------------------- |
| GroundedGenerationService | `app/rag/generation/grounded_service.py` | Core generation engine             |
| ContextBuilder            | `app/rag/generation/context_builder.py`  | Build prompt context from chunks   |
| CitationTracker           | `app/rag/generation/citation_tracker.py` | Track citations through generation |
| PromptTemplate            | `app/rag/generation/prompt_template.py`  | LLM prompt templates               |
| LLM Client                | `app/rag/generation/llm_client.py`       | LLM API client (stub or live)      |
| ResponseSanitizer         | `app/rag/generation/sanitizer.py`        | Sanitize LLM output                |
| TokenCounter              | `app/rag/verification/token_counter.py`  | Token cost estimation              |

**Subquery decomposition** (`app/rag/retrieval/subquery_decomposer.py`): Compound queries (multiple section references with conjunctions) are decomposed into sub-queries. Each sub-query runs the retrieval pipeline independently; results are merged and deduplicated by chunk_id.

**KG contract fusion** (`kg/hybrid.py`): When `RAG_KG_FUSION` is enabled, the graph-RAG retrieval contract (query → provisions) is run and RRF-fused (k=60) into the ranked context. Independent of retrieved chunk IDs — can surface gold provisions vector retrieval missed. Best-effort: missing Neo4j degrades gracefully.

**KG graph expansion** (`kg/hybrid.py`): When `RAG_KG_EXPANSION` is enabled, retrieved chunk IDs are expanded through Neo4j into structured legal context (provisions, domains, temporal status, authorities, cross-refs) and injected as additional [Source n] blocks in the prompt. Fused via RRF (k=60).

---

## 5. Phase 2 Intelligence Layer — Query Planning & Evidence Optimization

**Status: Complete**

**Components:**

| Component            | Module                                     | Purpose                                                  |
| -------------------- | ------------------------------------------ | -------------------------------------------------------- |
| QueryPlanner         | `app/rag/planning/query_planner.py`        | Intent extraction, requirements, task decomposition, DAG |
| EvidenceOptimizer    | `app/rag/planning/evidence_optimizer.py`   | Optimize evidence selection                              |
| FailureClassifier    | `app/rag/planning/failure_classifier.py`   | Classify failure types                                   |
| KG Reasoner          | `app/rag/planning/kg_reasoner.py`          | KG-based reasoning                                       |
| Profiles             | `app/rag/planning/profiles.py`             | Named query profiles                                     |
| Targeted Retry       | `app/rag/planning/targeted_retry.py`       | Failure-aware retry query generation                     |
| Three-Stage Reranker | `app/rag/planning/three_stage_reranker.py` | Multi-stage reranking                                    |
| Adaptive Retrieval   | `app/rag/planning/adaptive_retrieval.py`   | Adaptive retrieval parameters                            |

**Query Planner architecture:**

```
Query → Intent & Requirement Parse → Requirement Extraction →
Minimum Sufficient Task Decomposer → Evidence DAG
```

**Query Planner output:**

- **Intent** (17 types): IDENTIFICATION, LOOKUP, DEFINITION, PROHIBITION, DUTY, RIGHT, POWER, PENALTY, EXCEPTION, PROCEDURE, SCOPE, APPLICABILITY, COMPARISON, TEMPORAL, JURISDICTION, CROSS_REFERENCE, CASE_LAW, MULTI_HOP, FACT_PATTERN, COMPLIANCE_ASSESSMENT
- **Complexity** (3)

```

**Key files:**

- `app/rag/planning/query_planner.py` — main QueryPlanner class with intent extraction, requirement parsing, decomposition, and DAG construction
- `app/rag/planning/targeted_retry.py` — failure-aware retry query generation (P2.6)
- `app/rag/planning/failure_classifier.py` — failure classification (V2 plan item 16)
- `app/rag/planning/evidence_optimizer.py` — evidence optimization
- `app/rag/planning/adaptive_retrieval.py` — adaptive retrieval parameters
- `app/rag/planning/three_stage_reranker.py` — multi-stage reranking
- `app/rag/planning/profiles.py` — named query profiles
- `app/rag/planning/kg_reasoner.py` — KG-based reasoning

**Evidence Task DAG:**

Evidence tasks are organized into a DAG with dependency-based edges (Phase 1): foundational evidence types (provisions, definitions, cross-references) are wave-1; penalties, exceptions, fact application are wave-2, depending on wave-1 tasks in the same legal domain.

**Routing economy** (`app/rag/agent/routing_economics.py`):

- **Phase 3 (item 18)**: Budget-aware complexity routing — picks strategy (direct/moderate/deep) based on query complexity
- **Budget tiers** for tasks/retrieval rounds/LLM calls per strategy
- **Exhaustion detection** shared by DAG budget gate and linear retry router

**Budget tiers:**

```

direct: max_tasks=0, max_retrieval_rounds=3, max_documents=30, max_llm_calls=4
moderate: max_tasks=4, max_retrieval_rounds=4, max_documents=60, max_llm_calls=8
deep: max_tasks=8, max_retrieval_rounds=5, max_documents=90, max_llm_calls=16

```

---

## 6. Phase 3 — Verification & Hallucination Detection

**Status: Complete**

**Components:**
| Component | Module | Purpose |
|-----------|--------|---------|
| ClaimExtractor | `app/rag/verification/claim_extractor.py` | Extract factual claims from responses |
| EvidenceVerifier | `app/rag/verification/evidence_verifier.py` | Verify claims against evidence (rapidfuzz) |
| CitationValidator | `app/rag/verification/citation_validator.py` | Validate citations against chunks |
| HallucinationDetector | `app/rag/verification/hallucination_detector.py` | Orchestrate detection pipeline |
| GroundednessScorer | `app/rag/verification/score.py` | Score response groundedness |
| TokenCounter | `app/rag/verification/token_counter.py` | Estimate token costs |

**Hallucination detection pipeline:**

1. Extract claims — `ClaimExtractor`
2. Verify each claim — `EvidenceVerifier` (rapidfuzz)
3. Validate citations — `CitationValidator`
4. Score groundedness — `GroundednessScorer`
5. (Optional) LLM-based double-check — `GroundedLLMClient` (stub fallback)

**Thresholds:**
- **Hallucination threshold**: 0.50 — groundedness below this triggers expand-and-retry
- **Coverage threshold**: 0.2 — minimum chunk coverage for sufficiency
- **Relevance threshold**: 0.35 — minimum retrieval score
- **Authority threshold**: 0.5 — minimum authority weight

**Claim extraction** (`app/rag/verification/claim_extractor.py`):

- Rule-based extraction using spaCy NER for legal entities
- Structured output with start/end positions, claim text, and legal context

**Evidence verification** (`app/rag/verification/evidence_verifier.py`):

- Rapidfuzz-based textual similarity matching
- Supports multiple similarity metrics (jaro-winkler, levenshtein, etc.)
- Context window for matching

**Citation validation** (`app/rag/verification/citation_validator.py`):

- Validates citation references against retrieved chunks
- Checks section numbers, act references, and temporal validity
- Escalates failures to hallucination detection

**Groundedness scoring** (`app/rag/verification/scorer.py`):

- Combines claim verification, citation validation, and LLM confidence
- Produces 0.0–1.0 score indicating factual correctness

---

## 7. Phase 4 — Evaluation Framework

**Status: Complete**

**Components:**
| Component | Module | Purpose |
|-----------|--------|---------|
| RAGEvalDataset | `app/rag/evaluation/gold_dataset.py` | Golden benchmark dataset with gold provisions |
| DecompositionBenchmark | `app/rag/evaluation/benchmark.py` | Measure query planning quality |
| EvalRunner | `app/rag/evaluation/runner.py` | Run batch evaluation over datasets |
| CoverageMetrics | `app/rag/evaluation/metrics.py` | EvidenceTask coverage metrics |
| RAGAS Metrics | `app/rag/evaluation/ragas_metrics.py` | Deterministic RAGAS-style reference metrics |
| EvalStorage | `app/rag/evaluation/storage.py` | Persist evaluation results |

**Evaluation metrics:**

- **Task recall/precision/F1** — fraction of gold task kinds matched vs. predicted
- **Dependency accuracy** — Jaccard overlap of dependency edges
- **Over/under-decomposition rates** — entries with too many/too few tasks
- **Exact match rate** — strict multiset equality (decomposition atomicity)
- **Per-query class** — metrics broken out by query class
- **Coverage metrics** — EvidenceTask coverage (Phase 2+)
- **MRR** — Mean Reciprocal Rank for retrieval evaluation

**Benchmark datasets:**

- **Benchmark v1.0** — 150-question multi-domain golden benchmark
- **Gold provisions** — gold legal provisions, sources, and rubric
- **Review-conflict report** — identified conflicts between benchmark entries

**Evaluation API:**

- `POST /api/rag/eval` — batch evaluation endpoint
- `run_evaluate()` — evaluates dataset over a pipeline callable
- `evaluate_one()` — evaluates a single query

---

## 8. Phase 5 — Resilience & Integration

**Status: Complete**

**Components:**

| Component | Module | Purpose |
|-----------|--------|---------|
| ResilientRAGPipeline | `app/rag/resilient.py` | Circuit breaker + stub fallback |
| CircuitBreaker | `app/rag/circuit_breaker.py` | Open/closed/half-open state management |
| CircuitBreakerRegistry | `app/rag/circuit_breaker.py` | Global circuit breaker registry |

**Resilience features:**

- **Circuit breaker** — prevents hammering dead endpoints
- **Stub fallback** — provides canned responses when primary path is unavailable
- **Retry logic** — with exponential backoff and circuit breaker integration
- **Degraded mode** — returns partial responses with explanatory notes
- **Health monitoring** — tracks success/failure rates for circuit state transitions

**Circuit breaker states:**

- **Closed** — normal operation (success within threshold)
- **Open** — fail-fast mode (requests immediately fail)
- **Half-open** — probe for recovery (single request, success → close, failure → open)

---

## 9. LangGraph Agent Pipeline (M3–M5)

**Status: Complete**

**Architecture:**

```

classify ──► plan ──┬─ SIMPLE ─────────► retrieve ──► generate ──► verify ──► citation_quality
│ ▲ │ │ ──► finalize
│ └──── expand_query / targeted_retry ◄────────────┘
├─ cross_ref/case ─► multi_hop_retrieve ──► retrieve ──► …
└─ MULTI_PART/HOP ─► plan_tasks ──► budget_gate ──► execute_task
──► evidence_sufficiency ──► synthesize ──► verify
│ (abstain / targeted_retry)

```

**M3 (base agent):**

- **classify node** — `app/rag/agent/nodes.py::classify_node`
- **plan node** — `app/rag/agent/nodes.py::plan_node` (Phase 2.1 query planning)
- **retrieve node** — `app/rag/agent/nodes.py::retrieve_node` (calls `run_retrieval_pipeline`)
- **generate node** — `app/rag/agent/nodes.py::generate_node` (calls `run_generation_pipeline`)
- **verify node** — `app/rag/agent/nodes.py::verify_node` (runs hallucination detection)
- **citation_quality node** — `app/rag/agent/nodes.py::citation_quality_node` (Phase 0 validation)

**M4 (enhanced agent):**

- **evidence node** — Phase 1 (optional) — evidence set from apply_stages
- **budget_gate node** — `app/rag/agent/nodes.py::budget_gate_node` (P3 budget enforcement)
- **execute_task node** — `app/rag/agent/nodes.py::execute_task_node` (P1 DAG execution)
- **synthesize node** — `app/rag/agent/nodes.py::synthesize_node` (P1 result synthesis)
- **evidence_sufficiency node** — `app/rag/agent/nodes.py::evidence_sufficiency_node` (P2 sufficiency gate)
- **abstain node** — `app/rag/agent/nodes.py::abstain_node` (P2 abstention)

**M5 (human-in-the-loop):**

- **review node** — `app/rag/agent/nodes.py::review_node` (LangGraph interrupt for human decision)
- **route_after_review** — `app/rag/agent/graph.py::route_after_review`

**State schema** (`app/rag/agent/state.py`):

- **RAGState TypedDict** with all nodes' read/write fields
- **RAGResponse** compatible with legacy `run_generation_pipeline` result
- **AuditEntry** for execution trail

**Routing logic:**

- **route_after_verify** — P2: retry (expand/targeted) or finalize based on groundedness, claim groundedness, retry count, citation quality
- **route_after_plan** — P0: exactly one path per query, chosen from query plan (decomposition → plan_tasks, multi_hop → multi_hop_retrieve, SIMPLE/MULTI_PART → retrieve)
- **route_after_budget** — P3: budget exhausted → abstain, else execute_task
- **route_after_evidence** — P2: sufficiency → synthesize, else targeted_retry
- **route_after_retry** — P1: DAG path → plan_tasks, else linear → retrieve

**Checkpointer integration:**

- **memory checkpointer** — default for dev/tests (in-process, no durability)
- **postgres checkpointer** — production (requires `langgraph-checkpoint-postgres` + psycopg)
- **checkpointer_is_durable()** — returns True for postgres, False for memory

**Agent endpoints:**

- `POST /api/rag/query/agent` — invoke agent with thread_id for HITL
- `POST /api/rag/query/agent/resume` — resume paused M5 HITL run

**Performance features:**

- **Budget-aware routing** — prevents over-computation for simple queries
- **Targeted retry** — failure-aware retrieval targeting (P2.6)
- **Evidence sufficiency gate** — ensures task evidence meets thresholds
- **Multi-hop retrieval** — cross-reference/case-law queries

---

## 10. Knowledge Graph Integration

**Status: Complete**

**Components:**

| Component | Module | Purpose |
|-----------|--------|---------|
| Knowledge Graph | `kg/` | Neo4j-based legal knowledge graph |
| ProvisionIndexer | `kg/provision_indexer.py` | Index legal provisions |
| ProvisionLookup | `kg/provision_lookup.py` | Lookup provisions by query |
| Hybrid | `kg/hybrid.py` | RRF fusion of dense+sparse+KG |

**Integration points:**

- **Retrieval pipeline** — KG contract fusion (query → provisions → RRF)
- **Generation pipeline** — KG expansion (chunks → Neo4j → RRF)
- **Agent pipeline** — KG-based reasoning (cross-reference expansion)

**KG contract fusion** (`kg/hybrid.py`):

```

if cfg.kg_fusion:
provisions = provisions_for_query(query, LegalKGQueries(), limit=cfg.kg_max_provisions)
kg_chunks = provisions_to_retrieved_chunks(provisions, limit=cfg.kg_max_provisions)
if kg_chunks:
chunk_objects = rrf_fuse_chunks([chunk_objects, kg_chunks], rrf_k=60.0, top_k=slot_budget)

```

**KG graph expansion** (`kg/hybrid.py`):

```

if cfg.kg_expansion:
kg_expansion = KGContextExpander().expand_chunks(c.chunk_id for c in chunk_objects)
if kg_provisions:
kg_chunks = provisions_to_retrieved_chunks(kg_provisions, limit=cfg.kg_max_provisions)
chunk_objects = rrf_fuse_chunks([chunk_objects, kg_chunks], rrf_k=60.0, top_k=slot_budget)

````

**KG configuration flags:**

- `RAG_KG_FUSION` — enable KG contract fusion
- `RAG_KG_EXPANSION` — enable KG graph expansion
- `KG_PROVISION_LIMIT` — maximum provisions to retrieve

**Neo4j integration:**

- **Cypher APOC functions** — complex graph queries
- **Write guard** — `NEO4J_ALLOW_WRITE` config for production safety
- **Aura sync** — Neo4j Aura hosting with sync capabilities

---

## 11. Remote Inference Layer

**Status: Complete**

**Components:**

| Component | Module | Purpose |
|-----------|--------|---------|
| RemoteRerankClient | `app/rag/retrieval/remote_reranker.py` | TEI HTTP endpoint for CE scoring |
| RemoteEmbedder | `app/rag/retrieval/remote_embedder.py` | TEI HTTP endpoint for embedding generation |

**Configuration:**

- `RAG_RERANKER_ENDPOINT` — TEI rerank endpoint URL
- `RAG_RERANKER_TOKEN` — authentication token
- `RAG_RERANKER_TIMEOUT` — request timeout
- `RAG_RERANKER_REMOTE_FALLBACK` — fallback to local model when remote unavailable

**Usage:**

When `RAG_RERANKER_ENDPOINT` is set, the CE head is scored via TEI HTTP endpoint instead of local torch model. The remote client lazily builds the local CE as fallback when `RAG_RERANKER_REMOTE_FALLBACK` is on (default).

---

## 12. Configuration & Feature Flags

**Status: Complete**

**Config module:**

- **Module:** `app/shared/config.py`
- **Seam:** Pattern A (Flask config in-context, env var out-of-context)

**Key configuration flags:**

| Flag | Type | Default | Purpose |
|------|------|---------|---------|
| RAG_ENABLED | bool | true | Enable/disable RAG module |
| RAG_RETRIEVAL_CACHE | bool | false | Enable retrieval cache (LRU + TTL) |
| RAG_QDRANT_BM25 | bool | true | Use Qdrant in-cluster BM25 |
| RAG_ENSEMBLE_RERANK | bool | true | Enable ensemble reranker |
| RAG_LEGAL_QUERY_TYPING | bool | true | Enable legal query type classification |
| RAG_IDENTIFIER_ROUTE | bool | true | Enable identifier-based retrieval routing |
| RAG_KG_FUSION | bool | false | Enable KG contract fusion |
| RAG_KG_EXPANSION | bool | false | Enable KG graph expansion |
| RAG_HALLUCINATION_DETECTOR | bool | true | Enable hallucination detection |
| RAG_FULL_ENRICHMENT | bool | false | Enable full Phase 2 enrichment |
| RAG_AGENT_CHECKPOINTER | string | memory | Agent checkpointer type (memory/postgres) |
| RAG_RERANKER_ENDPOINT | string | null | TEI rerank endpoint URL |
| RAG_RERANKER_TOKEN | string | null | TEI authentication token |
| RAG_RERANKER_TIMEOUT | int | 30 | TEI request timeout |
| RAG_RERANKER_REMOTE_FALLBACK | bool | true | Fallback to local model when remote unavailable |

**Configuration patterns:**

- **Pattern A:** Flask `current_app.config` wins inside an app context; `os.environ` is read outside one
- **Resolution rule:** `seed_config_from_env(app)` is called from `create_app()` so env vars work identically in-context
- **Per-flag boolean conventions:** `opt_in` (string must be `"true"`) vs `opt_out` (anything but `"false"`), preserved historically and declared explicitly per row

---

## 13. Data Model

**Status: Complete**

**Core entities:**

- **RetrievedChunk** (`app/rag/retrieval/result.py`) — chunk with embedding, citations, metadata
- **Citation** (`app/rag/retrieval/result.py`) — legal citation reference
- **RAGResponse** (`app/rag/agent/state.py`) — response schema compatible with legacy pipeline
- **RAGState** (`app/rag/agent/state.py`) — TypedDict for LangGraph agent pipeline
- **IngestedDocumentResult** (`app/rag/ingestion.py`) — result of document ingestion

**Schema evolution:**

- **Backward compatibility:** All new fields optional (`total=False` TypedDict)
- **Migration compatibility:** No database migrations required (no schema changes)
- **Versioning:** State schema matches legacy `run_generation_pipeline` result dict

---

## 14. Task Orchestration

**Status: Complete**

**Task patterns:**

- **Celery task wrappers** — plain function + `bind=True` for self-injection
- **Resilient pipeline** — `ResilientRAGPipeline` with circuit breaker
- **Plain-function entry points** — tests and routes call plain functions directly

**Task definitions:**

```python
# Celery tasks in app/rag/tasks.py
- retrieve_task()         # wraps run_retrieval_pipeline
- generate_task()         # wraps run_generation_pipeline
- evaluate_task()         # wraps run_evaluate
- ingest_corpus_task()    # wraps ingest_corpus_dir
- embed_and_index_task()  # wraps run_embed_and_index
````

**Task registration:**

- **Auto-registration** — when Celery instance is available
- **Graceful degradation** — plain functions when Celery unavailable
- **Task naming** — `rag.*` with descriptive names

---

## 15. Corpus Inventory

**Status: Complete**

**Corpus sources:**

1. **CFSSAI corpus** — 2026-08-09 benchmark (2 scanned PDFs, 17 flagged)
2. **Benchmark v1.0** — 150-question multi-domain golden benchmark
3. **Legal instruments** — 58 instruments, 1,861 provisions, 27,343 chunks
4. **Custom corpora** — user-provided legal documents

**File formats:**

- **Supported:** PDF, DOCX, TXT
- **Unsupported:** Image-only PDFs (OCR fallback)
- **Processing:** DocumentLoaderFactory → DocumentCleaner → Chunker

**Collection strategy:**

- **Multi-domain** — separate Qdrant collection per legal domain
- **Domain mapping** — `app/rag/collections.py` maps domains to collections
- **Collection defaults** — `RAG_QDRANT_COLLECTION` config

**Ingestion capabilities:**

- **Single file** — `ingest_file()` for raw text or file paths
- **Corpus directory** — `ingest_corpus_dir()` for non-recursive batch processing
- **Text input** — `ingest_text()` for raw legal text
- **Enrichment** — optional Phase 2 enrichment when `RAG_FULL_ENRICHMENT=true`

---

## 16. Test Coverage Inventory

**Status: Complete**

**Test suites:**

| Test Suite    | Files                                                                  | Coverage               |
| ------------- | ---------------------------------------------------------------------- | ---------------------- |
| RAG Agent     | `tests/test_rag_agent_graph.py`, `tests/test_rag_agent_nodes.py`, etc. | 28+ tests              |
| RAG Benchmark | `tests/test_rag_benchmarks.py`                                         | 150+ questions         |
| RAG E2E       | `tests/test_rag_e2e.py`, `tests/test_rag_e2e_verification.py`          | End-to-end integration |
| RAG Interface | `tests/test_rag_interface.py`, `tests/test_rag_routes.py`              | HTTP endpoints         |
| RAG Phase 4   | `tests/test_rag_phase4_measurement.py`                                 | Evaluation metrics     |
| RAG Tasks     | `tests/test_rag_tasks.py`                                              | Celery task wrappers   |
| RAG UI GAPS   | `tests/test_rag_ui_gaps.py`                                            | UI functionality       |

**Testing approach:**

- **Unit tests** — mock external dependencies
- **Integration tests** — test components together
- **E2E tests** — test complete pipeline
- **Circuit breaker tests** — test resilience patterns
- **Benchmark tests** — validate against golden dataset

**Test coverage metrics:**

- **RAG evaluation** — 28 modules, 694 tests + 28 Agent A
- **Multi-hop agent** — reason/retrieve nodes
- **RAG Query UI** — weighted composite scoring + 6 priority improvements
- **Knowledge Graph** — Neo4j Aura sync with APOC/NEO4J_ALLOW_WRITE guard

---

## 17. Infrastructure & Deployment

**Status: Complete**

**Deployment targets:**

- **Render** — modern web hosting with auto-deploy
- **Docker** — containerized deployment (`Dockerfile`)
- **Celery** — task queue for async processing
- **Qdrant** — vector database for embeddings
- **PostgreSQL** — primary database for metadata and state

**Docker configuration:**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 5672

CMD ["python", "app.py"]
```

**Render configuration:**

```yaml
runtime: python3

services:
    - type: web
      runtime: python
      command: gunicorn --bind 0.0.0.0:8000 app:app
      env:
          - PYTHONUNBUFFERED=1
          - RAG_ENABLED=true
          - RAG_RETRIEVAL_CACHE=true
          - RAG_QDRANT_BM25=true
          - RAG_ENSEMBLE_RERANK=true
```

**Configuration management:**

- **Environment variables** — overrides for all feature flags
- **Flask config** — Pattern A resolution (in-context → env)
- **Config validation** — `tests/test_shared_config.py::test_env_example_keys_are_declared`

**Monitoring & observability:**

- **Health endpoint** — `/api/rag/health` with LLM mode, HITL durability signals
- **Circuit breaker metrics** — success/failure tracking
- **Audit logging** — hash-chained audit trail for all operations
- **Performance telemetry** — token usage, latency, throughput

---

## 18. Known Issues & Caveats

### Implementation limitations

1. **Image-only PDF ingestion** — requires OCR fallback (easyocr/torch/cv2)
2. **Language model dependence** — hallucination detection requires LLM API
3. **Neo4j availability** — KG integration degrades gracefully when Neo4j unavailable
4. **Remote inference** — TEI endpoint required for CE reranking

### Performance considerations

1. **Memory usage** — large embeddings consume significant RAM
2. **Query latency** — hybrid retrieval with multiple arms increases response time
3. **Cache efficiency** — LRU cache eviction policies may cause cache misses
4. **Storage costs** — vector storage for 27,343+ chunks incurs storage expenses

### Compatibility constraints

1. **Flask app context** — config resolution requires proper Flask app context
2. **Celery availability** — task queue required for production deployment
3. **Qdrant version** — compatible with Qdrant 1.x/2.x
4. **LangGraph version** — requires specific version for checkpointer integration

### Open issues

1. **Multi-language support** — limited to English legal documents
2. **Cross-document coherence** — long-range context across multiple documents
3. **Explainability** — limited explainability for retrieval results
4. **User feedback loop** — limited ability to incorporate user corrections

---

_Last updated: 2026-09-10_
_Documentation generated by automated analysis of codebase_

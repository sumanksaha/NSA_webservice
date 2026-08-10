# FSSAI Legal RAG Architecture — Implementation Workplan

**Document status:** Implementation baseline  
**Date:** 2026-08-08  
**Primary goal:** Build a production-oriented Retrieval-Augmented Generation (RAG) system for FSSAI law on top of the existing legal document-processing platform.

---

## 1. Executive Goal

Transform the existing legal document-processing system into a **grounded, provenance-preserving, amendment-aware FSSAI legal RAG system**.

The target system must be able to:

1. ingest and process Acts, Rules, Regulations, Notifications, Orders, circulars and other authoritative legal material;
2. preserve legal hierarchy such as Act → Chapter → Section → Subsection → Clause → Proviso → Schedule;
3. create legally meaningful chunks rather than arbitrary token windows;
4. generate and persist embeddings;
5. index embeddings in Qdrant;
6. combine semantic, lexical, metadata and knowledge-graph retrieval;
7. rerank retrieved evidence;
8. construct a source-traceable context for the LLM;
9. generate answers grounded in retrieved evidence;
10. provide precise legal citations and source locations;
11. distinguish current, amended and superseded provisions;
12. refuse or qualify answers when authoritative evidence is insufficient;
13. continuously measure retrieval, citation and grounding quality.

The existing platform already provides substantial infrastructure: document ingestion, OCR, cleaning, metadata extraction, legal paragraph/cross-reference processing, versioning, audit trails, persistence and lexical search. The implementation should **extend this foundation rather than replace it**.

---

# 2. Strategic Architecture

The target architecture is:

```text
Authoritative Legal Sources
          │
          ▼
Existing Document Pipeline
          │
          ├── Ingestion
          ├── OCR
          ├── Cleaning
          ├── Metadata extraction
          ├── Legal structure extraction
          ├── Cross-reference detection
          └── Versioning
          │
          ▼
Canonical Legal Representation
          │
          ▼
Legal-aware Chunking
          │
          ├───────────────┐
          ▼               ▼
   PostgreSQL          Embedding Service
   metadata/provenance      │
                             ▼
                           Qdrant
                             │
                             ▼
                       Retrieval Layer
                             │
             ┌───────────────┼────────────────┐
             ▼               ▼                ▼
        Lexical          Semantic          Graph
        retrieval        retrieval        expansion
             └───────────────┼────────────────┘
                             ▼
                          Reranker
                             │
                             ▼
                       Context Builder
                             │
                             ▼
                    Citation / Grounding
                       Verification
                             │
                             ▼
                            LLM
                             │
                             ▼
                    Answer + Citations
                    + Sources + Confidence
```

---

# 3. Core Design Principle

The system must be treated as a **legal evidence retrieval system first and an LLM application second**.

The priority order is:

```text
Source authority
    >
Document identity
    >
Legal structure
    >
Version / temporal validity
    >
Chunk quality
    >
Provenance
    >
Retrieval quality
    >
Reranking
    >
Generation
    >
UI convenience
```

A sophisticated chatbot over an incorrectly indexed corpus is a failure.

---

# 4. Guardrails

## 4.1 Source Authority Guardrail

Only approved/authoritative sources should be allowed into the authoritative legal corpus.

Every indexed document must have:

- source identity;
- issuing authority;
- document type;
- jurisdiction;
- publication/notification information where available;
- effective date where available;
- version;
- content hash;
- ingestion timestamp.

Unverified material must not silently become authoritative corpus material.

---

## 4.2 Provenance Guardrail

Every retrievable chunk must be traceable to:

```text
Document
→ Version
→ Page
→ Section
→ Subsection / Clause
→ Chunk
```

No anonymous vector should be permitted in the production legal collection.

---

## 4.3 Legal Hierarchy Guardrail

Chunking must preserve legal structure.

Do not rely exclusively on fixed token/character windows.

Preferred hierarchy:

```text
Act
 └── Chapter
      └── Section
           └── Subsection
                └── Clause
                     └── Proviso
                          └── Explanation
```

Where a legal unit is too large, split it only at semantically/legal-safe boundaries.

---

## 4.4 Version Guardrail

A retrieved provision must carry version information.

The system must distinguish:

```text
Current
Amended
Superseded
Repealed
Future-effective
Unknown
```

A superseded provision must not outrank the currently applicable provision merely because its wording is semantically similar.

---

## 4.5 Temporal Guardrail

For queries involving "current", "applicable", "at that time", "before amendment", etc., retrieval must consider:

```text
query date
effective_from
effective_to
amendment date
notification date
repeal/supersession status
```

Temporal ambiguity should be surfaced rather than hidden.

---

## 4.6 Citation Guardrail

Every material legal proposition in a generated answer should be linked to retrieved evidence.

The system must validate:

- document;
- section;
- subsection;
- clause;
- page;
- version;
- source.

The LLM must never be allowed to invent a citation.

---

## 4.7 Grounding Guardrail

The generation layer must follow:

1. Answer from retrieved evidence.
2. Do not invent legal provisions.
3. Do not invent notifications.
4. Do not invent case law.
5. Do not assume that model pretraining is authoritative.
6. Distinguish fact from inference.
7. Identify conflicting evidence.
8. State when evidence is insufficient.

---

## 4.8 Retrieval Guardrail

The system must not depend solely on vector similarity.

Use:

```text
Lexical retrieval
+
Semantic retrieval
+
Metadata filtering
+
Legal hierarchy
+
Knowledge graph
+
Temporal validity
+
Reranking
```

---

## 4.9 Amendment Guardrail

When multiple versions of a provision exist:

```text
Current applicable version
        >
historical applicable version
        >
superseded version
```

unless the query explicitly asks for historical law.

---

## 4.10 Human Review Guardrail

High-risk outputs should be presented as **research assistance**, not autonomous legal decisions.

The system should clearly identify:

- retrieved evidence;
- uncertainty;
- conflicting provisions;
- missing source material;
- assumptions.

---

## 4.11 Index Integrity Guardrail

Every embedding should record:

```text
chunk_id
embedding_model
embedding_version
vector_dimension
collection
qdrant_point_id
created_at
```

Changing the embedding model must not silently invalidate the corpus.

---

## 4.12 No Silent Failure

Failures must be observable.

Examples:

```text
OCR failed
metadata incomplete
chunking failed
embedding failed
Qdrant unavailable
citation unresolved
temporal status unknown
retrieval below threshold
grounding failed
```

These states must be logged and surfaced.

---

# 5. Phase 0 — Architecture Freeze

**Priority:** P0  
**Goal:** Establish contracts before implementation.

### Tasks

- define RAG architecture;
- define data contracts;
- define document lifecycle;
- define chunk lifecycle;
- define embedding lifecycle;
- define retrieval response schema;
- define citation schema;
- define evaluation schema;
- define failure states;
- define configuration variables.

### Deliverables

```text
app/rag/
app/rag/config.py
app/rag/schemas.py
app/rag/exceptions.py
architecture specification
data model specification
```

### Exit criterion

All downstream modules have stable interfaces.

---

# 6. Phase 1 — RAG Domain Model

**Priority:** P0

Create:

```text
RAGDocument
RAGChunk
RAGEmbedding
RAGQuery
RetrievalResult
Citation
Evidence
RetrievalTrace
```

### RAGDocument

Minimum conceptual fields:

```text
id
source_document_id
document_type
title
authority
jurisdiction
language
effective_date
notification_date
amendment_status
version
source_hash
canonical_hash
status
created_at
updated_at
```

### RAGChunk

```text
id
document_id
parent_chunk_id
chunk_type
text
normalized_text
section
subsection
clause
paragraph
schedule
page_start
page_end
effective_from
effective_to
status
content_hash
chunk_version
```

### RAGEmbedding

```text
chunk_id
embedding_model
embedding_version
vector_dimension
qdrant_collection
qdrant_point_id
created_at
```

### Exit criterion

Database migrations exist and all entities can be created, updated and queried.

---

# 7. Phase 2 — Canonical Legal Representation

**Priority:** P0

Create:

```text
app/rag/canonicalizer.py
```

### Functions

```python
canonicalize_document()
canonicalize_section()
canonicalize_clause()
canonicalize_citation()
canonicalize_metadata()
normalize_legal_whitespace()
normalize_section_reference()
normalize_notification_reference()
```

### Objective

Convert processed documents into a deterministic legal representation.

Example:

```json
{
  "document": "Food Safety and Standards Act, 2006",
  "document_type": "Act",
  "section": "32",
  "subsection": "1",
  "text": "...",
  "effective_status": "active",
  "source": "...",
  "page": 25
}
```

### Exit criterion

Repeated processing of the same source produces the same canonical representation and content hash.

---

# 8. Phase 3 — Legal-Aware Chunking

**Priority:** P0

Create:

```text
app/rag/chunking/
    legal_chunker.py
    hierarchy.py
    boundary.py
    overlap.py
```

### Functions

```python
chunk_document()
chunk_section()
chunk_subsection()
chunk_clause()
chunk_schedule()
split_large_clause()
merge_small_clauses()
detect_chunk_boundary()
preserve_legal_hierarchy()
```

### Rules

- preserve section identity;
- preserve subsection identity;
- preserve clause identity;
- preserve provisos;
- preserve definitions;
- preserve schedules;
- preserve references;
- avoid splitting a legal proposition unnecessarily;
- include sufficient parent context.

### Exit criterion

A manually reviewed benchmark corpus demonstrates that legal provisions remain interpretable after chunking.

---

# 9. Phase 4 — Provenance Engine

**Priority:** P0

Create:

```text
app/rag/provenance/
    tracker.py
    citation.py
    source_locator.py
```

### Functions

```python
create_provenance()
resolve_source_location()
resolve_page()
resolve_section()
resolve_clause()
resolve_document_version()
build_citation()
validate_citation()
```

### Exit criterion

Every chunk can be mapped back to its original source location.

---

# 10. Phase 5 — Embedding Service

**Priority:** P0

Create:

```text
app/rag/embeddings/
    service.py
    providers.py
    batch.py
    cache.py
```

### Functions

```python
embed_text()
embed_batch()
embed_query()
get_embedding_dimension()
get_embedding_model()
get_embedding_version()
embedding_exists()
cache_embedding()
```

### Requirements

- provider abstraction;
- batch processing;
- retry handling;
- rate-limit handling;
- deterministic metadata;
- embedding cache;
- model/version tracking.

### Exit criterion

A corpus can be embedded reproducibly and failed embeddings can be retried without duplicating valid vectors.

---

# 11. Phase 6 — Qdrant Vector Store

**Priority:** P0

Create:

```text
app/rag/vector_store/
    qdrant.py
    collections.py
    payload.py
    filters.py
```

### Functions

```python
create_collection()
delete_collection()
upsert_vectors()
upsert_batch()
delete_vectors()
get_vector()
search_vectors()
search_by_filter()
count_vectors()
rebuild_collection()
health_check()
```

### Payload must include

```text
chunk_id
document_id
document_type
title
authority
jurisdiction
section
subsection
clause
document_version
effective_from
effective_to
status
language
page_start
page_end
source_hash
chunk_hash
```

### Exit criterion

A complete legal corpus can be indexed and independently verified against the source database.

---

# 12. Phase 7 — RAG Indexing Orchestrator

**Priority:** P0

Create:

```text
app/rag/indexing/
    pipeline.py
    orchestrator.py
    tasks.py
    manifest.py
```

### Main functions

```python
index_document()
index_batch()
reindex_document()
incremental_index()
delete_document()
detect_stale_index()
validate_index()
```

### Pipeline

```text
Load
 ↓
OCR if required
 ↓
Clean
 ↓
Metadata
 ↓
Legal structure
 ↓
Canonicalize
 ↓
Chunk
 ↓
Provenance
 ↓
Embed
 ↓
Qdrant upsert
 ↓
Manifest
 ↓
Validation
```

### Exit criterion

One command/task can take an authoritative legal document from source to validated Qdrant vectors.

---

# 13. Phase 8 — Hybrid Retrieval

**Priority:** P0

Create:

```text
app/rag/retrieval/
    semantic.py
    lexical.py
    hybrid.py
    filters.py
    reranker.py
```

### Functions

```python
semantic_search()
lexical_search()
hybrid_search()
apply_metadata_filters()
merge_candidates()
deduplicate_results()
rerank_results()
```

### Retrieval architecture

```text
                Query
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
   Lexical               Semantic
   search                 search
       │                     │
       └──────────┬──────────┘
                  ▼
           Candidate pool
                  │
                  ▼
              Reranker
                  │
                  ▼
          Final evidence set
```

### Exit criterion

Hybrid retrieval outperforms each individual retrieval method on the evaluation set.

---

# 14. Phase 9 — Query Understanding

**Priority:** P1

Create:

```text
app/rag/query/
    classifier.py
    parser.py
    expansion.py
    planner.py
```

### Functions

```python
classify_query()
extract_legal_entities()
extract_sections()
extract_dates()
extract_authority()
detect_document_type()
detect_jurisdiction()
expand_query()
generate_search_variants()
build_query_plan()
```

### Query classes

At minimum:

```text
definition
legal_provision
procedure
authority/power
penalty
licensing
compliance
comparison
historical
amendment
case-specific
document lookup
```

### Exit criterion

The system selects appropriate retrieval strategies for different legal query classes.

---

# 15. Phase 10 — Knowledge Graph Retrieval

**Priority:** P1

The existing application already contains `Entity` and `Relationship` concepts. Extend these into the RAG retrieval system.

Create:

```text
app/rag/graph/
    entity_extractor.py
    relationship_extractor.py
    graph_retriever.py
    expansion.py
```

### Functions

```python
extract_entities()
extract_relationships()
resolve_entity()
resolve_entity_alias()
link_entities()
expand_graph()
find_related_law()
find_amendments()
find_dependencies()
find_citations()
```

### Exit criterion

A query can retrieve related provisions through legal relationships in addition to semantic similarity.

---

# 16. Phase 11 — Temporal and Amendment-Aware Retrieval

**Priority:** P0

Create:

```text
app/rag/legal_temporal/
    amendment.py
    validity.py
    timeline.py
    conflict.py
```

### Functions

```python
detect_amendment()
link_amendment()
build_version_chain()
resolve_current_version()
check_temporal_validity()
filter_superseded()
detect_conflicting_provisions()
```

### Exit criterion

The system can distinguish current and historical versions and answer historical queries without mixing legal regimes.

---

# 17. Phase 12 — Context Builder

**Priority:** P0

Create:

```text
app/rag/context/
    builder.py
    compressor.py
    deduplicator.py
    prioritizer.py
    citation_mapper.py
```

### Functions

```python
build_context()
deduplicate_chunks()
merge_adjacent_chunks()
rank_evidence()
compress_context()
preserve_citations()
build_source_map()
```

### Exit criterion

The LLM receives a compact, relevant and fully traceable evidence package rather than an unstructured list of chunks.

---

# 18. Phase 13 — Grounded Generation

**Priority:** P0

Create:

```text
app/rag/generation/
    answer.py
    prompts.py
    grounding.py
    refusal.py
```

### Functions

```python
generate_answer()
generate_legal_answer()
generate_summary()
generate_procedure()
generate_comparison()
check_grounding()
detect_unsupported_claims()
refuse_unanswerable_query()
```

### Generation rules

The model must:

- answer from retrieved evidence;
- cite material legal propositions;
- distinguish law from inference;
- identify uncertainty;
- identify conflicts;
- avoid unsupported legal conclusions.

### Exit criterion

Answers pass the grounding benchmark without unacceptable unsupported-claim rates.

---

# 19. Phase 14 — Citation Verification

**Priority:** P0

Create:

```text
app/rag/citations/
    resolver.py
    validator.py
    formatter.py
```

### Functions

```python
resolve_citation()
validate_section_exists()
validate_source_exists()
validate_page_reference()
validate_document_version()
validate_effective_date()
```

### Exit criterion

Invalid or hallucinated citations are rejected or flagged before final answer delivery.

---

# 20. Phase 15 — RAG API

**Priority:** P1

Create:

```text
app/rag/routes.py
```

### Endpoints

```text
POST /rag/query
POST /rag/search
POST /rag/index
POST /rag/reindex
GET  /rag/document/<id>
GET  /rag/chunk/<id>
GET  /rag/citations/<id>
GET  /rag/health
GET  /rag/stats
```

### Exit criterion

The RAG engine can be consumed independently of the frontend.

---

# 21. Phase 16 — RAG User Interface

**Priority:** P1

Create a dedicated legal research interface showing:

```text
Question
Answer
Legal citations
Source documents
Section/subsection
Page
Current/historical status
Related provisions
Retrieval confidence
Grounding status
```

The UI should prioritize evidence visibility over chatbot aesthetics.

### Exit criterion

A user can inspect the source supporting every material part of an answer.

---

# 22. Phase 17 — Evaluation Framework

**Priority:** P0

Create:

```text
app/rag/evaluation/
    dataset.py
    retrieval_eval.py
    answer_eval.py
    citation_eval.py
    regression.py
```

### Functions

```python
create_eval_dataset()
evaluate_retrieval()
evaluate_recall()
evaluate_precision()
evaluate_mrr()
evaluate_ndcg()
evaluate_answer_grounding()
evaluate_citation_accuracy()
evaluate_hallucination()
evaluate_temporal_accuracy()
run_regression_suite()
```

---

# 23. Evaluation Dataset

Create a manually validated benchmark containing at least:

```text
100+ legal questions
```

covering:

- definitions;
- FSS Act provisions;
- FSS Rules;
- regulations;
- licensing;
- inspection powers;
- improvement notices;
- sampling;
- adjudication;
- penalties;
- appeals;
- enforcement;
- amendments;
- historical provisions;
- cross-references;
- multi-document questions.

Each question should have:

```text
question
expected answer
gold document
gold section
gold chunk(s)
gold version
gold citation
acceptable alternatives
```

---

# 24. Evaluation Metrics

Minimum metrics:

| Metric | Purpose |
|---|---|
| Recall@5 | Required evidence retrieved |
| Recall@10 | Retrieval coverage |
| Precision@5 | Retrieval relevance |
| MRR | Correct evidence ranking |
| nDCG | Ranking quality |
| Citation accuracy | Citation correctness |
| Groundedness | Answer supported by evidence |
| Unsupported claim rate | Hallucination control |
| Temporal accuracy | Current vs historical law |
| Section accuracy | Correct legal provision |
| Latency | Production performance |

---

# 25. Phase 18 — Observability

**Priority:** P1

Create:

```text
app/rag/observability/
    logger.py
    metrics.py
    traces.py
```

Record:

```text
query
query_class
retrieval_strategy
filters
candidate_count
top_k
reranker_score
selected_chunks
embedding_model
LLM_model
tokens
latency
citations
grounding_score
answer
```

This enables continuous error analysis and model/index optimization.

---

# 26. Phase 19 — Administration and Index Management

**Priority:** P2

Create:

```text
app/rag/admin/
    dashboard.py
    indexing.py
    collections.py
    evaluation.py
```

Dashboard should expose:

```text
Total legal documents
Total chunks
Embedded chunks
Qdrant vectors
Failed embeddings
Stale chunks
Superseded documents
Current documents
Last indexing run
Index failures
Retrieval metrics
Citation metrics
Grounding metrics
```

---

# 27. Phase 20 — Production Hardening

**Priority:** P0

Before production:

- backup Qdrant;
- backup PostgreSQL metadata;
- verify index/document consistency;
- test Qdrant failure;
- test embedding provider failure;
- test LLM failure;
- test incomplete OCR;
- test malformed documents;
- test duplicate documents;
- test amended documents;
- test conflicting sources;
- test malicious prompt injection inside documents;
- test unsupported questions;
- test citation hallucination;
- test concurrent indexing;
- test reindexing;
- test rollback.

---

# 28. Milestone Gates

## Gate G0 — Architecture

```text
Architecture
Schemas
Interfaces
Configuration
```

**Pass:** contracts frozen.

---

## Gate G1 — Legal Corpus

```text
Canonical documents
Legal hierarchy
Legal chunks
Provenance
```

**Pass:** human-reviewed chunk benchmark passes.

---

## Gate G2 — Vector Infrastructure

```text
Embedding
Qdrant
Indexing
Reindexing
```

**Pass:** corpus is reproducibly indexed.

---

## Gate G3 — Retrieval

```text
Lexical
Semantic
Hybrid
Metadata filters
Reranking
```

**Pass:** retrieval benchmark reaches predefined target.

---

## Gate G4 — Legal Intelligence

```text
Query planning
Knowledge graph
Temporal retrieval
Amendment handling
```

**Pass:** current/historical and cross-reference tests pass.

---

## Gate G5 — Grounded Generation

```text
Context builder
LLM
Citation resolver
Grounding checker
```

**Pass:** answer benchmark reaches predefined target and unsupported claims remain below threshold.

---

## Gate G6 — Production

```text
Evaluation
Observability
Security
Failure recovery
Index integrity
```

**Pass:** production-readiness checklist complete.

---

# 29. Recommended Implementation Order

Do not implement all modules simultaneously.

Use this sequence:

```text
1. Architecture + schemas
        ↓
2. RAG models
        ↓
3. Canonicalizer
        ↓
4. Legal chunker
        ↓
5. Provenance
        ↓
6. Embedding service
        ↓
7. Qdrant
        ↓
8. Indexing orchestrator
        ↓
9. Hybrid retrieval
        ↓
10. Reranking
        ↓
11. Query planner
        ↓
12. Temporal/amendment retrieval
        ↓
13. Knowledge graph retrieval
        ↓
14. Context builder
        ↓
15. Citation validation
        ↓
16. Grounded generation
        ↓
17. Evaluation
        ↓
18. UI
        ↓
19. Production hardening
```

---

# 30. Definition of Done

The FSSAI RAG system should **not** be considered complete merely because:

```text
Qdrant works
+
LLM answers questions
```

It is complete only when:

```text
✓ Authoritative sources are controlled
✓ Legal hierarchy is preserved
✓ Chunks are legally meaningful
✓ Every chunk has provenance
✓ Embeddings are versioned
✓ Qdrant index is reproducible
✓ Lexical + semantic retrieval work together
✓ Metadata filters work
✓ Reranking works
✓ Amendment/version handling works
✓ Current and historical law are distinguishable
✓ Knowledge-graph relationships can improve retrieval
✓ Context is traceable
✓ Citations are validated
✓ Unsupported claims are detected
✓ Low-evidence questions are qualified/refused
✓ Retrieval has a benchmark
✓ Generation has a benchmark
✓ Citation accuracy is measured
✓ Grounding is measured
✓ Index failures are observable
✓ Reindexing is safe
✓ Production failure modes are tested
```

---

# 31. Strategic End State

The final system should evolve from:

```text
Legal document management system
```

into:

```text
FSSAI Legal Knowledge Infrastructure
```

with four major layers:

```text
┌───────────────────────────────────────────┐
│             APPLICATION LAYER             │
│ Legal research / drafting / case support │
└─────────────────────┬─────────────────────┘
                      │
┌─────────────────────▼─────────────────────┐
│              RAG INTELLIGENCE             │
│ Query planning / retrieval / reranking   │
│ graph / temporal / grounding / citation  │
└─────────────────────┬─────────────────────┘
                      │
┌─────────────────────▼─────────────────────┐
│              LEGAL KNOWLEDGE              │
│ Chunks / embeddings / graph / versions   │
│ provenance / metadata / citations        │
└─────────────────────┬─────────────────────┘
                      │
┌─────────────────────▼─────────────────────┐
│          DOCUMENT FOUNDATION              │
│ Ingestion / OCR / cleaning / extraction  │
│ versioning / audit / storage             │
└───────────────────────────────────────────┘
```

The implementation strategy is therefore **incremental rather than a rewrite**: preserve the existing document-processing foundation and progressively add the legal knowledge, vector retrieval, graph, temporal, provenance, grounding and evaluation layers.


---

# 32. Parallel Multi-Agent Implementation Strategy

## Objective

The implementation is explicitly designed to allow **two independent coding agents to work in parallel** without repeatedly editing the same modules, files or architectural surfaces.

The project is divided into two mutually exclusive implementation pathways:

```text
                    FROZEN CONTRACTS
                           │
             ┌─────────────┴─────────────┐
             │                           │
             ▼                           ▼
      PATHWAY A                      PATHWAY B
   Knowledge / Indexing          Retrieval / Intelligence
             │                           │
             │                           │
             └─────────────┬─────────────┘
                           │
                           ▼
                    INTEGRATION GATE
                           │
                           ▼
                   COMPLETE FSSAI RAG
```

The two agents should **not share implementation files**.

They may share only:

- agreed schemas;
- API/interface contracts;
- test fixtures;
- read-only existing application modules;
- integration-test definitions.

---

# 33. Agent Ownership Model

## Agent A — Legal Corpus, Indexing & Knowledge Infrastructure

### Mission

Build everything required to transform authoritative legal documents into a **validated, versioned, provenance-preserving searchable knowledge base**.

Agent A owns:

```text
Canonicalization
Legal chunking
Provenance
Embedding
Qdrant
Indexing
Document/version state
Temporal/amendment metadata
Knowledge graph construction
```

### Agent A MUST NOT implement

```text
Query planning
Retrieval orchestration
Reranking
Context construction
LLM prompting
Answer generation
Citation presentation
Frontend chatbot
Generation evaluation
```

---

## Agent B — Retrieval, Reasoning & Grounded Answering

### Mission

Build everything required to transform a user question into **ranked legal evidence and a grounded, cited answer**.

Agent B owns:

```text
Query understanding
Query classification
Query expansion
Lexical retrieval adapter
Semantic retrieval adapter
Hybrid retrieval
Filtering
Reranking
Graph retrieval consumption
Context construction
Citation validation
Grounded generation
Answer evaluation
RAG API/UI integration
```

### Agent B MUST NOT implement

```text
Document loaders
OCR
Document cleaning
Canonical legal representation
Chunk generation
Embedding generation
Qdrant indexing
Database migrations for Agent A models
Knowledge graph construction
```

---

# 34. Hard File Ownership Boundary

The following directory ownership is mandatory.

## Agent A

```text
app/rag/corpus/
app/rag/canonical/
app/rag/chunking/
app/rag/provenance/
app/rag/embeddings/
app/rag/vector_store/
app/rag/indexing/
app/rag/temporal/
app/rag/graph/
app/models/rag_corpus.py
app/models/rag_embedding.py
app/models/rag_graph.py
tests/rag/corpus/
tests/rag/chunking/
tests/rag/embeddings/
tests/rag/indexing/
tests/rag/vector_store/
tests/rag/temporal/
tests/rag/graph/
```

## Agent B

```text
app/rag/query/
app/rag/retrieval/
app/rag/reranking/
app/rag/context/
app/rag/generation/
app/rag/citations/
app/rag/evaluation/
app/rag/api/
app/rag/ui/
tests/rag/query/
tests/rag/retrieval/
tests/rag/reranking/
tests/rag/context/
tests/rag/generation/
tests/rag/citations/
tests/rag/evaluation/
```

## Shared but read-only

```text
app/document_loader/
app/document_cleaner/
app/ocr_pipeline/
app/metadata_extractor/
app/legal_analysis/
app/cross_reference/
app/version_control/
app/search/
```

Neither agent should modify these existing modules during parallel implementation unless a separately approved compatibility patch is required.

---

# 35. The Contract Layer

The only intentional bridge between the two pathways is a small **frozen contract layer**.

Create:

```text
app/rag/contracts/
    document.py
    chunk.py
    retrieval.py
    citation.py
    graph.py
    errors.py
```

These files should be frozen before parallel implementation begins.

## Agent A produces

```python
LegalDocumentRecord
LegalChunkRecord
EmbeddingRecord
SourceProvenance
TemporalValidity
GraphRecord
```

## Agent B consumes

```python
LegalChunkRecord
RetrievalCandidate
SourceProvenance
TemporalValidity
GraphRecord
```

## Agent B produces

```python
RetrievalResult
EvidenceSet
Citation
GroundingReport
RAGAnswer
```

The contracts should contain **interfaces and schemas, not implementation logic**.

---

# 36. Data Flow Contract

The cross-agent boundary is:

```text
AGENT A
Document
   ↓
Canonical document
   ↓
Legal chunks
   ↓
Provenance
   ↓
Embeddings
   ↓
Qdrant
   ↓
Graph
   │
   └───────────────┐
                   ▼
             CONTRACT LAYER
                   │
                   ▼
AGENT B
Query
   ↓
Query plan
   ↓
Retrieval
   ↓
Reranking
   ↓
Context
   ↓
Citation validation
   ↓
Grounded answer
```

Agent B should not need to know how Agent A generated a chunk.

Agent A should not need to know how Agent B will use a chunk.

---

# 37. Parallel Phase Schedule

The phases should be reorganized into the following parallel waves.

## Wave 0 — Joint Architecture Freeze

**Both agents participate.**

Tasks:

```text
[ ] Finalize schemas
[ ] Finalize contracts
[ ] Finalize Qdrant payload
[ ] Finalize chunk metadata
[ ] Finalize citation structure
[ ] Finalize error model
[ ] Finalize test fixtures
[ ] Freeze directory ownership
```

**No feature implementation should begin before Wave 0 is frozen.**

---

# 38. Wave 1 — Parallel Foundation

## Agent A

```text
A1. RAG corpus models
A2. Canonical legal representation
A3. Legal chunking
A4. Provenance
```

## Agent B

```text
B1. Query schemas
B2. Query classifier
B3. Query parser
B4. Retrieval interfaces
B5. Retrieval result schemas
```

These work independently.

---

# 39. Wave 2 — Parallel Infrastructure

## Agent A

```text
A5. Embedding service
A6. Embedding cache
A7. Qdrant client
A8. Qdrant collection manager
A9. Indexing pipeline
```

## Agent B

```text
B6. Lexical retrieval adapter
B7. Semantic retrieval adapter interface
B8. Metadata filter engine
B9. Candidate merger
B10. Deduplication
B11. Reranker interface
```

Agent B can use mocked retrieval candidates while Agent A builds the real vector infrastructure.

---

# 40. Wave 3 — Parallel Intelligence

## Agent A

```text
A10. Temporal/version engine
A11. Amendment relationships
A12. Knowledge graph extraction
A13. Graph persistence
A14. Graph retrieval interface
A15. Index validation
```

## Agent B

```text
B12. Hybrid retrieval
B13. Reranking
B14. Query expansion
B15. Query planning
B16. Context builder
B17. Citation resolver
```

Agent B must use the **contracted graph interface**, not Agent A's internal graph implementation.

---

# 41. Wave 4 — Parallel Completion

## Agent A

```text
A16. Batch indexing
A17. Incremental indexing
A18. Reindexing
A19. Stale-index detection
A20. Qdrant health checks
A21. Corpus statistics
A22. Corpus regression tests
```

## Agent B

```text
B18. Grounded generation
B19. Grounding checker
B20. Citation validator
B21. Refusal/insufficient-evidence logic
B22. RAG API
B23. RAG UI
B24. Answer evaluation
```

---

# 42. Wave 5 — Integration Gate

Neither agent should immediately merge the other pathway's implementation.

The integration should happen only after both pathways satisfy their independent acceptance criteria.

```text
Agent A COMPLETE
       │
       ├── corpus tests pass
       ├── indexing tests pass
       ├── Qdrant tests pass
       ├── provenance tests pass
       └── temporal tests pass

Agent B COMPLETE
       │
       ├── query tests pass
       ├── retrieval tests pass
       ├── reranking tests pass
       ├── grounding tests pass
       └── citation tests pass

       ↓
INTEGRATION
       ↓
END-TO-END RAG TESTS
```

---

# 43. Mock Boundary Strategy

To prevent blocking, Agent B should not wait for Agent A.

Create a deterministic mock corpus:

```text
tests/fixtures/rag/
    documents.json
    chunks.json
    retrieval_candidates.json
    graph_records.json
    citations.json
```

Agent B can build against:

```python
MockVectorStore
MockCorpus
MockGraphStore
MockEmbeddingProvider
```

Agent A can independently build the real:

```python
QdrantVectorStore
RealEmbeddingProvider
LegalCorpusIndexer
LegalGraphStore
```

Once the interfaces match, the mock implementations are replaced by the real adapters.

---

# 44. Integration Contract Example

Agent A guarantees:

```python
chunk = LegalChunkRecord(
    chunk_id="...",
    document_id="...",
    text="...",
    section="32",
    subsection="1",
    clause=None,
    page_start=25,
    page_end=25,
    source_provenance=...,
    temporal_validity=...
)
```

Agent B only assumes that this contract exists.

It does not care whether the chunk originated from:

```text
PDF
DOCX
OCR
manual correction
database
Qdrant
```

Similarly, Agent B guarantees:

```python
result = RetrievalResult(
    chunk_id="...",
    score=0.94,
    retrieval_method="hybrid",
    provenance=...
)
```

Agent A does not need to know how the result is reranked or presented.

---

# 45. No Cross-Pathway Coding Rule

During parallel development:

### Agent A cannot

```text
edit Agent B files
change retrieval algorithms
change generation prompts
change UI
change answer evaluation
```

### Agent B cannot

```text
edit Agent A indexing code
change chunking rules
change embedding implementation
change Qdrant collection schema
change corpus models
change graph persistence
```

If a change is required across the boundary:

```text
Agent identifies interface problem
        ↓
Create contract-change proposal
        ↓
Freeze current implementation
        ↓
Update contract
        ↓
Both agents adapt their own pathway
```

Do not solve cross-boundary problems by directly editing the other agent's code.

---

# 46. Git Branch Strategy

Use:

```text
main
│
├── rag/contracts
│
├── rag/agent-a-corpus
│
└── rag/agent-b-retrieval
```

Agent A works only on:

```text
rag/agent-a-corpus
```

Agent B works only on:

```text
rag/agent-b-retrieval
```

The contract branch is merged first.

Then:

```text
contracts
   ↓
agent-a-corpus
   ↓
agent-b-retrieval
   ↓
integration
```

or both feature branches can be developed simultaneously after the contract branch is frozen and then merged into an integration branch.

Recommended:

```text
main
  │
  └── rag-integration
          ├── agent-a-corpus
          └── agent-b-retrieval
```

---

# 47. Integration Branch

Create:

```text
rag-integration
```

Only the integration process should:

```text
connect real Qdrant
connect real embedding service
connect real graph
connect real retrieval
connect real LLM
run end-to-end tests
```

Neither Agent A nor Agent B should use the integration branch for day-to-day feature development.

---

# 48. Independent Definition of Done

## Agent A — DONE when

```text
✓ Legal documents can be canonicalized
✓ Legal hierarchy is preserved
✓ Chunks have deterministic IDs
✓ Every chunk has provenance
✓ Every chunk has temporal metadata
✓ Embeddings can be generated
✓ Embeddings are versioned
✓ Qdrant collection is populated
✓ Reindexing is deterministic
✓ Incremental indexing works
✓ Stale vectors are detected
✓ Graph data is available through contract
✓ Corpus validation passes
✓ Agent A tests pass
```

## Agent B — DONE when

```text
✓ Query classification works
✓ Query expansion works
✓ Lexical retrieval works
✓ Semantic retrieval adapter works
✓ Hybrid retrieval works
✓ Metadata filtering works
✓ Reranking works
✓ Context construction works
✓ Citation resolution works
✓ Citation validation works
✓ Grounding checks work
✓ Insufficient-evidence handling works
✓ Answer generation works
✓ RAG API works
✓ Evaluation suite passes
✓ Agent B tests pass
```

---

# 49. Final Integration Definition of Done

The overall project is complete only when:

```text
Document
   ↓
Agent A
   ↓
Validated Qdrant + provenance + graph
   ↓
Agent B
   ↓
Hybrid retrieval
   ↓
Reranking
   ↓
Grounded context
   ↓
Citation validation
   ↓
LLM
   ↓
Grounding verification
   ↓
Final answer
```

passes the end-to-end test suite.

Required tests:

```text
1. Basic legal lookup
2. Section-specific lookup
3. Multi-section question
4. Cross-reference question
5. Amendment question
6. Historical question
7. Current-law question
8. Conflicting-source question
9. Unsupported question
10. Citation hallucination test
11. OCR-noise test
12. Duplicate-document test
13. Superseded-law test
14. Prompt-injection-in-document test
15. Qdrant failure test
16. Embedding failure test
17. LLM failure test
18. Reindexing test
19. Concurrent indexing test
20. Full end-to-end RAG test
```

---

# 50. Parallelization Critical Path

The optimized critical path becomes:

```text
                   WAVE 0
             Contract Freeze
                     │
          ┌──────────┴──────────┐
          │                     │
          ▼                     ▼
       AGENT A               AGENT B
      Corpus Path          Retrieval Path
          │                     │
    Canonicalization       Query Planning
          │                     │
       Chunking             Retrieval
          │                     │
      Provenance            Reranking
          │                     │
      Embeddings             Context
          │                     │
       Qdrant               Grounding
          │                     │
       Indexing              Citation
          │                     │
       Graph                Generation
          │                     │
          └──────────┬──────────┘
                     ▼
              INTEGRATION
                     │
                     ▼
            END-TO-END TEST
                     │
                     ▼
             PRODUCTION RAG
```

This reduces the project from a strictly sequential implementation into two largely independent workstreams.

---

# 51. Recommended Agent Prompts

## Agent A Prompt

> You are Agent A — Legal Corpus & Knowledge Infrastructure.
>
> Implement only the Agent A pathway defined in `FSSAI_Legal_RAG_Implementation_Workplan.md`.
>
> Your responsibility is canonicalization, legal-aware chunking, provenance, embeddings, Qdrant, indexing, temporal/version handling and knowledge-graph construction.
>
> Do not implement retrieval orchestration, reranking, context building, LLM generation, UI or answer evaluation.
>
> Do not modify Agent B directories.
>
> Treat `app/rag/contracts/` as frozen.
>
> If a cross-boundary change is required, propose a contract change rather than editing Agent B code.
>
> Every implementation must have unit/integration tests and deterministic identifiers where applicable.
>
> Complete Agent A independently and report:
> 1. files created/modified;
> 2. functions implemented;
> 3. tests added;
> 4. tests passed;
> 5. contract assumptions;
> 6. remaining blockers.

---

## Agent B Prompt

> You are Agent B — Retrieval & Grounded Intelligence.
>
> Implement only the Agent B pathway defined in `FSSAI_Legal_RAG_Implementation_Workplan.md`.
>
> Your responsibility is query understanding, retrieval, hybrid search, filtering, reranking, context construction, citation validation, grounded generation, evaluation, API and UI.
>
> Do not implement document ingestion, canonicalization, chunking, embeddings, Qdrant indexing, corpus models or graph persistence.
>
> Do not modify Agent A directories.
>
> Treat `app/rag/contracts/` as frozen.
>
> Use mock corpus/vector/graph implementations whenever Agent A's real infrastructure is unavailable.
>
> If a cross-boundary change is required, propose a contract change rather than editing Agent A code.
>
> Complete Agent B independently and report:
> 1. files created/modified;
> 2. functions implemented;
> 3. tests added;
> 4. tests passed;
> 5. contract assumptions;
> 6. remaining blockers.

---

# 52. Optimization Principle

The two-agent strategy should maximize **parallel work while minimizing merge entropy**.

The rule is:

```text
Parallelize implementation,
NOT interfaces.
```

Therefore:

```text
Stable contracts
        +
Separate directories
        +
Separate tests
        +
Mock boundary
        +
Independent acceptance criteria
        =
Low-conflict parallel development
```

The integration phase should be relatively small. If integration becomes large, that is evidence that the interface boundary was insufficiently defined.

---

# 53. Final Target

The complete implementation should emerge from:

```text
               SHARED CONTRACTS
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
   PATHWAY A                 PATHWAY B
   Knowledge                Intelligence
   Infrastructure           Infrastructure
        │                         │
        │                         │
        ▼                         ▼
 Canonical corpus            Query planning
 Legal chunks                Hybrid retrieval
 Provenance                  Reranking
 Embeddings                  Context
 Qdrant                      Citation
 Temporal                    Grounding
 Graph                       Generation
 Indexing                    Evaluation
        │                         │
        └────────────┬────────────┘
                     ▼
              INTEGRATION GATE
                     │
                     ▼
             FSSAI LEGAL RAG
```

The key architectural decision is that **Agent A owns the truth substrate, while Agent B owns the evidence-to-answer pipeline**. Neither agent needs to understand the other's internal implementation. They communicate through frozen, typed contracts. This gives the project a clean path to parallel development while preserving the overall goal of a reliable FSSAI legal RAG system.

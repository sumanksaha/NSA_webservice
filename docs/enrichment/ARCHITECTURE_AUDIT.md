# ARCHITECTURE AUDIT — Existing FSSAI Legal RAG System

> **Phase 0 deliverable** of the *Incremental Legal Enrichment of Existing FSSAI
> RAG Chunks* project. Written **before** any enrichment is implemented.
> Audit date: 2026-08-10 · Audit version: 1.0
>
> **Status update (2026-08-10): Neo4j is now installed and connected** — the
> `neo4j` driver (6.2.0), `app/services/neo4j_graph.py` (APOC dynamic labels,
> 9 uniqueness constraints, 3 property indexes), `neo4j_aura_loader.py`, and
> `.env` credentials are all present and connectivity was verified. Finding #3
> below ("no Neo4j") reflected the state at audit time and is superseded for
> the enrichment graph phase; the case-file Neo4j sync path
> (`app/knowledge_graph/`) was already implemented and tested.
>
> **Headline findings**
> 1. The canonical chunk store is **Qdrant itself** (12,819 points) — the
>    SQLite `legal_chunk`/`legal_document` tables are **empty** (0 rows).
> 2. The real corpus is **12,819 chunks / 29 documents**, not the ~7,000 the
>    task assumed.
> 3. There is **no Neo4j** anywhere in the project. Two graph artifacts exist:
>    a JSON knowledge graph (`knowledge_graph.json`) and DB-persisted
>    case-level graphs (`entity`/`relationship` tables + Cytoscape UI).
> 4. Chunks are extremely granular (median 57 chars) and **75 % lack any
>    section metadata** — deterministic section attribution + cross-reference
>    resolution are the highest-value enrichments.
> 5. The LLM client (httpx, OpenAI-compatible) is already built, defaults to
>    OpenRouter free tier, and the project already has the full Phase 2
>    deterministic adapter chain (metadata / citation / crossref / entity /
>    quality) plus a 6-metric evaluation framework.

---

## 1. Current architecture

```
                    ┌─────────────────────────────────────────────────┐
                    │  corpus PDF/DOCX/TXT (source/provenance layer)  │
                    └───────────────────────┬─────────────────────────┘
                                            │ DocumentLoaderFactory (R0)
                                            ▼
                    DocumentCleaner ──▶ LegalParagraphEngine (chunker)
                                            │   Chunker → Chunk (§5.1 payload)
                                            ▼
        ┌─────────────── deterministic adapters (RAG_FULL_ENRICHMENT=true)
        │  MetadataAdapter · DocumentClassifier · CitationAdapter
        │  CrossRefAdapter · LegalEntityExtractor · ChunkQualityValidator
        ▼
        ┌───────────────────────────────────────────────────────────────┐
        │  Qdrant  collection "fssai_legal_768"   ← CANONICAL STORE      │
        │  12,819 points · 768-dim cosine dense + BM25 sparse (named)    │
        │  payload = §5.1 schema (full chunk metadata)                   │
        └───────────────▲───────────────────────────────────────────────┘
                        │ offline backup (restorable)
            backups/vector_store_fssai_legal_768_20260809_161941.json
                        │
        ┌───────────────┴───────────────────────────────────────────────┐
        │  Retrieval: DenseRetriever + SparseRetriever → HybridRetriever │
        │             (server-side Qdrant RRF, fallback client RRF k=60) │
        │             → optional Reranker (cross-encoder)                │
        ├───────────────────────────────────────────────────────────────┤
        │  Generation: ContextBuilder → GroundedLLMClient (httpx,        │
        │             OpenRouter/OpenAI-compatible, stub-able) →         │
        │             ResponseSanitizer → CitationTracker                │
        ├───────────────────────────────────────────────────────────────┤
        │  Verification: ClaimExtractor → EvidenceVerifier →             │
        │             CitationValidator → GroundednessScorer →           │
        │             HallucinationDetector → TokenCounter               │
        ├───────────────────────────────────────────────────────────────┤
        │  Evaluation: EvalRunner (6 metrics + MRR) → EvalStorage        │
        │             (RAGEvalResult table — empty today)                │
        └───────────────────────────────────────────────────────────────┘
```

**Stack** (from `pyproject.toml`): Flask 2.x · SQLAlchemy 2.x · SQLite (dev) /
PostgreSQL (prod) · `qdrant-client>=1.9` · `sentence-transformers>=3.0` ·
`fastembed>=0.4` · `rapidfuzz>=3.0` · `httpx`. **No OpenAI SDK, no neo4j, no
networkx** — the LLM client is plain httpx against an OpenAI-compatible
endpoint; graphs are JSON / relational.

### Data stores (what actually holds data)

| Store | Role | State (2026-08-10) |
| --- | --- | --- |
| **Qdrant** `fssai_legal_768` (cloud, live) | Canonical chunk store + vectors | 12,819 points, dense+sparse named vectors |
| `backups/vector_store_fssai_legal_768_20260809_161941.json` | Offline restorable backup | 151 MB, 12,819 points incl. vectors, `sha256` sealed |
| SQLite `instance/app.db` → `legal_chunk` / `legal_document` | Intended per-chunk registry | **0 rows** — corpus was ingested straight into Qdrant by scripts |
| SQLite → `rag_query_log` | Retrieval observability | 4 rows (dev traffic) |
| SQLite → `rag_eval_result` / `rag_eval_dataset` | Evaluation storage | 0 rows — eval dataset must be authored |
| `knowledge_graph.json` | JSON knowledge graph (docs/sections/authorities) | Built from `corpus_eval_result.json` — 24 docs / 57 sections / 3 authorities snapshot |
| `corpus_eval_result.json` | Corpus evaluation snapshot | **Currently corrupt** (JSONDecodeError on all encodings) |
| `entity` / `relationship` tables + `app/knowledge_graph/` | Case-level graphs (Case→FBO/Sample/Lab/Section/Evidence) | Used by case workflow UI |

### Runtime configuration (`.env`, masked values)

| Var | Value |
| --- | --- |
| `RAG_QDRANT_URL` / `RAG_QDRANT_API_KEY` | **Live cloud cluster** (GCP Australia) |
| `RAG_QDRANT_COLLECTION` | `fssai_legal_768` |
| `RAG_VECTOR_SIZE` | `768` |
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/all-mpnet-base-v2` |
| `RAG_ENABLE_SPARSE` | `true` (BM25 `text_sparse`, `Qdrant/bm25`) |
| `RAG_RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `RAG_FULL_ENRICHMENT` | `true` |
| `RAG_USE_STUB_LLM` | `false` (real LLM calls enabled) |
| `RAG_LLM_MODEL` | `poolside/laguna-s-2.1:free` (OpenRouter) |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` | set (per `.env`) |

---

## 2. Existing chunk schema

### 2a. Qdrant payload (§5.1 — the live schema, 27 keys)

Observed verbatim from the backup points:

```
chunk_id  document_id  document_uri  document_title  document_type
authority  jurisdiction  state  effective_date  enactment_date  amended_date
is_current  chunk_index  chunk_text  chunk_char_count  word_count
section_number  section_title  subsection  hierarchy_level  parent_chunk_id
citations  references  entities  confidence  created_at  embedding_model
content_hash
```

Key semantics:
* `chunk_id` = Qdrant point id (uuid4 hex) — **stable, canonical**.
* `chunk_text` = **original legal text — the authoritative evidence**. Must stay immutable.
* `citations` = `[{"section": "55", "type": "statutory"}]`-derived strings (payload stores plain strings).
* `references` = cross-references to other provisions.
* `entities` = **plain entity-name strings** (structured `[{name, type, confidence}]` lives only in the unused DB model).
* `content_hash` = SHA-256 of normalized chunk text (dedup fingerprint).
* `confidence` = paragraph-engine overall confidence (0–1).

### 2b. SQLAlchemy model `LegalChunk` (unused today)

```python
id  document_id  document_type  section_number  chunk_index  text
char_count  word_count  hierarchy_level  parent_id
citations  references  entities          # structured JSON forms
metadata_json                          # full Qdrant payload cache
content_hash  qdrant_point_id          # back-reference to the point
```

Design note: the model is a **superset** of the payload and already has a
`metadata_json` cache column. This is the natural home for enrichment fields
if we choose to persist enrichment to the DB (currently 0 rows).

---

## 3. Existing vector schema

* **Dense vector**: 768-dim, cosine, `sentence-transformers/all-mpnet-base-v2`
  — stored under the **named** vector `dense` (hybrid collections require all
  vectors named).
* **Sparse vector**: BM25 named vector `text_sparse` (`Qdrant/bm25` via
  fastembed, IDF modifier) — hybrid retrieval.
* **Collection**: `fssai_legal_768`; payload keyword indexes on 12 filterable
  fields (document_id, document_type, section_number, authority, …).
* **Embedding path**: `EmbeddingService.embed_text` → `QdrantStore.upsert`
  (batch 100, retry-once). Query side: `DenseRetriever.embed_query`.

**Consequence for enrichment**: re-embedding is **not required** for metadata
enrichment. Payload-only updates (additive keys) leave vectors untouched.
Only a retrieval-representation experiment (Strategy A vs B, Phase 9) would
touch embeddings — and even then only into a **new** collection.

---

## 4. Existing "graph" schema

**There is no Neo4j.** The task's Neo4j assumptions must be re-based onto what
exists:

### 4a. JSON knowledge graph — `knowledge_graph.json` (built by `scripts/build_kg.py`)

* Entity types: `document`, `section`, `authority`, `jurisdiction`, `reference`.
* Relationship types: `document_contains_section`, `document_issued_by`,
  `document_applies_to`, `section_cites`, `section_references`,
  `authority_oversees`, `document_amends`, `section_cooccurrence`.
* Snapshots: 24 documents, 57 unique sections, 3 canonical authorities,
  section→description map (`SECTION_CONTEXT`), authority hierarchy.
* Input (`corpus_eval_result.json`) is currently **corrupt** — the graph is a
  stale snapshot.

### 4b. Case-level graph — `app/knowledge_graph/engine.py` (+ `entity`/`relationship` tables)

* Nodes: `case`, `fbo`, `inspector`, `sample`, `lab`, `section`, `evidence`, `ancillary`.
* Edges: `INSPECTED_BY`, `SAMPLED_FROM`, `TESTED_AT`, `VIOLATED_SECTION`,
  `SUPPORTED_BY`, `REFERENCES`.
* Persisted for `case_file` records; rendered via Cytoscape.js
  (`templates/knowledge_graph/view.html`).

**Implication**: the "graph" layer for enrichment should be (a) an enriched
JSON knowledge graph following the 4a pattern (cheap, 8 GB-friendly,
diff-able) and/or (b) a **controlled relational graph** in the existing
`entity`/`relationship` tables — NOT a Neo4j server (memory, ops burden, no
existing code). A Neo4j migration is possible later behind the same ontology.

---

## 5. Current retrieval flow

1. `QueryClassifier` classifies the query (`section_lookup` | `case_law` |
   `provision_search` | `general_qa` | `amendment_query`).
2. `HybridRetriever.retrieve(query, top_k)`:
   * Server-side path: embed dense + sparse, `QdrantStore.hybrid_search`
     (prefetch + `Fusion.RRF`) in one round trip.
   * Fallback path: `DenseRetriever.search` (Qdrant) +
     `SparseRetriever.retrieve` (fastembed BM25 / rapidfuzz) → client-side RRF
     (k=60).
3. Optional `Reranker.rerank` (cross-encoder) reorders the fused top-k.
4. `ContextBuilder` assembles chunks → prompt; `GroundedLLMClient` generates;
   `CitationTracker` maps citations to chunk IDs.
5. `HallucinationDetector` scores groundedness; `RetrievalLogger` persists the
   query log (hash-chained).
6. `EvalRunner` computes 6 metrics (faithfulness, answer_relevance,
   context_precision, context_recall, citation_recall, groundedness) + MRR.

API surface (`/api/rag/*`): `health` · `ingest` · `ingest/corpus` ·
`generate` · `query` · `eval`.

---

## 6. Current limitations (relevant to enrichment)

1. **Section metadata sparsity** — 75 % of chunks lack `section_number` /
   `section_title` (audit MEDIUM). Deterministic section attribution is the
   single biggest enrichment opportunity.
2. **Extreme granularity** — median chunk = 57 chars (p50), mean 174.5.
   Tiny fragments mean (a) weak lexical match, (b) missing context for the
   LLM. `retrieval_summary` + document/paragraph-level context could help;
   **re-chunking is not recommended** (audit default decision).
3. **`LegalChunk`/`LegalDocument` DB registry empty** — no relational
   per-chunk metadata to query; everything lives in Qdrant payloads.
4. **`references` are near-empty in the live corpus** (CrossRefAdapter ran on
   re-ingestion, but payload audit shows few; the corrupt eval snapshot
   reported 0 references) — cross-reference resolution (Phase 6) is greenfield.
5. **`entities` payload holds plain names** — no types/confidence/grounding in
   Qdrant (structured form exists only in the unused model).
6. **Eval dataset absent** — `RAGEvalDataset` has 0 rows; the baseline-vs-
   enriched comparison (Phase 14) needs an authored dataset.
7. **No checkpoint/resume layer** for batch enrichment; **no enrichment store**.
8. **`corpus_eval_result.json` corrupt** — re-generate or treat
   `knowledge_graph.json` as the stale snapshot it is.
9. **Page numbers absent** — the §5.1 payload has no page field (audit
   `missing_page_info` = 100 %); pages are only recoverable from the PDF
   provenance layer.
10. LLM is a free-tier model (`poolside/laguna-s-2.1:free`) — LLM enrichment must
    be batch-small, prompt-disciplined (explicit/inferred/unknown), and
    guarded by the deterministic layer.

---

## 7. Files that will be modified (planned)

| File | Change |
| --- | --- |
| `scripts/enrichment/audit_chunks.py` | ✅ **created** — Phase 1 audit (read-only) |
| `app/rag/enrichment/deterministic.py` | ✅ **created** — Phase 3 deterministic extraction (section inheritance, legal location, cross-ref candidates, keywords, structural flags) |
| `app/rag/enrichment/validation.py` | ✅ **created** — Phase 12 structural validation invariants |
| `app/rag/enrichment/store.py` + `app/models/enrichment.py` | ✅ **created** — ORM enrichment store (ChunkEnrichment / EnrichmentCheckpoint / ChunkCrossReference / ResourceUsage) |
| `migrations/versions/add_chunk_enrichment_tables.py` | ✅ **created + applied** (dev DB) |
| `scripts/enrichment/backfill_registry.py` | ✅ **created + run** — legal_document/legal_chunk registry backfilled (29 docs / 12,819 chunks) |
| `scripts/enrichment/enrich_pipeline.py` | ✅ **created + run** — deterministic pipeline (checkpointing, resume, resource telemetry) — 12,819/12,819 validated, 0 failed |
| `scripts/enrichment/run_evaluation.py` | Pending — Phase 14 baseline vs enriched eval harness |
| `scripts/enrichment/run_ablation.py` | Pending — Phase 15 ablation harness |
| `docs/enrichment/ARCHITECTURE_AUDIT.md` | ✅ this file |
| `docs/enrichment/CHUNK_AUDIT.md` · `reports/chunk_audit.json` | ✅ generated by audit script |
| `docs/enrichment/ENRICHMENT_SCHEMA.md` · `ENRICHMENT_DESIGN.md` | ✅ this phase |
| `tests/test_enrichment_audit.py` · `tests/test_enrichment_deterministic.py` | ✅ **created** — 33 tests passing |
| `knowledge_graph.json` | Pending — regenerate from the enrichment store (Phase 8) |

## 8. Files that must NOT be modified

| File | Why |
| --- | --- |
| `backups/vector_store_fssai_legal_768_20260809_161941.json` | Offline restore point; treat as read-only |
| **Existing Qdrant point payloads' `chunk_text`** | Original legal text = highest authority; immutable |
| **Existing chunk IDs / point IDs** | Canonical identity; never regenerate |
| `app/rag/qdrant_client.py`, `app/rag/retrieval/*`, `app/rag/generation/*`, `app/rag/verification/*` | Working, tested retrieval/generation stack — enrichment plugs **around** it |
| `app/rag/chunker.py`, `app/rag/ingestion.py` | Chunk boundaries stay frozen |
| `app/knowledge_graph/engine.py` | Case-workflow graph is out of scope for corpus enrichment |
| Production `legal_chunk.text` / payload fields | Never silently replaced |

## 9. Risks

| Risk | Mitigation |
| --- | --- |
| Enrichment becomes "a second source of law" | Authority hierarchy: original text > verified metadata > LLM enrichment; every field carries provenance + evidence spans; validation blocks invented sections/penalties |
| Payload bloat slows Qdrant upserts / filter scans | Enrichment persisted in the sidecar enrichment store (SQLite) + **additive** payload keys only for retrieval-critical fields, merged only after Phase 9 evidence |
| 8 GB RAM limit | Stream from Qdrant via `scroll` in batches (50–100), release objects per batch, SQLite store, no full-corpus materialisation (audit one-shot load of the 151 MB backup is the only large read, and it is optional) |
| LLM hallucination on free-tier model | Deterministic extraction first; LLM only fills `unknown`-gated fields with explicit/inferred provenance; structural validation; hallucination guardrails from Phase 3 already in repo |
| Corrupt `corpus_eval_result.json` blocks eval | Evaluation harness sources its own authored dataset (RAGEvalDataset) + retrieval ground truth from chunk IDs |
| Qdrant Cloud downtime / cost | Offline backup supports the whole enrichment pipeline (`--source backup:`), which is read-only and cloud-free |
| Empty DB registry complicates joins | Enrichment store keyed by `chunk_id` (payload has all needed fields); no DB join required |

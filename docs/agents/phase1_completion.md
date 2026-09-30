# Phase 1 Implementation Summary — Autonomous RAG Research

**Date**: 2026-09-27  
**Status**: ✅ Complete  
**Spec**: `docs/RAG_AUTORESEARCH_RESEARCH.md` (lines 146–150)

## What Phase 1 Delivered

### 1. Corpus Discovery Engine (`app/rag/research/`)
- **`corpus_discovery.py`** — 5 dataclasses + `discover_corpus_gaps()` top-level function
  - Scans all 99 gold provisions via `evaluation.benchmark.load_gold_registry()`
  - Checks both (a) `chunk_id` presence in Qdrant payload index AND (b) section-body text existence via `FamilyMap` + `payload_to_keys` coverage index
  - Classifies gaps: `unindexed` / `orphaned` / `body_missing` / `fragmented` / `present`
  - Groups gaps by source document using `gold_sources_v1.0.json` (22 docs, 6 collections)
  - Emits bounded `IngestionRequest` objects (circuit-breaker: `RAG_RESEARCH_MAX_INGESTION_REQUESTS=50`)
  - Graceful degradation when payload index unavailable (falls back to `chunk_id`-only)
- **`__init__.py`** — package marker + public API exports

### 2. Automated Evidence Gap Analysis (extends beyond Step 0)
- **`GapAnalyzer`** class:
  - Maps missing provisions to ALL benchmark questions (not just Step 0's 124 residual QIDs)
  - `cross_reference_step0_targets()` — cross-references Step 0's 38 corpus-fill targets
  - Severity ranking: high (≥3 questions), medium (≥1 question or ≥3 missing per doc), low
- **Result against real data**: 71 indexed, 28 missing (unindexed), 11 high-severity, 69 affected questions
- **Step 0 cross-ref**: 8/38 targets covered by autonomous discovery; 30 need Step 0's retrieval-gap analysis

### 3. Config Flags (Pattern A, `app/shared/config.py`)
| Key | Attr | Type | Default | Opt-in | Purpose |
|-----|------|------|---------|--------|---------|
| `RAG_RESEARCH_ENABLED` | `research_enabled` | bool | `False` | ✓ | Master switch for autonomous loop |
| `RAG_RESEARCH_CORPUS_DISCOVERY` | `research_corpus_discovery` | bool | `True` | ✓ | Sub-switch for registry scan |
| `RAG_RESEARCH_USE_PAYLOAD_INDEX` | `research_use_payload_index` | bool | `True` | ✗ | Use Qdrant payload cache for body-check |
| `RAG_RESEARCH_MAX_INGESTION_REQUESTS` | `research_max_ingestion_requests` | int | `50` | — | Circuit breaker |

### 4. Tests (`tests/test_corpus_discovery.py`)
- 39 tests, all passing — no live Qdrant/Neo4j/network required
- Covers: `classify_provision_gap` (11), `group_provisions_by_document` (3), `discover_corpus_gaps` integration (8), `GapAnalyzer` (6), config flags (5), dataclass serialization (5)

### 5. Regressions
- `test_shared_config.py` (18) + `test_rag_agent_nodes.py` (76) — all green, no regressions
- `.env.example` consistency check passes

### Already Done (not redone)
- Git sync (main → 7746746)
- Universal multi-hop activation (`routing_economics.py` + `multi_hop_retrieve_node`)

## Next: Phase 2 — Data Acquisition (2–4 months)

### `app/rag/research/document_ingestion.py`
- **Web scraping** for legal repositories — reuse HTTP layer from outside `app/rag/` (no `requests`/`urllib`/`httpx` in `app/rag/` per Phase 1 constraint)
- **PDF/text parsing** — integrate existing `LegalDocumentOCR`, `DocumentCleaner`, `DocumentClassifier` from `app/rag/ingestion.py`
- **Chunking + vectorization** — wire into `app/rag/qdrant_indexer.py`

### `app/rag/research/provision_extraction.py`
- NLP for statutory section identification/extraction
- Cross-reference detection (reuse `app/rag/retrieval/reference_extractor.py`)
- Build on `kg/` package: `LegalSemanticEnricher`, `LegalKGIngestionEngine`

### Integration
- Connect Phase 2 pipeline to `run_ingest_corpus()` / `ingest_corpus_task` at `app/rag/tasks.py:384`

## Phase 3 — Autonomous Research (4–6 months)

### `app/rag/agent/research_orchestrator.py`
- Coordinates: `GapAnalyzer` → `DocumentIngestionPipeline` → `ProvisionExtractor` → Qdrant → re-run `discover_corpus_gaps()` → evaluation
- Triggers autonomous research loops based on `RAG_RESEARCH_ENABLED`
- Manages research state (reuse `RAGState` from `app/rag/agent/state.py`)

### Continuous loops + feedback
- Scheduled jobs (reuse `app/services/scheduled_jobs.py` pattern)
- Gap-closure verification via `evaluation/resolution.py` (`gold_in_corpus`, `chunks_cover_gold`)
- Feed metrics into `routing_economics.py` budget tier selection

## Key Infra Already in Place (Do Not Redeploy)
- `app/rag/ingestion.py` — `IngestionPipeline` with injectable adapters (handles local files only, no web scraping)
- `app/rag/qdrant_indexer.py` — Qdrant indexing
- `app/rag/retrieval/reference_extractor.py` — `extract_references()`, `Reference` dataclass
- `app/rag/retrieval/provision_versions.py` — `ProvisionVersion`, `VersionFamily`
- `evaluation/resolution.py` — `FamilyMap`, `payload_to_keys`, `matches_gold`, `gold_in_corpus`, `chunks_cover_gold`
- `evaluation/benchmark.py` — `load_gold_registry()`, `load_gold_sources()`, `load_questions()`, `GoldUnit`, `BenchmarkQuestion`
- `kg/hybrid.py` — `KGContextExpander`
- `app/rag/agent/routing_economics.py` — `route_strategy()`, `BUDGET_TIERS` (universal multi-hop already done)

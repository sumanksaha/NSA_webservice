# RAG Automated Data Research Capability: Research Report

## Executive Summary

Investigation of the Legal RAG system's current automated data research capabilities reveals a **partially implemented** system with **significant gaps** in autonomous research functionality. The system supports basic multi‑hop reasoning but lacks comprehensive data acquisition, corpus completion, and full‑scope query decomposition capabilities required for complete automated research.

## What Is Implemented

### 1. Multi‑Hop Reasoning Pipeline (`app/rag/agent/nodes/linear.py`)

- **`reason_node()` (line 559‑569)**: Analyzes retrieved chunks to determine if coverage is sufficient
    - Heuristic: `len(chunks) >= 3` and `len(chunk_text) >= 500` → "sufficient" coverage
    - Outputs `need_more_hops` boolean, hop count, reasoning note
- **`multi_hop_retrieve_node()` (line 647‑726)**: Performs targeted retrieval for complex queries
    - ~~Activates **only** for `query_type in ("cross_reference", "case_law")`~~ —
      **superseded 2026-09-27 (universal multihop, Parts A/B):** the type gate
      is removed; every query type gets pass 1 plus an evidence-gated
      follow-up loop (up to `MULTIHOP_MAX_FOLLOWUPS`) over mined
      section/rule/schedule references, with a definition-flavored builder
      and `multi_hop_*` stash keys consumed by `retrieve_node`.
    - Extracts cross‑reference candidates from chunk text using regex patterns
    - Performs targeted retrieval with expanded queries

### 2. Query Planning & Decomposition (`app/rag/planning/`)

- **`query_planner.py`**: Decomposes queries into EvidenceTasks with DAG structures
    - Uses `QueryPlanner().plan(query)` for structured planning
    - Supports complexity labeling and intent classification
- **`targeted_retry.py`**: Failure‑aware retrieval targeting
    - Focused retrieval for specific failure scenarios
    - Integrated with `evidence_sufficiency` checks

### 3. Knowledge Graph Expansion (`kg/hybrid.py`)

- **`KGContextExpander`**: Expands Qdrant chunk IDs through Neo4j
    - Provides provision relationships (`CROSS_REFERENCES`, `COMPLEMENTS`, etc.)
    - Requires `RAG_KG_EXPANSION=true` (enabled in `.env`)
    - Supports `RAG_KG_FUSION=true` (enabled in `.env`)

### 4. Routing Economics (`app/rag/agent/routing_economics.py`)

- **Strategic path selection**: DIRECT vs decomposition vs DAG based on query complexity
- **Budget tiers**: Constrains strategies to prevent infinite loops
- **Task slot management**: Optimizes resource usage for complex queries

### 5. Retrieval Stages (`app/rag/retrieval/stages.py`)

- **Evidence selector**: Filters and prioritizes relevant evidence
- **Legal identity**: Identifies temporal status, instrument/domain, authorities

### 6. Evaluation Integration (`evaluation/step3_gated_generation.py`)

- **Budget‑capped generation**: 150 generation limit
- **Evidence‑only paths**: `cited_span` required for `evidence_missing` gate
- **Contrastive options**: `model_wrong` path for verification

## What Is Missing

### 1. Comprehensive Automated Research

- **Web scraping/corpus ingestion**: No capability to discover, fetch, or ingest new legal documents
- **Autonomous corpus completion**: Cannot automatically identify missing statutory sections
- **Document analysis**: Cannot read and parse source documents to extract provisions

### 2. Universal Multi‑Hop Activation

- **Query type limitation**: Multi‑hop only activates for `cross_reference`/`case_law`
- **Missing for**: `prohibition`, `penalty`, `definition`, `procedure`, `general` queries
- **Root cause**: Routing economics and route logic restrict multi‑hop to specific query types

### 3. Evidence Gap Discovery

- **Step 0 labeling dependency**: AutoSearch requires human labeling before activation
- **Pre‑annotation limited**: 14 evidence‑missing candidates out of 124 residual questions
- **Limited scope**: Identifies gaps only after human labeling

### 4. Continuous Research Loop

- **No research orchestration**: No mechanism to repeatedly research questions over time
- **No knowledge base updates**: Cannot update corpus incrementally with new legal developments
- **No feedback loops**: Cannot learn from research outcomes to improve future queries

## Component Architecture Needed

### Core Components

| Component                       | Purpose                                        | Current Implementation                                    | Gap                              |
| ------------------------------- | ---------------------------------------------- | --------------------------------------------------------- | -------------------------------- |
| **Corpus Discovery Engine**     | Scans for missing statutory texts              | None                                                      | ❌ Cannot find missing evidence  |
| **Document Ingestion Pipeline** | Reads, parses, and indexes new legal documents | `app/rag/ingestion.py` (manual trigger)                   | ❌ No autonomous operation       |
| **Provision Extraction Engine** | Extracts legal provisions from documents       | None (used via `LegalSemanticEnricher` in KG)             | ❌ Limited to structured data    |
| **Research Orchestrator**       | Coordinates multi‑stage research workflow      | None (agent pipeline orchestration exists but incomplete) | ❌ Cannot trigger ingestion      |
| **Gap Analysis Module**         | Identifies systematic evidence gaps            | Limited to pre‑annotation                                 | ❌ Needs automated gap discovery |

### Required Implementation

1. **Research Orchestrator** (`app/rag/agent/research_orchestrator.py`)
    - Coordinates corpus discovery → ingestion → retrieval → evaluation
    - Triggers autonomous research loops based on evidence gaps
    - Manages research state and progress tracking

2. **Corpus Discovery** (`app/rag/research/corpus_discovery.py`)
    - Uses benchmark gold registry to identify missing provisions
    - Maps missing statutory sections to source document types
    - Generates ingestion requests for discovered gaps

3. **Document Ingestion** (`app/rag/research/document_ingestion.py`)
    - Web scraping for legal repositories
    - PDF/text parsing for statutory documents
    - Chunking and vectorization for Qdrant storage

4. **Provision Extraction** (`app/rag/research/provision_extraction.py`)
    - Natural language processing for legal texts
    - Statutory section identification and extraction
    - Cross‑reference detection and mapping

## Feasibility Analysis

### Technical Feasibility

**HIGH** - All required components use existing technologies:

- **Document parsing**: Python libraries (PyPDF2, pdfminer, tabula)
- **Web scraping**: Existing HTTP clients in the codebase
- **NLP**: Available via existing `LegalSemanticEnricher` and KG enrichment
- **Orchestration**: Uses existing agent framework (LangGraph nodes/graph)

### Resource Constraints

**MODERATE** - Requires:

- **Storage**: Incremental corpus growth (managed via Qdrant)
- **Compute**: Additional processing for document ingestion
- **Time**: Research loops need to complete within evaluation budgets

### Integration Complexity

**MODERATE** - Requires changes to:

- **Route logic** (`app/rag/agent/nodes/linear.py`): Expand multi‑hop activation
- **Configuration** (`.env`): New research‑related flags
- **Evaluation framework**: New metrics for research completeness

## Implementation Roadmap

### Phase 1: Foundation (0‑2 months)

1. Implement **corpus discovery module** using existing benchmark registry
2. Add **universal multi‑hop activation** (remove query‑type restrictions)
3. Extend **evidence gap analysis** beyond pre‑annotation

### Phase 2: Data Acquisition (2‑4 months)

1. Implement **document ingestion pipeline**
2. Add **provision extraction engine**
3. Integrate with **existing Qdrant ingestion** (`app/rag/qdrant_indexer.py`)

### Phase 3: Autonomous Research (4‑6 months)

1. Build **research orchestrator**
2. Implement **continuous research loops**
3. Add **feedback mechanisms** for improving queries

## Business Impact

### Immediate Benefits

- **Eliminate evidence‑missing questions**: From 14% to <1% of residual set
- **Improve binary correctness**: Target +5% (per `step3_gated_generation.py` analysis)
- **Reduce manual intervention**: Automated gap discovery and filling

### Long‑Term Benefits

- **Maintain current corpus**: Continuous legal updates with minimal manual effort
- **Improve recall**: Enhanced evidence availability across all query types
- **Scale research**: Support for growing legal document volumes

## Risk Assessment

### High Risks

- **Quality assurance**: Automated document parsing may introduce errors
- **Legal compliance**: Data ingestion must respect copyright and licensing
- **System stability**: Research loops could create infinite recursion

### Mitigation Strategies

1. **Quality gates**: Validate extracted provisions against known gold standards
2. **Legal review**: Implement manual verification for high‑risk document types
3. **Circuit breakers**: Use existing evaluation framework’s budget and retry caps

## Recommendation

### Short‑Term (0‑3 months)

Implement **universal multi‑hop activation** and **enhanced evidence gap analysis** using existing components. This delivers immediate ROI by extending multi‑hop reasoning to all query types.

### Long‑Term (3‑12 months)

Build full **autonomous research capability** with the four core components. This transforms the RAG system from a passive retrieval service to an active research assistant.

### Success Metrics

- **Binary correctness improvement**: ≥5% on evidence‑missing subset
- **Coverage increase**: Multi‑hop activation from 2/6 query types to all
- **Research completeness**: Reduction in evidence‑missing questions from 14/124 to <5/124

## Primary Sources Cited

1. `app/rag/agent/nodes/linear.py` — Agent node implementations
2. `app/rag/agent/graph.py` — LangGraph orchestration
3. `app/rag/planning/query_planner.py` — Query decomposition
4. `app/rag/planning/targeted_retry.py` — Failure‑aware retrieval
5. `kg/hybrid.py` — Knowledge graph expansion
6. `app/rag/agent/routing_economics.py` — Strategic path selection
7. `app/rag/retrieval/stages.py` — Evidence selection pipeline
8. `app/rag/retrieval/subquery_decomposer.py` — Query decomposition
9. `docs/RAG_IMPLEMENTATION.md` — System implementation plan
10. `docs/RAG_UPGRADE_RESEARCH.md` — Upgrade requirements and gaps
11. `docs/RAG_EFFECTIVENESS_EVALUATION.md` — Evaluation framework analysis
12. `app/shared/config.py` — System configuration
13. `evaluation/step3_gated_generation.py` — Budget‑capped generation

**File saved**: `docs/RAG_AUTORESEARCH_RESEARCH.md`

---

_Research completed by background agent investigating automated data research capability in Legal RAG system. All findings based on primary source artifacts from the repository._

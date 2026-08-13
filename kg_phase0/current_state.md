# CURRENT_STATE.md — Legal Knowledge Graph Audit

> **Phase 0 — Existing-system audit**  
> Audited: 2026-08-10  
> Method: static analysis of all source files + live probes of Neo4j Aura (v5.27) + local DB inspection

---

## 1. Graph Database

| Component | Status | Details |
|---|---|---|
| **Database** | Neo4j Aura (5.27-aura) | Live, Bolt protocol over TLS |
| **Connection** | ✅ Configured | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` set in `.env` |
| **APOC** | ✅ Available | Used by `push_to_neo4j()` for dynamic labels |
| **Data** | 🔴 EMPTY | 0 nodes, 0 relationships, 1 relationship type (`RELATIONSHIP`) |

---

## 2. Existing Neo4j Schema

### 2.1 Node Labels (9 — all created, all empty)

| Label | Purpose | Constraint |
|---|---|---|
| `Case` | FSO case file | `local_id` UNIQUE |
| `FBO` | Food Business Operator | `local_id` UNIQUE |
| `Inspector` | Food Safety Officer | `local_id` UNIQUE |
| `Sample` | Sample for analysis | `local_id` UNIQUE |
| `Lab` | Testing lab | `local_id` UNIQUE |
| `Section` | Legal section (from case data) | `local_id` UNIQUE |
| `Evidence` | Evidence record | `local_id` UNIQUE |
| `Ancillary` | Bills, annexures | `local_id` UNIQUE |
| `Entity` | Fallback label (no APOC) | `local_id` UNIQUE |

**None of these are legal-instrument labels.** The existing graph stores case-file entities (Case→FBO→Inspector→Sample→Section→Evidence), NOT legal provisions (Act→Section→Subsection→Rule→Regulation).

### 2.2 Relationship Types

| Type | Source |
|---|---|
| `RELATIONSHIP` | All edges (single catch-all type with `type` property) |

Edge types stored as a property `type` on `RELATIONSHIP`: `INSPECTED_BY`, `SAMPLED_FROM`, `TESTED_AT`, `VIOLATED_SECTION`, `SUPPORTED_BY`, `REFERENCES`.

### 2.3 Indexes

| Name | Label/Type | Properties |
|---|---|---|
| `entity_type_index` | `Entity` | `entity_type` |
| `entity_name_index` | `Entity` | `name` |
| `rel_type_index` | `RELATIONSHIP` | `type` |
| Plus constraint-backed RANGE indexes for each label's `local_id` |

### 2.4 Constraints

9 uniqueness constraints — one per label on `local_id`. No existence constraints, no key constraints, no relationship constraints.

---

## 3. Existing Code Paths

### 3.1 `app/services/neo4j_graph.py` — Legacy case-file sync (NOT multi-domain KG)

Functions:
- `neo4j_configured()` — checks `NEO4J_URI` + `NEO4J_USERNAME` + `NEO4J_PASSWORD`
- `_get_driver()` — creates `GraphDatabase.driver(uri, auth=basic_auth(...))`
- `_entity_to_node(Entity)` — converts local `Entity` model row → Neo4j node
- `_relationship_to_edge(Relationship)` — converts local `Relationship` row → edge
- `build_cypher_payload()` — reads ALL `Entity`/`Relationship` rows from SQLite/PostgreSQL DB, returns JSON
- `setup_constraints_and_indexes()` — creates the 9 constraints + 3 indexes above
- `push_to_neo4j(case_type, case_id, use_apoc)` — **DELETES ALL NODES** (`MATCH (n) DETACH DELETE n`), then re-pushes via APOC `apoc.create.node`
- `query_neo4j(cypher, params)` — arbitrary Cypher execution

**Critical limitation:** This pushes from the local `Entity`/`Relationship` tables (which are populated by `KnowledgeGraphEngine.build_graph_for_case()` from case-file data), NOT from legal corpus data. It is a case-file entity graph, not a legal-provision graph.

### 3.2 `neo4j_aura_loader.py` — Development bootstrap script (root, NOT used in production)

- Hardcoded sample data: Person, Organization, Act, Case, Section nodes
- Relationships: VIOLATES, FILED_UNDER, PRESIDED_BY, ENACTED, REPEALED, REFERENCES, KNOWN_AS
- Run manually via `python neo4j_aura_loader.py`
- **Wiped by `push_to_neo4j()`** which does `MATCH (n) DETACH DELETE n` on every sync
- Never called from any Flask route or Celery task in production
- The test `test_push_sample_data` mocks this data and pushes via `push_to_neo4j()`

### 3.3 `app/knowledge_graph/` — Knowledge Graph extraction (Phase 14, case-file scope)

**`engine.py`** — `KnowledgeGraphEngine.build_graph_for_case(case_id, case_type)`:
- Extracts entities from `CaseFile`/`Adjudication` records: Case, FBO, Inspector, Sample, Lab, Section, Evidence, Annexure, Bill
- Edges: SUPPORTED_BY, INSPECTED_BY, SAMPLED_FROM, TESTED_AT, VIOLATED_SECTION, REFERENCES
- Persists to `Entity`/`Relationship` tables (case_file only)
- Returns Cytoscape.js format JSON

**`routes.py`** — Three routes:
- `GET /knowledge-graph/case/<id>` — renders Cytoscape.js visualizer
- `GET /api/case/<id>` — returns graph JSON, triggers persistence
- `POST /api/sync-neo4j[/<case_id>]` — pushes to Neo4j via QStash/Celery

**`tasks.py`** — `_run_sync_kg_to_neo4j()` wraps `push_to_neo4j()` as Celery task

### 3.4 `app/rag/collections.py` — Multi-domain collection mapping (ALREADY EXISTS)

```python
DOMAIN_COLLECTIONS = {
    "fssai":       "fssai_legal_768",
    "env":         "env_legal_768",
    "commercial":  "commercial_legal_768",
    "animal":      "animal_legal_768",
    "wb_state":    "wb_state_legal_768",
}
```

Domain aliases: `food`→`fssai`, `environment`→`env`, `state`→`wb_state`, `municipal`→`wb_state`

### 3.5 `app/rag/legal_sections.py` — Act section registries (ALREADY EXISTS)

```python
ACT_SECTION_RANGES = {
    "Food Safety and Standards Act, 2006": (1, 104),
    "Environment (Protection) Act, 1986": (1, 26),
    "Water (Prevention and Control of Pollution) Act, 1974": (1, 64),
    "Air (Prevention and Control of Pollution) Act, 1981": (1, 54),
    "Companies Act, 2013": (1, 470),
    "Indian Contract Act, 1872": (1, 238),
    "Sale of Goods Act, 1930": (1, 66),
    "Indian Partnership Act, 1932": (1, 74),
    "Limited Liability Partnership Act, 2008": (1, 81),
    "Limitation Act, 1963": (1, 32),
    "Specific Relief Act, 1963": (1, 44),
    "Consumer Protection Act, 2019": (1, 107),
}
```

This is advisory only — for cross-reference validation and query classification.

---

## 4. Local Database State

### 4.1 LegalDocument table — 29 documents (ALL FSSAI)

| Field | Distribution |
|---|---|
| `document_type` | regulation: 17, notification: 8, act: 4 |
| `authority` | FSAN (14), M-oH&FW (9), MoLJ (3), Legislative Dept (1), fssai/FSSAI (2) |
| `jurisdiction` | ALL NULL |
| `legal_domain` | **DOES NOT EXIST as a column** |

### 4.2 LegalChunk table — 12,819 chunks

| Field | Values |
|---|---|
| `section_number` | Present on ~97% of chunks |
| `document_type` | act/regulation/notification |
| `authority` | From parent document |
| `jurisdiction` | ALL empty string |
| `act_name` | ALL empty string |
| `legal_domain` | **DOES NOT EXIST in the payload** |
| `metadata_json` | Cached Qdrant payload (14+ fields) — no `legal_domain` |

### 4.3 Entity table — 0 rows

### 4.4 Relationship table — 0 rows

---

## 5. Qdrant Vector Store

| Item | Status |
|---|---|
| **Reachable from dev** | ❌ No (`RAG_QDRANT_URL` empty in `.env.example`) |
| **Reachable from prod** | ✅ Yes (configured in `render.yaml`) |
| **Collection** | `fssai_legal_768` |
| **Vector size** | 768 (dense + BM25 sparse hybrid) |
| **Indexed points** | Unknown from dev (AGENTS.md claims ~1,097, but local DB has 12,819 chunk records — collection may be stale or partial) |
| **Payload fields** | Same §5.1 schema as `LegalChunk` — no `legal_domain` field |

---

## 6. Existing Ingestion Scripts

| Script | Purpose | Multi-domain? |
|---|---|---|
| `app/rag/ingestion.py` | File → clean → dedup → chunk → embed → upsert | Uses `collection` param (single collection per run) |
| `app/rag/qdrant_indexer.py` | Embed + upsert chunks | Single collection |
| `neo4j_aura_loader.py` | Hardcoded sample data loader | No (dev only) |

No corpus manifest, no per-domain ingestion orchestration.

---

## 7. Existing Query/Retrieval Code

| Module | Purpose |
|---|---|
| `app/rag/retrieval/dense_retriever.py` | Qdrant dense vector search |
| `app/rag/retrieval/sparse_retriever.py` | BM25/rapidfuzz fuzzy search |
| `app/rag/retrieval/hybrid_retriever.py` | RRF fusion (k=60) |
| `app/rag/retrieval/reranker.py` | Cross-encoder rerank |
| `app/rag/retrieval/query_classifier.py` | Classify query type + parse sections/authorities/jurisdictions |
| `app/rag/retrieval/result.py` | `RetrievedChunk` dataclass |

**No graph-based retrieval exists.** All retrieval is vector/dense or sparse keyword search against Qdrant. There is no Cypher-based retrieval layer.

---

## 8. Missing Capabilities (vs. Task Requirements)

| Requirement | Status |
|---|---|
| Legal-instrument node types (Act, Rule, Regulation, etc.) | ❌ None exist |
| Legal-provision node types (Section, Subsection, Clause) | ❌ Only `Section` as case-file entity |
| Legal-concept nodes (Obligation, Prohibition, Offence, Penalty) | ❌ None exist |
| Cross-domain relationships (RELATED_TO, INTERACTS_WITH, etc.) | ❌ None exist |
| Authority graph (ENFORCED_BY, GRANTS_POWER_TO) | ❌ None exist |
| Temporal model (effective_from/to, status, version) | ⚠️ Partial — in Qdrant payload, not in Neo4j |
| Source provenance chain (Provision → Document → Chunk) | ⚠️ Partial — Qdrant payload has chunk fields, Neo4j has nothing |
| Entity resolution / canonical names | ⚠️ Partial — `legal_sections.py` has act-name normalisation |
| Legal citation normalisation | ⚠️ Partial — `citation_adapter.py` for Qdrant, not Neo4j |
| Graph retrieval layer | ❌ None exists |
| LLM retrieval contract | ❌ None exists |
| Validation scripts | ❌ None exist for KG quality |
| Multi-domain corpus | ❌ Only FSSAI (29 docs, 12,819 chunks) |

---

## 9. Risks and Constraints

### 9.1 `push_to_neo4j()` destructively deletes all nodes

Every sync call executes `MATCH (n) DETACH DELETE n` before re-pushing. This means any manual data in the graph (e.g., from `neo4j_aura_loader.py`) is wiped. The new KG must use a different sync strategy.

### 9.2 Neo4j labels are case-file entity labels, not legal-instrument labels

The existing constraints (`Case`, `FBO`, `Section`, etc.) are incompatible with the legal-instrument hierarchy. The new schema will need different labels (`Act`, `Rule`, `Regulation`, `LegalProvision`, etc.) and corresponding constraints.

### 9.3 No `legal_domain` field anywhere

The Qdrant payload, the `LegalDocument` model, the `LegalChunk` model, and the `Chunk` dataclass all lack a `legal_domain` field. This must be added to support domain segregation.

### 9.4 Existing tests depend on the current Neo4j schema

`test_neo4j_kg_sync.py` tests `push_to_neo4j()` with mocked `Case`/`Person` nodes. New schema must not break these tests OR they must be updated.

### 9.5 8 GB RAM constraint

The `neo4j_aura_loader.py` and `push_to_neo4j()` load all Entity/Relationship rows at once. Ingestion must be batched/streaming for the multi-domain corpus.

### 9.6 Neo4j Aura has a 1M node limit on free tier

This is adequate for a legal KG of ~24 documents × ~500 sections = ~30,000 provisions, but worth noting.

---

## 10. Reusable Infrastructure

| Component | Reusable? | How |
|---|---|---|
| `Neo4jGraphService` connection logic (`_get_driver`, `query_neo4j`) | ✅ Yes | Can be reused for new KG queries |
| `KnowledgeGraphEngine` extraction patterns | ⚠️ Partially | Case-file extraction logic is domain-specific; legal-instrument extraction is new |
| `app/rag/ingestion.py` pipeline | ✅ Yes | `collection` param already supports multi-domain |
| `app/rag/collections.py` domain→collection map | ✅ Yes | Already defines 5 domains + aliases |
| `app/rag/legal_sections.py` act/section registry | ✅ Yes | 12 acts registered with section ranges |
| `app/models/document.py` Entity/Relationship models | ❌ No | Too case-file specific (entity_type values, source_table FK) |
| `app/models/rag.py` LegalDocument/LegalChunk | ✅ Partially | Has document_type, authority, jurisdiction — but no legal_domain |
| `app/rag/qdrant_client.py` QdrantStore | ✅ Yes | Already supports multiple collections via `collection_name` |
| `app/rag/chunker.py` Chunk dataclass | ⚠️ Partially | Has act_name, jurisdiction but no legal_domain |

---

## 11. Summary

The existing Neo4j integration is a **case-file entity graph** (Case→FBO→Inspector→Sample→Section→Evidence), not a **legal-instrument knowledge graph**. It is connected to Neo4j Aura but completely empty. The Qdrant corpus contains 12,819 FSSAI chunks with §5.1 metadata but no `legal_domain` field. No legal-provision, legal-concept, authority, or cross-domain infrastructure exists in Neo4j.

The existing codebase provides reusable foundations in `collections.py` (domain mapping), `legal_sections.py` (act registry), and `qdrant_client.py` (multi-collection support), but a new legal KG schema must be built from scratch.

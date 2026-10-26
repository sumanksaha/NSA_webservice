# Knowledge‑Graph Implementation Plan for Legal RAG

## 1. Goal
Upgrade the existing knowledge‑graph engine so that it directly supports the Legal Retrieval‑Augmented Generation (RAG) pipeline. The plan focuses on schema unification, richer legal relationships, persistence of all graph types, hybrid retrieval, incremental updates, robust error handling, testing, documentation, and performance.

---

## 2. Unified Schema
| Existing Engine Constant | Canonical JSON Schema | Description |
|---|---|---|
| `CASE` | `document` | Legal case / adjudication document |
| `FBO` | `authority` | Food Business Operator (entity) |
| `INSPECTOR` | `authority` | Food Safety Officer |
| `SAMPLE` | `evidence` (type=`sample`) |
| `LAB` | `authority` (lab) |
| `SECTION` | `section` | Statutory section/sub‑section |
| `EVIDENCE` | `evidence` | Photo / document evidence |
| `ANCILLARY` | `reference` | Bills, annexure docs |

Create **`app/knowledge_graph/schema_adapter.py`** that maps `KGNode`/`KGEdge` objects to the canonical schema defined in `knowledge_graph.json`.

---

## 3. New Edge Types (Legal Relationships)
```python
# in app/knowledge_graph/constants.py (new)
AMENDS = "AMENDS"
OVERROLS = "OVERROLS"
SUPPORTS = "SUPPORTS"
CITES = "CITES"
APPLIES_TO_JURISDICTION = "APPLIES_TO_JURISDICTION"
```
Populate these edges while iterating over a document's `references` field (already present in the JSON source). Example logic:
```python
for ref in section.references:
    target_id = f"section:{ref.id}"  # canonical id format
    edges.append(KGEdge(source=section_node.id, target=target_id, type=CITES))
```
---

## 4. Metadata Enrichment
Add the following fields to node `metadata` where applicable:
- `full_text`
- `effective_date`, `expiry_date`
- `jurisdiction`
- `vector_id` (link to embedding in the vector store)
- `source_table` / `source_id` (already present)

Update `KnowledgeGraphEngine.build_graph_for_case` to pull these from the ORM models (e.g., `Section.text`, `Section.effective_date`).
---

## 5. Persistence of All Graph Types
1. **Create new DB models** `AdjudicationEntity` / `AdjudicationRelationship` mirroring `Entity` / `Relationship`.
2. Extend `KnowledgeGraphEngine._persist_graph` to accept a `graph_origin` argument (`case_file` or `adjudication`).
3. When `case_type == "adjudication"`, store to the new tables.
---

## 6. Hybrid Retrieval API
1. **Endpoint**: `POST /api/graph/search`
   - Payload: `{ "query": "string", "max_hops": 2, "k": 5 }`
2. Steps:
   - Embed the query using the same model (`sentence‑transformers/all‑mpnet‑base‑v2`).
   - Perform nearest‑neighbor search against stored node embeddings (via existing vector store).
   - Retrieve the matching node IDs.
   - Traverse the persisted graph up to `max_hops` using a BFS on the `Entity`/`Relationship` tables.
   - Return a sub‑graph JSON (nodes + edges) plus the original similarity scores.
3. Add a Flask blueprint `app/knowledge_graph/api.py` and register it in `create_app`.
---

## 7. Incremental Graph Updates
Implement `KnowledgeGraphEngine.upsert_graph(case_id, case_type)`:
- Detect changes via `record.updated_at` vs stored `graph_last_modified` column.
- Use SQLAlchemy `bulk_save_objects` for batch upserts of nodes/edges.
- Remove stale edges where the source/target no longer exists.
---

## 8. Robust Error Handling
- Define `class GraphBuildError(RuntimeError): pass` in `app/knowledge_graph/errors.py`.
- Raise this error for missing cases or DB failures.
- API layer catches the exception and returns `400` with a structured error payload.
---

## 9. Testing Enhancements
| New Test | Purpose |
|---|---|
| `test_schema_alignment` | Verify that every `KGNode`/`KGEdge` produced maps to a valid entry in `knowledge_graph.json` schema. |
| `test_legal_edges` | Ensure `CITES`, `AMENDS`, `OVERROLS` edges are created for sections with references. |
| `test_adjudication_persistence` | Confirm adjudication graphs are stored and retrievable. |
| `test_hybrid_search` | End‑to‑end test of `/api/graph/search` (mock vector store). |
| `test_incremental_upsert` | Modify a section and ensure graph updates without full rebuild. |
Add these to `tests/test_knowledge_graph_extended.py`.
---

## 10. Documentation & Indexing
1. Create `docs/knowledge_graph/README.md` covering:
   - Unified schema table
   - New edge definitions
   - API usage examples
   - How to run the extended test suite
2. Update `docs/INDEX.md` with an entry linking to the new README.
3. Add a Mermaid diagram generation script (`scripts/gen_kg_diagram.py`) that reads the schema and outputs `docs/knowledge_graph/diagram.mmd`.
---

## 11. Performance Optimisations
- **Redis Cache**: Cache node look‑ups for frequently accessed statutes (key: `kg:node:{id}`).
- **Bulk DB Ops**: Use `session.bulk_save_objects([...])` for node/edge inserts in `upsert_graph`.
- **Vector Store Sync**: Store embeddings lazily; only when a node is first created.
---

## 12. Milestones (Tentative Timeline)
| Milestone | Owner | Estimated Duration |
|---|---|---|
| Schema adapter & constant definitions | Backend | 2 days |
| Metadata enrichment & ORM changes | Backend | 3 days |
| Persistence for adjudication graphs | DB/Backend | 2 days |
| New edge extraction logic | Backend | 2 days |
| Hybrid search endpoint | API | 3 days |
| Incremental upsert implementation | Backend | 2 days |
| Error handling & logging | Backend | 1 day |
| Extended test suite | QA | 3 days |
| Documentation & diagram script | Docs | 2 days |
| Performance tuning & cache | DevOps | 2 days |

Total **≈ 22 working days** (≈ 4 weeks). Adjust as needed based on sprint capacity.
---

## 13. Checklist for Completion
- [ ] Unified schema adapter implemented
- [ ] New legal edge constants added
- [ ] Node metadata enriched & stored
- [ ] Adjudication persistence tables added
- [ ] `upsert_graph` method works
- [ ] `/api/graph/search` endpoint operational
- [ ] All new tests pass (`pytest -q`)
- [ ] Documentation updated and indexed
- [ ] Performance benchmarks recorded

---

*Prepared by Antigravity – the agentic AI coding assistant.*
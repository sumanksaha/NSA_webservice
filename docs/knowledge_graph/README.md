# Knowledge Graph — Legal RAG support

The knowledge-graph engine (`app/knowledge_graph/`) extracts entity/relationship
graphs from case-file and adjudication records and persists them so the Legal
RAG pipeline can query a unified graph of cases, authorities, evidence, and
statutory sections.

## Unified schema

KG nodes/edges (`KGNode`/`KGEdge`, produced by `KnowledgeGraphEngine`) map to
the canonical schema in `app/knowledge_graph/knowledge_graph.json`:

| Engine node type | Canonical entity type | Source |
|-----------------|----------------------|--------|
| `case` | `document` | CaseFile / Adjudication |
| `fbo` | `authority` | Manufacturer / FBO |
| `inspector` | `authority` | Food Safety Officer |
| `sample` | `evidence` | Sample |
| `lab` | `authority` | Laboratory |
| `section` | `section` | Statutory section (enriched from `kg_sections`) |
| `evidence` | `evidence` | Photo / document evidence |
| `ancillary` | `reference` | Bills / annexures |

Use `app.knowledge_graph.schema_adapter.to_canonical()` / `to_canonical_edge()`
and `validate_graph(nodes, edges)` to check a graph against the schema.

## Legal edges

In addition to the case-graph edges (`INSPECTED_BY`, `SAMPLED_FROM`, `TESTED_AT`,
`VIOLATED_SECTION`, `SUPPORTED_BY`, `REFERENCES`), the engine now extracts legal
relationships between sections while iterating a document's section references:

| Edge | Meaning |
|------|---------|
| `CITES` | Section references another section (target `section:<case_id>:<ref id>`) |
| `AMENDS` | Document/section amends the referenced section |
| `OVERRULES` | Document/section overrules the referenced section |
| `APPLIES_TO_JURISDICTION` | Section applies to the given jurisdiction |

References come from `record.section_references` (a list of
`{"id": "<section>", "type": "cites|amends|overrules"}` or plain section
numbers); absent that, `CITES` edges are inferred from section numbers
mentioned in the record's problem text.

## Metadata enrichment

Section nodes are enriched from the ORM `Section` model (`kg_sections`):
`full_text`, `effective_date`, `expiry_date`, `jurisdiction`, `act`, plus
`source_table`/`source_id`. Every node also carries `graph_last_modified`.
Nodes expose a `vector_id` linkage (``vec:<node_id>``) populated lazily via
`KnowledgeGraphEngine.attach_vector_id(node_id, text)` (sentence-transformers
`all-mpnet-base-v2`; no-op when the model is unavailable).

## Persistence

Graphs are persisted to SQL tables `entity`/`relationship` (case_file) and
`adjudication_entity`/`adjudication_relationship` (adjudication), with
`graph_last_modified` stamped on every row.

## Hybrid search API

`POST /api/graph/search` with payload `{"query": "...", "max_hops": 2, "k": 5}`:

1. Embeds the query (sentence-transformers/all-mpnet-base-v2, lazily) and runs
   nearest-neighbor search over persisted node texts → seed nodes + scores.
2. BFS traverses the persisted graph up to `max_hops` from the seeds.
3. Returns the sub-graph JSON (nodes, edges) plus per-node similarity scores.

The endpoint catches `GraphBuildError` and returns a `400` with a structured
payload (`{"error": ..., "code": "graph_build_error"}`); invalid requests
return `400` with `code: "invalid_request"`.

## Incremental upsert

`KnowledgeGraphEngine.upsert_graph(case_id, case_type)` rebuilds the graph only
when `record.updated_at` is newer than the stored `graph_last_modified`,
otherwise returns `{"status": "unchanged", ...}`. Writes use
`session.bulk_save_objects`; stale edges (source/target no longer present) are
removed on rebuild. Raises `GraphBuildError` for unknown cases.

## Testing

```bash
pytest -q tests/test_knowledge_graph.py tests/test_knowledge_graph_extended.py
```

The extended suite covers schema alignment, legal edges, adjudication
persistence, hybrid search (mocked vector store), and incremental upsert.

## Neo4j sync

Neo4j Aura sync (`POST /knowledge-graph/api/sync-neo4j`, QStash-backed) is
unchanged and remains dormant unless `NEO4J_URI`/`NEO4J_USERNAME`/
`NEO4J_PASSWORD` are set; writes additionally require `NEO4J_ALLOW_WRITE=1`.
SQL persistence remains the source of truth when Neo4j is unconfigured.

## Performance

- Node look-ups are cached in Redis under `kg:node:{id}` (best effort; degrades
  gracefully when Redis is unavailable).
- Upserts use bulk inserts and remove stale edges.
- Embeddings/vector links are created lazily, only on demand.
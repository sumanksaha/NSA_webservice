# Enrichment Project — Remaining Work & Followups

> **Purpose:** Single reference for the remaining phases of the FSSAI chunk
> enrichment project. Status captured on **2026-08-10** after Phase 14
> (evaluation) and Phase 15 (ablation) completed. Companion docs:
> `ARCHITECTURE_AUDIT.md` · `CHUNK_AUDIT.md` · `ENRICHMENT_SCHEMA.md` ·
> `ENRICHMENT_DESIGN.md` (design details + full results).

---

## 0. Project status snapshot

| Phase | Status | Evidence |
| ----- | ------ | -------- |
| 1 Repository audit | ✅ | `ARCHITECTURE_AUDIT.md` |
| 2 Chunk audit | ✅ | `CHUNK_AUDIT.md`, `reports/chunk_audit.json` |
| 3 Schema design | ✅ | `ENRICHMENT_SCHEMA.md` |
| 3b Deterministic extraction | ✅ 12,819/12,819, 0 failures | section coverage 24.9% → **99.4%**; keywords on 6,890 chunks |
| 5 Checkpoint system | ✅ resume verified | re-run reprocessed only 58 failed, then 0 |
| 7 Validation | ✅ all records VALIDATED | `tests/test_enrichment_validation.py` |
| 8 Cross-ref resolution (first pass) | ✅ 2,734 REFERS_TO edges | 2,344 sources → 528 targets |
| 11 Baseline-vs-enriched eval | ✅ 53 Qs / 7 archetypes, 0 skipped | `reports/evaluation_*.json` |
| 12 Ablation | ✅ `section+crossrefs` kept | `reports/ablation_results.json` |
| 4 LLM batch enrichment | ⏸ deferred (user decision: deterministic-first) | `RAG_USE_STUB_LLM=true` |
| 8b Graph build (Neo4j corpus graph) | ⏳ **next** — Neo4j installed + connected 2026-08-10 | Phase 8 below |
| 9 Qdrant Strategy A vs B | ⏳ after LLM summaries exist | new collection only |
| 13 Production integration | ⏳ | Phase 13 below |
| Answer-level eval (grounding/hallucination) | ⏳ after Phase 4 | via `EvalRunner` |

**Key eval/ablation results (see `ENRICHMENT_DESIGN.md` for full tables):**
- Recommended retrieval config: **section boost + cross-ref expansion** — positive
  on all 5 metrics vs dense baseline (R@5 +0.028, R@10 +0.019, P@5 +0.007,
  MRR +0.004, nDCG@10 +0.008). 53-question set.
- **Rejected:** keyword credit (−0.041 MRR; multiword-only probe worse, −0.092),
  "full" config (−0.043 MRR), summaries (inert — field empty until LLM pass).
- The earlier 19-question headline (MRR +0.073) did **not** generalize to 53 —
  ablation caught it. Do not re-introduce the keyword credit.

---

## 1. Suggested followups (clickable next steps)

### ▶ Phase 8 — Build the knowledge graph into Neo4j (recommended next)

**Status update (2026-08-10): Neo4j is now installed and connected.** The
`neo4j` Python driver is installed, `app/services/neo4j_graph.py`
(`Neo4jGraphService`: APOC dynamic labels, 9 uniqueness constraints, 3
property indexes, `push_to_neo4j` / `query_neo4j`) + `neo4j_aura_loader.py`
already exist, and connectivity to the configured instance was just verified
(`NEO4J_URI` live, `verify_connectivity()` OK). The "defer Neo4j" decision in
`ENRICHMENT_DESIGN.md` is superseded — the corpus graph now targets Neo4j.

Plan (evidence-first, reuse existing service):
- Build a corpus graph loader (`scripts/load_kg_to_neo4j.py`) that reads the
  **enrichment store** (SQLite) + chunk payloads — do **not** consume
  `corpus_eval_result.json` (corrupt — audited). `scripts/build_kg.py` stays
  untouched.
- Node labels with stable IDs: `ACT:FSSAI_2006`, `SECTION:FSSAI_2006:32`,
  `PROVISION:<chunk_id>`, `AUTHORITY:FSSAI`, … (controlled ontology from
  `ENRICHMENT_SCHEMA.md` §3).
- Edges from evidence only: `REFERS_TO` (resolved cross-refs, 2,734 existing),
  `PART_OF` / `UNDER` / `IMPLEMENTS` (document structure) — every relationship
  carries `source_chunk_id · target_chunk_id · relationship_type ·
  confidence · evidence · provenance`.
- Add uniqueness constraints + indexes (reuse
  `Neo4jGraphService.setup_constraints_and_indexes`); MERGE, never duplicate
  nodes.
- Keep the JSON knowledge graph as a companion/fallback export (diff-able,
  8 GB-friendly) — the ontology maps 1:1.
- **Evaluate** whether graph traversal improves retrieval on the 53-Q dataset
  before wiring it into the RAG retrieval path (task: evidence-first — do not
  keep what does not measurably help).

### ▶ Phase 4 — LLM semantic enrichment (deferred, requires API key)
- Batch **50 default / 100 max** (`ENRICHMENT_BATCH_SIZE`), streamed — never
  >1 batch in memory. Reuses `GroundedLLMClient` (httpx, OpenAI-compatible),
  `RAG_LLM_MODEL`, `OPENROUTER_API_KEY`.
- LLM only for fields determinism can't produce: `legal_concepts`,
  `obligations`, `prohibitions`, `permissions`, `powers`, `duties`,
  `conditions`, `exceptions`, `offences`, `penalties`, `procedures`,
  `applicability`, `retrieval_summary`, `question_types`.
- Prompt `enrich-v1`: JSON-only output, per-field `kind ∈ {explicit,
  inferred, unknown}`, "unknown over guessing". No LLM for section numbers /
  dates / authorities / penalties without evidence spans.
- 1× transport retry per batch; FAILED batches stored with error, never
  retained in memory; restart retries only non-VALIDATED chunks.
- **After the pass, re-run the ablation** — populated `retrieval_summary` and
  more specific keywords may change the keep/reject recommendations.

### ▶ Answer-level evaluation via EvalRunner
- Run grounding, citation correctness, hallucination rate on the 53-Q dataset
  (already seeded in `rag_eval_dataset` with resolved gold chunk IDs +
  `expected_section`) once generation is exercised (LLM pass on).
- Reuse `app/rag/evaluation/` (`FaithfulnessMetric`, `AnswerRelevanceMetric`,
  `CitationRecallMetric`, `GroundednessMetric`, ...) + `RAGResponse` schema.

---

## 2. Remaining implementation-order work (from the task)

### 2.0 Neo4j — what already exists (do not re-build)

| Asset | Location | State |
| --- | --- | --- |
| Python driver `neo4j` | installed (6.2.0) | ✅ |
| `Neo4jGraphService` (APOC labels, constraints, indexes, push/query) | `app/services/neo4j_graph.py` | ✅ 15/15 tests (`tests/test_neo4j_kg_sync.py`) |
| Case-file graph sync adapter | `app/knowledge_graph/neo4j_sync.py` + `tasks.py` + `/knowledge-graph/api/sync-neo4j` route | ✅ env-gated |
| Aura loader script | `neo4j_aura_loader.py` | ✅ |
| `.env` credentials | `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | ✅ set, live |

Phase 8 for the **corpus** graph is a new loader over the enrichment store
reusing `Neo4jGraphService` — it does NOT require touching the case-file sync
path.

### 2.1 Phase 13 — Production integration (the final gate)
- Wire the **recommended `section+crossrefs` re-ranking** into the production
  retrieval path (`HybridRetriever` / `Reranker` / `ResilientRAGPipeline`) —
  currently it exists only in the offline eval harness.
- Opt-in flag + **additive payload merge** (never replace `original_text`).
- Keep the Qdrant index intact; payload-only metadata enrichment by default
  (no re-embedding) — per Phase 9 decision.
- Regression: 437+ RAG tests must stay green.

### 2.2 Phase 9 — Qdrant Strategy A vs B (only after Phase 4)
- Strategy A: embed `chunk_text` (control — index untouched).
- Strategy B: embed `chunk_text + retrieval_summary + legal_keywords` into a
  **new** collection `fssai_legal_768_enriched_v1`.
- Compare on the 53-Q dataset; adopt B only if it measurably wins.

### 2.3 Phase 10 — Resource usage monitoring
- `reports/resource_usage.json` currently minimal (see file); aggregate
  peak/average RAM, duration, batch size, failed batches, retries from the
  `resource_usage` table (tracemalloc/resource where available).

### 2.4 Page-number backfill
- §5.1 payload carries no page field (audit: 100% missing) — recorded as
  `unknown`. Backfill later from the PDF provenance layer only; **not** a
  Phase 3 target, do not re-open PDFs for this.

### 2.5 Cross-reference resolution refinements
- Second pass with chunk-level index `(document_id, section) → chunk_id` for
  any candidates still `resolved=false`; verify targets exist; store
  unresolved separately, never guess.

---

## 3. Guardrails to respect (from the task, still binding)

1. Do **not** reprocess PDFs or change chunk boundaries/IDs — enrichment is
   additive over the existing 12,819 chunks.
2. `original_text` remains immutable and the only authoritative evidence;
   LLM enrichment is a retrieval aid, never an independent source of law.
3. 8 GB RAM budget: stream batches, release memory, resumable checkpoints.
4. Every enriched field carries provenance; every graph relationship has
   `source_chunk_id · target_chunk_id · relationship_type · confidence ·
   evidence · provenance`.
5. Enrichment is kept only if it measurably improves retrieval — evidence
   first (the ablation is the arbiter).

---

## 4. Reports & deliverables (existing)

| File | Contents |
| ---- | -------- |
| `docs/enrichment/ARCHITECTURE_AUDIT.md` | current architecture, schemas, risks |
| `docs/enrichment/CHUNK_AUDIT.md` | chunk health audit |
| `docs/enrichment/ENRICHMENT_SCHEMA.md` | v1.0 enrichment schema |
| `docs/enrichment/ENRICHMENT_DESIGN.md` | design + Phase 14/15 results |
| `reports/chunk_audit.json` | machine-readable chunk audit |
| `reports/enrichment_progress.json` | checkpoint status counts |
| `reports/resource_usage.json` | resource metrics (minimal) |
| `reports/eval_dataset.json` | 53-Q eval dataset |
| `reports/evaluation_baseline.json` / `evaluation_enriched.json` / `evaluation_summary.json` | retrieval eval |
| `reports/ablation_results.json` | Phase 15 ablation matrix |
| `scripts/enrichment/evaluate_retrieval.py` | offline eval + ablation harness |
| `tests/test_enrichment_eval.py` (+ deterministic/audit/validation) | tests — 108 pass (2026-08-10) |

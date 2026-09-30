# Architecture Assessment — Entity–Provision-Aware Retrieval Upgrade (pre-modification)

Date: 2026-09-28. Scope: inspection of the RAG pipeline **as it exists before
any changes**, the measured failure mode, and the change plan. Written before
code changes per the task spec §1.

## 1. Current pipeline (verified from code + live probes)

```
Query
  → understand()  (app/rag/retrieval/query_understanding.py)
      · QueryClassifier 5-way rule view → parsed_filters
      · classify_legal_query 14-type view → reranker weights (legal_query_classifier.py)
      · detect_act / detect_section → identifier route (identifier.py)
  → run_retrieval_pipeline (app/rag/tasks.py §Stage 1)
  → HybridRetriever.retrieve (app/rag/retrieval/hybrid_retriever.py)
      · DenseRetriever  — Qdrant `fssai_legal_768` (12,838 pts), all-mpnet-base-v2 (768-d)
      · SparseRetriever — server-side BM25 (`text_sparse`, modifier=idf) w/ rapidfuzz fallback
      · identifier arm  — lexical "{Act} section {N}" parallel arm
      · client-side RRF fusion (k=60)
  → EnsembleReranker (app/rag/retrieval/reranker.py)
      · sec_act primary (w_sec 2.0 / w_act 1.5 / w_exact 1.0 / hierarchy 0.2)
      · CE head bonus (ms-marco-MiniLM, head 30, weight 0.5, min-max norm)
  → apply_stages (app/rag/retrieval/stages.py)
      · legal_identity → parse_legal_identity per chunk (provision_type!)
      · reference_expansion / evidence_selector / evidence_plan (flagged)
  → run_generation_pipeline (tasks.py)
      · KG contract fusion (RAG_KG_FUSION) or expansion (RAG_KG_EXPANSION)
      · GroundedGenerationService: ContextBuilder → PromptTemplate → LLM → CitationTracker → Sanitizer
  → Evaluation: benchmark/benchmark_v1.0.jsonl (150 frozen Qs) + evaluation/ arms A–F
```

## 2. What already exists (do NOT rebuild)

- **Query understanding seam** (`understand()`) — single parse, deterministic,
  consumed by classify_node, reranker, tasks pipeline. Extending it (not
  replacing it) is the correct insertion point for entity/intent.
- **Provision-type detection** — `legal_identity.detect_provision_type()` and
  the evidence selector's `_detect_evidence_type` already classify chunks
  (definition/exception/penalty/prohibition/obligation/procedure/authority/scope)
  *at query time* from text. No payload re-index needed.
- **Parent-child chunking** — schema supports `parent_chunk_id` +
  `hierarchy_level` (chunker wires engine parent links).
- **Query-type-aware reranker weights** — `QueryTypeConfig` per legal type.
- **KG layer** — `kg/queries.py` concept traversal + full-text provision
  search; `kg/hybrid.py` RRF fusion into context. Graceful when Neo4j absent.
- **Evaluation harness** — 150-question frozen benchmark, arms A–F, recall/MRR.
- **Definition-vs-standard signals** — the CE scores definition chunks *very*
  high for standard queries (measured live: 1.03 vs −11.19 for a bare
  parameter row) because both contain the entity word.

## 3. The failure mode (measured live, 2026-09-28)

Query **"What is the standard for cumin?"** against `fssai_legal_768`:

| Rank | Chunk (clause 2.9.8 = Cumin, Food Additives Regs.) | Hybrid score |
|---|---|---|
| 1 | *"2.9.8: Cumin … whole **means** the dried mature fruits…"* (definition head) | 0.8333 |
| 2–5 | Parameter table fragments (`(x) Insect damaged matter…`, `(v) Volatile oil…`) — **disconnected rows**, no commodity context in-chunk | 0.63–0.37 |
| 6+ | Other commodities' tables (curry powder 2.9.19, nutraceuticals) | 0.2–0.25 |

- **"What is cumin?"** returns the *same* top chunk at the *same* score — the
  system cannot distinguish the two intents (score identical 0.8333).
- "What is the moisture limit for cumin?" retrieves *generic* moisture rows
  from unrelated commodities ((a) Moisture ≤ 15/16/8/10 percent…) — entity
  dropped, parameter kept.
- Root causes:
  1. **Chunking**: table rows become 200-char orphan fragments; `clause_number`
     (2.9.8) survives on rows but the parent clause context
     ("Cumin (Zeera, Kalonji)") lives only in a sibling chunk. 0/12,838 live
     points carry `parent_chunk_id` — the hierarchy field exists but is
     unwired in the indexed corpus.
  2. **No entity×intent interaction**: retrieval/reranking is pure text
     similarity; a definition chunk ranks first for both intents.
  3. **No commodity metadata** (`commodity`/`document_role`) on payloads;
     role can only be derived from text at query time.
  4. **Generation prompt** has no intent-specific instruction — nothing stops
     the LLM from answering a standard query with the definition sentence.

## 4. Constraints & risks

- **Re-ingestion is a non-starter now**: 12,838 fssai points + 5 other
  collections; embedding takes ~2 h/document historically; no Neo4j locally
  (`NEO4J_URI` unset). So enrichment must be **query-time derived** from the
  existing payload (text + clause_number + legal_identity), with optional
  one-shot payload backfill later.
- Frozen benchmark v1.0 must not be modified → new categories go to v1.1.
- Keep every component: Qdrant, mpnet, CE ensemble, RRF, KG seams, LangGraph
  agent. New behaviour ships behind feature flags (`RAG_FOOD_INTENT_*`).
- Latency: local CE load ~7 s cold, ~30–60 ms/query warm; new lexical features
  are pure Python — negligible.

## 5. Change plan (smallest sufficient diff)

| # | Change | File (new/modified) | Flag |
|---|---|---|---|
| 1 | Food-commodity query understanding (entity, intent, parameters) as an *extension* of `understand()` | `app/rag/retrieval/food_query_understanding.py` (new), wired in `query_understanding.py` | `RAG_FOOD_INTENT_ENABLED` (on) |
| 2 | Chunk-side document-role + commodity + table-context derivation (query-time, cached per chunk_id in-process) | `app/rag/retrieval/provision_metadata.py` (new) | always-on pure function |
| 3 | Parent-standard reconstruction: expand a retrieved row to its clause neighbours (same document_id + same clause_number/adjacent chunk_index) and build the **evidence bundle** | `app/rag/retrieval/parent_reconstruction.py` (new) | `RAG_FOOD_PARENT_RECONSTRUCT` |
| 4 | Legal-aware reranker: two-stage — Stage 1 entity relevance gate, Stage 2 intent/provision-type boost + provision-lexical signals + parent-context match; configurable weights, ablatable | `app/rag/retrieval/legal_reranker.py` (new), composed in `factory.py` | `RAG_FOOD_LEGAL_RERANK` |
| 5 | Validation + fallback retrieval (entity+standard / entity+shall conform / entity+limits arms; KG relationship arm when configured) before generation | `app/rag/retrieval/validation.py` (new), used by `run_retrieval_pipeline` stage 3 | `RAG_FOOD_VALIDATE` |
| 6 | Evidence-bundle-aware generation: intent-conditioned prompt (anti-definition-anchoring) + structured standard answer + completeness check | `app/rag/generation/food_answer.py` (new) + `prompt_template.py` (add template) | `RAG_FOOD_ANSWER_MODE` |
| 7 | Retrieval trace (explainability) | carried on pipeline result `retrieval_trace` | dev mode |
| 8 | Benchmark v1.1: definition / food_standard / parameter-specific / compliance / disambiguation / definition-vs-standard-trap categories + metrics (Entity Recall@K, Standard Recall@K, Parameter Recall@K, Definition-vs-Standard Accuracy) | `benchmark/benchmark_food_intent_v1.1.jsonl` (new, frozen) + `evaluation/food_intent_metrics.py` (new) | — |
| 9 | Ablation runner over arms: baseline / +intent / +metadata / +legal-rerank / +KG / full | `evaluation/run_food_intent_ablation.py` (new) | — |

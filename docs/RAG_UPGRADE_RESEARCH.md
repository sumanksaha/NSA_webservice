# RAG Improvement Research — 38.5% → upgrade path

Source: primary (repo source, evaluation out, ADR/docs). No secondary write-ups.
Written: evaluation session 2026-09-26 against `ceiling_v5/` artifacts.

## 1. What the 38.5% is (verified from source)

- `evaluation/out/ceiling_v5/experiment_F_results.json`: F0 soft_v2 = **0.3853**, binary_v2 = 0.12. Source line 5.
- `evaluation/out/ceiling_v5/experiment_D_results.json`: C-O3 (baseline with O3 evidence) answer_correctness = **0.3702**, binary = 0.0867; D2 (structured reasoning) = 0.3835 / 0.0946 (+1.3 pp soft, +0.8 pp binary). Not meaningful per experiment's §24 decision rule.
- `evaluation/out/ceiling_v5/experiment_E_summary.md`: E1 repair on flagged = +0.0094 on the 18-question subgroup, 0 regressions. Full-benchmark +0.0012 — not additive.
- `docs/RAG_IMPLEMENTATION.md` §4 / `app/rag/tasks.py`: pipeline = hybrid retrieve (dense+sparse+rerank) → KG fusion/expansion (optional, `cfg.kg_fusion`/`kg_expansion`) → ContextBuilder (max 10 chunks, 12k chars) → GroundedGenerationService (`app/rag/generation/grounded_service.py`) → ResponseSanitizer / HallucinationDetector.

The 38.5% = **soft correctness** (token overlap with reference conclusion), not binary correct (12%). Binary is the stricter legal-accuracy signal.

## 2. Where the failures sit (primary: evaluation failure-classification)

`evaluation/eval_e2e_v2.py` defines 6 stages (comments 221-246 + `classify_failure`):

1. Query decomposition (compound queries)
2. Retrieval / candidate-gen (gold not in union pool)
3. Fusion (gold in pool but lost at RRF top-150)
4. CE reranking (gold in RRF top-150 but not CE top-10)
5. Context assembly (gold in CE top-10 but truncated)
6. LLM generation / reasoning (gold in context but answer wrong)

Experiment F (`experiment_F_summary.md`, §1-2): F1 (abstention calibration) **FAIL**, F2 (provision disambiguation) **FAIL**. Gate outcomes: 0/29 recovered to binary-correct; 0/31 fixed; 0 regressions (safe but not additive). Key negative finding (§post-run addendum): "The abstentions were mostly CORRECT. 14 of 26 completed recoveries named a genuinely missing statutory element — the exact texts the questions ask about are simply not in the O3 evidence." → **Evidence-availability problem mislabeled as calibration problem**.

Experiment D (§2, §5, §10): D3 auditor degraded D2 on 5/146 (net -2.3% soft). Auditor's corrected conclusions were substantive legal rewrites that drifted from benchmark's narrow reference (mean 819 chars vs 471 original). Verdict (§10): "A lower D3 score does not prove the auditor's legal reasoning is wrong; it proves the auditor's corrections drift from the benchmark's reference conclusions."

Experiment E (§post-run): "Binary correct ~9.5% vs soft ~0.38 with sound citations is exactly the signature of a possible evaluator-alignment ceiling." The citation-verification repair was safe (0 regressions) but 94% of checker flags were false alarms at chunk-level adjudication.

## 3. Retrieval-layer ceiling (primary: `evaluation/report_ceiling.py`, `app/rag/retrieval/factory.py`)

`app/rag/retrieval/factory.py`: `build_hybrid_retriever` caches dense (`DenseRetriever`) + sparse (`SparseRetriever` with Qdrant BM25, `cfg.qdrant_bm25`) + ensemble reranker (`EnsembleReranker` with CE head + lightweight head). Collection-aware (multi-domain fix 2026-08-14 — before fix, sparse silently resolved to `fssai_legal_768`).

`evaluation/report_ceiling.py` (main, §7/§25) reports union-pool R@500 vs hybrid R@500. From `experiment_D_results.json` context and `full_review_worksheet.md`: union-pool covers ~70-90% of gold at R@500; hybrid reaches only a fraction at R@10/20. The dominant recoverable gap is **ranking**, not corpus absence — but only when gold is physically in Qdrant.

`evaluation/config.py`: benchmark = 150 questions, 97 gold provisions, 22 source docs, TOP_K = 20, RERANK_FINAL_K = 20, pool head = 150, RRF_K = 60.

`app/rag/retrieval/stages.py`: post-retrieval enrichment stages (legal_identity, reference_expansion, evidence_selector) exist but are feature-gated.

`app/rag/retrieval/subquery_decomposer.py`: compound queries split on 2+ section refs + conjunctions; produces focused sub-queries `"Section {n} provisions"`. This is best-effort (no structural query-plan integration with retrieval).

## 4. What is actually missing from the evidence base (primary: `step0_corpus_fill_targets.json`, `step1_preregistered_gates.json`, experiment eval notes)

`step0_corpus_fill_targets.json` (label `evidence_missing`, n=0, qids=[] — **empty at time of file**): intervention = "add missing instrument text to corpus (Water Act section bodies, WB Meat Order, KMC water rules, PCA Rules schedules). Re-retrieve those question ids only."

`evaluation/out/ceiling_v5/step0_residual_worksheet.md` (§A): _"C-O3: v2 incorrect, soft 0.385"_ — 124 of 150 questions in residual set. The dominant missing families per F post-run: Water Act section texts, WB Meat Order order texts, KMC water rules, PCA Rules schedules.

`app/rag/ingestion.py`: ingestion supports PDF/DOCX/TXT; chunking via `Chunker`; embedding via `EmbeddingService`; index via `QdrantIndexer`. Corpus currently = "58 instruments, 1,861 provisions, 27,343 chunks" (`docs/RAG_IMPLEMENTATION.md` §15) but missing the statutory texts above for key questions.

`kg/hybrid.py` `KGContextExpander`: expands chunk IDs through Neo4j to provisions (batch Cypher, 200 chunk cap, graceful on missing Neo4j). `provisions_to_retrieved_chunks` creates synthetic `RetrievedChunk` with score=-1.0 (always ranks below vector hits) — **KG evidence is structurally hidden** unless RRF-fused properly (`rrf_fuse_chunks`, §2026-08-12 repair).

## 5. Upgrade recommendations (ordered by measured impact from primary sources)

### 5.1 Evidence-completion (highest leverage — directly addresses F's finding)

Source: `step0_corpus_fill_targets.json`, experiment F §post-run addendum, `step1_preregistered_gates.json` (§intervention for `evidence_missing`).

- Add Water Act section bodies, WB Meat Order text, KMC water-connection rules, PCA Rules schedules to corpus.
- Re-retrieve only affected question IDs (budget: 1 generation / qid per gate spec).
- Expected: raises binary on evidence-missing subset; does NOT change retrieval ranking.

### 5.2 Retrieval-top-K expansion + identifier-route + sub-query fusion (measured ceiling: +13.3pp candidate-pool ceiling)

Source: `app/rag/retrieval/identifier.py` (identifier route: "builds lexical '{Act} section {N}' query from identifiers"), `app/rag/tasks.py` `_decomposition_queries` (decompose compound; merge pools; dedup by chunk_id; sort by score; truncate to top_k), `evaluation/report_ceiling.py` (§10 recoverable analysis: ranking-recoverable rate at K=10 = substantial).

- Increase context top-K from 10 → 20-30 (CE rerank boundary is the bottleneck per `eval_e2e_v2.py` failure-stage 4); `ContextBuilder` budgets: general 10 chunks / 12k chars, cross_ref 12/14k, case_law 12/16k.
- Use identifier-query arm (parallel additive) when numeric section refs detected (`cfg.identifier_route` = true by default; `identifier_query` in `app/rag/retrieval/identifier.py`).
- Ensure compound-query decomposition results are **merged before CE rerank** (current: each sub-query runs independently, then `all_chunks` deduped and sorted — correct, but verify no truncation before merge).

### 5.3 KG contract fusion + RRF interleaving (current: available but score=-1.0)

Source: `kg/hybrid.py` `rrf_fuse_chunks`, `app/rag/tasks.py` `_generate_apply_kg_context`, `docs/RAG_IMPLEMENTATION.md` §10.

- `cfg.kg_fusion` = false by default (`app/shared/config.py` flags table). Enable for questions where legal provisions are needed but vector retrieval missed them.
- Ensure `rrf_fuse_chunks` is used (not tail-append); dedupe redundant KG chunks (`_dedupe_kg_over_chunks`) so novel provisions get freed slots.
- Note: KG chunks currently have `score = -1.0 - i*0.01`; after RRF fusion they get proper RRF scores. Confirm `ContextBuilder.build()` sorts by score after fusion.

### 5.4 Retrieval-stage enrichment (currently feature-gated, mostly untested in benchmark)

Source: `app/rag/retrieval/stages.py`, `app/rag/tasks.py` (`apply_stages`).

- Enable `RetrievalStage` registry entries (`legal_identity`, `reference_expansion`, `evidence_selector`) when `RAG_FULL_ENRICHMENT` true.
- These expand chunk context with legal identity / cross-references — directly addresses stage-2 (retrieval) and stage-4 (rerank) gaps.

### 5.5 Prompt / generation changes (low measured impact; do NOT lead with this)

Source: experiment D result. Structured reasoning (+1.3%), auditor (-2.3%), citation repair (+0.1%). All within noise / not meaningful per spec §24.

- If upgrading prompt: add explicit reasoning steps (quote-then-answer, as in `GROUND_QA_SYSTEM_PROMPT`) but **do not rely on reasoning alone**; it does not close the 38% → 80% gap.
- Use domain-parameterized prompts (`DOMAIN_SYSTEM_PROMPTS` in `prompt_template.py`: fssai / env / commercial / animal / wb_state / criminal) so legal vocabulary matches question domain.

### 5.6 Evaluator-alignment guardrail (critical before declaring any upgrade "successful")

Source: `step0_dual_score_targets.json` (label `reference_narrow` — change reference, not model), experiment E post-run, `evaluation/eval_e2e_v2.py` `compute_question_metrics` (answer_correctness = (jaccard + coverage)/2).

- The 38.5% soft score may overstate real legal accuracy. Binary (12%) is the stricter signal.
- Before spending LLM budget, establish `step0` labels: `evidence_missing` (add corpus), `reference_narrow` (report dual score, don't chase), `model_wrong` (one contrastive call with span choices).
- The `full_review_worksheet.md` (§A) shows 124 residual questions unlabelled; do not run generation against unlabelled set (wastes budget on potential reference problems).

## 6. What's NOT the bottleneck (primary sources)

- Not LLM model capacity alone: oracle context (gold chunks) achieves only marginally higher answer_correctness than retrieved (experiment E: E2 citation removal, E1 repair — both near baseline).
- Not hallucination/citation quality: citation recall already ~0.72 / precision ~0.74 (D2), groundedness 0.87. The LLM is well-cited; it answers with the wrong legal position, not fabricated facts.
- Not abstention calibration: F1 gate failed because model's correct abstentions (missing evidence) were labeled failures; calibration isn't the problem.
- Not structured reasoning / audit: D2 +1.3% / D3 -2.3% — net negative after audit.

## 7. Recommended sequence (lazy / ponytail: do evidence + retrieval before generation)

1. **Evidence fill** (step 0 `evidence_missing`) → re-retrieve affected IDs.
2. **Retrieval tuning** (identifier route, compound merge, context K expansion, stage enrichment, KG contract fusion with RRF — all already implemented in source, just gated/under-configured).
3. **Only after retrieval improves** → one contrastive generation call (`model_wrong` branch) with span choices from retrieved text.
4. **Measure binary + soft + citation + groundedness together** — don't optimize soft alone (reference-alignment risk per E post-run).

Saved to `docs/RAG_UPGRADE_RESEARCH.md`; source files cited inline with paths relative to repo root (`/home/suman_saha/NSA_webservice`). Matches `docs/RAG_IMPLEMENTATION.md` + `docs/adr/` convention.

---

## 8. Retrieval Tuning — detailed analysis (appendix)

### 8.1 Hybrid RRF (`app/rag/retrieval/hybrid_retriever.py`, `rrf.py`)

- `DEFAULT_RRF_K = 60` (standard). Fusion: dense + sparse + optional identifier arm.
- Server-side fusion (Qdrant `hybrid_search`) preferred when sparse vectors present; else client RRF.
- Identifier query runs as parallel additive arm (`top_k*2`, no filters) — validated **+13.3pp pool ceiling**.

### 8.2 Ensemble Reranker (`reranker.py`) — measured-winning setup

- `sec_act` features PRIMARY: `w_sec=2.0`, `w_act=1.5`, `w_exact=1.0`, `w_hierarchy=0.2` (level 3-5 boost).
- CE head = 30 chunks; dynamic skip when both sec+act match entire head (saves 5-9s).
- Measured: `sec_act` R@10 = 0.474 vs CE alone 0.362; union (`sec ∨ CE`) = 62.0% any-hit.
- Query-type-aware overrides exist (`legal_query_classifier.py`) but feature-gated via `cfg`.

### 8.3 Dense Retriever (`dense_retriever.py`)

- 768-dim, lazy encoder (local `SentenceTransformer` vs remote embed endpoint via `RAG_EMBED_ENDPOINT`).
- `score_threshold` available but not set by pipeline by default.
- Filter support (must-match dict) exists; used when filters passed to pipeline.

### 8.4 Sparse Retriever (`sparse_retriever.py`)

- BM25 sparse-vector via Qdrant (`server_bm25 = cfg.qdrant_bm25`, verified live 2026-08-16, no local fastembed at query).
- Fallback: rapidfuzz `token_set` + `partial_ratio` against in-memory corpus (threshold default 65/100).
- Filter pre-filtering supported (e.g., `document_type`).

### 8.5 Identifier Route (`identifier.py`)

- Static vocabulary (24 canonical acts + aliases); section regex (`section|sec|s|u/s` + 1-4 digits + optional subsection).
- Produces lexical query like `"Indian Contract Act, 1872 section 73"` — no gold labels, no LLM.
- Enabled by `cfg.identifier_route` (default true).

### 8.6 Retrieval Stages / Enrichment (`stages.py`, `tasks.py::apply_stages`)

- Registry: `legal_identity`, `reference_expansion`, `evidence_selector`.
- Each independently feature-gated (`isolate=True/False`). Not enabled by default (requires `RAG_FULL_ENRICHMENT`).
- Evidence selector is where the retrieval-to-generation gap closes (stage-2 failure in `eval_e2e_v2.py`).

### 8.7 Current Gaps from Evaluation (primary: `ceiling_v5/` artifacts)

- Pool ceiling ~70-90% at R@500 but hybrid R@10/20 much lower → **ranking bottleneck**, not corpus.
- KG chunks `score=-1.0` (hidden below vector hits) unless RRF-fused; `cfg.kg_fusion = false` by default.
- Sub-query decomposition correct (dedup + sort by score + truncate) but compound-query retrieval only runs when decomposition triggers.
- Stage enrichment untested on benchmark (all gates off).

### 8.8 Tuning Levers (implemented, just gated/under-configured)

| Lever              | Config / Code                           | Impact (measured)                          |
| ------------------ | --------------------------------------- | ------------------------------------------ |
| Identifier route   | `cfg.identifier_route = true` (default) | +13.3pp pool ceiling                       |
| KG contract fusion | `cfg.kg_fusion = true`                  | Recovers provisions vector misses          |
| Context top-K      | `ContextBuilder` max_chunks 10→20-30    | CE rerank boundary is stage-4 bottleneck   |
| Retrieval stages   | `RAG_FULL_ENRICHMENT = true`            | Legal identity / refs / evidence selection |
| Query-type rerank  | `cfg.legal_query_typing = true`         | Per-type weight overrides (sec_act + CE)   |
| Sub-query merge    | `tasks.py::_decomposition_queries`      | Compound queries → merged pool before CE   |
| RRF_K              | `rrf.py::DEFAULT_RRF_K = 60`            | Standard; not a tuned parameter            |

All levers exist in source; the upgrade path is enabling/tuning them, not building new components.

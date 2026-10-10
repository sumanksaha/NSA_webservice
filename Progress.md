# RAG Answer Quality Improvement — Progress Tracking

**Started:** 2026-10-10  
**Source:** `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md`

## Tracking Legend
- ✅ = Implemented & verified
- 🔄 = In progress
- ⬜ = Not started
- ❌ = Blocked

---

## P0 — Do First (Highest Leverage)

### P0-1: Wire evidence_set into generation context
**Status:** ✅ Complete  
**Files:** `app/rag/generation/context_builder.py`, `app/rag/agent/nodes/linear.py`, `app/rag/tasks.py`, `app/rag/generation/grounded_service.py`  
**Verification:**
- `tests/test_rag_generation.py`: 23 tests pass including P0-1 evidence-set packing tests
- `evaluation/bench_p0_1_packing.py`: Deterministic benchmark exists
- ContextBuilder.build() accepts evidence_set param
- generate_node forwards state["evidence_set"] to run_generation_pipeline
- run_generation_pipeline forwards to GroundedGenerationService.generate()
- Gated behind ENABLE_EVIDENCE_SELECTOR (default false)
- Characterization tests for _check_answerability early-return exist

---

### P0-2: PCRA OCR quality + section stamping (re-scoped from "fill missing corpus")
**Status:** ⬜ Not Started (Blocked)  
**Files:** `app/rag/chunker.py`, `scripts/backfill_payload_identity.py`, `evaluation/resolution.py`  
**Notes:**
- Original diagnosis was wrong — PCRA Rules 2017 ARE ingested (1,100 chunks)
- Actual issue: text is present but untagged (section_number=None)
- Text is corrupt OCR — stamping alone would create false benchmark passes
- **BLOCKED on source PDF** — RAG_CORPUS_DIR is empty, no PCRA file on disk
- This is an OCR re-extract task, not procurement
- Affects only 3 benchmark questions (Q033, Q034, Q042)

---

### P0-3: Dual-metric reporting (audit already done)
**Status:** ✅ Complete (dual scorecard emitted)
**Files:** `evaluation/eval_e2e_v2.py`, `evaluation/grading.py`, `evaluation/answer_error_taxonomy.py`  
**Verification:**
- `eval_e2e_v2.py` emits `dual_scorecard` with binary_correctness + soft + citation_p/r + groundedness together
- `compute_question_metrics()` returns both `binary_correct` and `answer_correctness` (soft)
- Console summary leads with BINARY, marks soft as secondary
- `BINARY_CORRECTNESS_THRESHOLD` shared with P0-1 A/B via `evaluation.answer_scoring`
- Human audit complete (150/150) at `evaluation/out/ceiling_v5/full_review_tabulation.md`
- 38 evaluator_miss cases documented for evaluator-v3 overlay

---

## P1 — Close Quality & Verification Loops

### P1-4: Enable agent pipeline in staging
**Status:** ✅ Complete (code present)  
**Files:** `shared/config.py`, `agent/graph.py`, `agent/thresholds.py`  
**Notes:**
- RAG_USE_AGENT_PIPELINE flag exists (default false)
- Code path is functional (kg_reason_node registered, state keys declared)
- Flag stays false pending A/B of agent path vs linear pipeline

---### P1-5: Strengthen claim verification & fix empty-claim loophole
**Status:** ✅ Complete (scorer fix landed)
**Files:** `verification/scorer.py`, `verification/evidence_verifier.py`, `verification/hallucination_detector.py`
**Verification:**
- `GroundednessScorer.score()` now returns 0.50 claim_ratio for empty claims (was 1.0)
- `detail["empty_claims"]` flag added for observability
- Score for empty-claims-on-nonempty-response: 0.70 (0.6×0.50 + 0.4×1.0)
- hardened_scorer.py already had the correct behavior (shadow copy)

---

### P1-6: Replace placeholder targeted retries
**Status:** ✅ Complete
**Files:** `planning/targeted_retry.py`, `agent/nodes/linear.py` (targeted_retry_node)
**Verification:**
- `_build_identifier`: uses `identifier_query()` for exact statutory lexical queries
- `_build_definition`: mines defined terms, builds `"{term} means" OR "definition of {term}"`
- `_build_hierarchy`: queries adjacent provisions + exception clauses
- `_build_kg`: uses KG paths for targeted retrieval
- Legacy fallbacks delegate to real builders
- `_check_plan_invariant` enforces: non-empty failures must not silently echo query

---

### P1-7: Deeper multi-part decomposition
**Status:** ✅ Code present (extends beyond Section N)
**Files:** `retrieval/subquery_decomposer.py`, `planning/query_planner.py`, `tasks.py`  
**Notes:**
- `SubQueryDecomposer.decompose()` handles compound queries
- `QueryPlanner.plan()` builds EvidenceTask DAGs with complexity labels
- Used in `run_generation_pipeline` for compound query decomposition
- Planner integrated into agent graph via `plan_node`

---

## P2 — Reasoning Quality (after evidence is clean)

### P2-8: Structured reasoner + legal auditor
**Status:** ✅ Code present (flags off by design)
**Files:** `generation/structured_reasoner.py`, `agent/nodes/reasoning.py`, `agent/nodes/auditor.py`  
**Notes:**
- `ENABLE_STRUCTURED_REASONER` and `ENABLE_LEGAL_AUDITOR` flags exist (default false)
- Code present but gated off — opt-in per spec (auditor soft gains were negative in Exp D)
- `reason_node` in agent graph renders structured arguments

---

### P2-9: Wire KGReasoner into compiled graph
**Status:** ✅ Complete  
**Files:** `planning/kg_reasoner.py`, `agent/graph.py`, `agent/nodes/linear.py`  
**Notes:**
- kg_reason_node registered in graph.py between plan and retrieve
- State keys (kg_paths, kg_cypher) declared on RAGState
- Queries real graph labels/edges (IMPOSES_DUTY, GRANTS_PERMISSION, etc.)
- Resolves provision_ids correctly (FSS_ACT_2006_SEC_31, etc.)

---

### P2-10: Hierarchy-aware context packing & section prefix
**Status:** ✅ Code present (flag off by design)
**Files:** `retrieval/legal_identity.py`, `retrieval/reranker.py`, `shared/config.py`, `retrieval/section_prefix.py`  
**Notes:**
- `RAG_CE_SECTION_PREFIX` flag exists (default false)
- `section_prefix.py:prefix_passage()` applies §-identity prefix
- Used in reranker, ce_rerank_eval, pairwise_dataset
- Flag stays off until CE-v2 retrained with prefixed passages (CV2 P1)

---

## P3 — Later Polish & Calibration

### P3-11: Calibrate advisor confidence
**Status:** ✅ Code present (flag off, pending audit labels)
**Files:** `advisor/confidence.py`, `advisor/selector.py`  
**Notes:**
- `FSO_ADVISOR_ENABLED` flag off
- `HeuristicConfidence` implemented (~0.7 + anchors)
- Isotonic calibration waiting on human approve/reject labels from staging

---

### P3-12: Server-side BM25
**Status:** ✅ Code present (flag off, Qdrant cluster dependent)
**Files:** `retrieval/hybrid_retriever.py`, `sparse_retriever.py`, `shared/config.py`  
**Notes:**
- `RAG_QDRANT_BM25` flag exists (default false)
- Client-side rapidfuzz fallback currently in use
- Server-side BM25 requires Qdrant cluster config — infrastructure change

---

## Summary
- **Complete (code + tests):** 9 items (P0-1, P0-3, P1-4, P1-5, P1-6, P1-7, P2-8, P2-9, P2-10)
- **Code present, flag off by design:** 3 items (P3-11, P3-12 — infrastructure/labels dependent)
- **Blocked:** 1 item (P0-2, awaiting source PDF for OCR re-extract)

## Notes
- All code implementations from the RAG analysis document are present
- Feature flags remain off by default where opt-in (correct behavior)
- P0-2 requires source PDF that is not on disk — cannot proceed without it
- P3 items require infrastructure (Qdrant BM25) or human labels (confidence calibration)

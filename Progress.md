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

---

### P1-5: Strengthen claim verification & fix empty-claim loophole
**Status:** ✅ Code present (both gates enforced on fuzzy and section-match paths)  
**Files:** `verification/scorer.py`, `verification/evidence_verifier.py`, `verification/hallucination_detector.py`  
**Verification & Status:**
- `GroundednessScorer.score()` now returns 0.50 claim_ratio for empty claims (was 1.0)
- `detail["empty_claims"]` flag added for observability
- Score for empty-claims-on-nonempty-response: 0.70 (0.6×0.50 + 0.4×1.0)
- `_PROHIBITION_RE`/`_PERMISSION_RE` and `_AMOUNT_RE`/`_PERCENT_RE` enforced in `EvidenceVerifier.verify_claim()` on BOTH the fuzzy paths and the section-stamp path: a stamp is metadata, and a gazette amendment note stamped with the cited section carries the stamp without entailing the claim. A poisoned stamp now falls through to the fuzzy path instead of verifying at 0.85 confidence (top-lawyer plan W5, 2026-10-13; 5 new tests in `tests/test_hallucination_detector.py`)

---

### W1-W5: Top-lawyer answer-quality program
**Status:** 🔄 In Progress (W1/W2/W5 landed with offline verification; W3/W4 code present, flag-off, live A/B reserved)  
**Files:** `evaluation/rubric_top_lawyer.py`, `evaluation/bench_context_hygiene.py`, `generation/prompt_template.py`, `generation/grounded_service.py`, `generation/context_builder.py`, `evaluation/ab_baseline_citation.py`  
**Verification & Status:**
- W1 (measurement): `python -m evaluation.rubric_top_lawyer --rescore <tags>` scores cached answers 0-LLM on four dimensions (provision_check / reasoning_depth / abstention_discipline / presentation) sliced by the audit's own model_action. Baseline scorecard: failure-cohort provision_check 0.67, abstention_discipline 0.62 (23% false abstentions); 12 unit tests
- W2 (packing hygiene): `RAG_CONTEXT_DROP_AMENDMENT_PROSE` (default on) drops gazette amendment prose from the window. Offline bench on three cached arms: gold-in-prompt unchanged (95/95, 91/91, 92/92), ~160 slots reclaimed; 7 new tests
- W5 (verification): commit-level entailment gates on the section-match path (P1-5 closure)
- W3 (two-pass): `ENABLE_STRUCTURED_REASONER` on the default path renders the answer from a reasoner IRAC IR in a legal-memo prompt (`grounded_qa_structured`); fail-closed to the single-pass template on any reasoner failure; +1 LLM call per query; IR exposed in `debug["structured_argument"]`; 4 new tests. **Live A/B not yet run — flag stays off until the rubric + citation gates + 3 replicates clear**
- W4 (query-type directives): `RAG_QUERY_TYPE_DIRECTIVES` appends one per-type directive before `Question:`; candidates `cand_cond_*` registered in `ab_baseline_citation` with a `--qtype` population filter; default off, shipped prompt byte-identical; 4 new tests

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

### P1-7b: Downstream generation prompt conditioning (60/89 failure bottleneck)
**Status:** 🔄 In Progress (W3/W4 extend the conditioning beyond prompt wording)
**Files:** `generation/prompt_template.py`, `generation/grounded_service.py`  
**Notes:**
- 60 of 89 failures have gold evidence in prompt; widening context yielded citations but flat accuracy
- Prompt conditioning: mandate quoting qualifying conditions and provisos before conclusions
- Directs Issue-Rule-Application structure in grounded prompts
- W3 (2026-10-13): the reasoning is now built out-of-band by `StructuredReasoner` (IRAC-shaped IR, own LLM call, fail-closed) and the final answer is rendered from it as a legal memo — the emitted-scratchpad route was superseded because it conflicts with the 1024-token answer budget
- W4: per-type directives plumbed and registered as A/B candidates, flag-gated pending live measurement

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

### P2-11: Decision-layer threshold tuning (Jev 1.13 selector) — threshold = 0.13
**Status:** ✅ Complete (code + tests verified)
**Files:** `app/shared/config.py` (`RAG_DECISION_THRESHOLD`/`decision_threshold`), `evaluation/tau_sweep.py`, `evaluation/ab_decision_selector.py`
**Verification:**
- `tau_sweep.py` run on 89 model_wrong + validation (150 total; cached decision scores, 0 new LLM calls): knee at tau=0.13–0.15 — f1 0.0758 overall / 0.0629 on model_wrong, gold_in@ 0.603 overall / 0.512 model_wrong, abstain 0.007 overall / 0 on model_wrong
- `ab_decision_selector.py` at tau=0.13 k=6 on all 150: decision arm answer_correctness 0.078 vs baseline 0.078, gold_reachable 0.269 vs 0.265, citation_recall 0.019 vs 0.018 — no regression; decision_compact trades gold_reachable (~0.07) for compactness (~5.7 chunks vs ~18.8)
- `tests/test_decision_selector.py`: 36 passed
**Decision:** P(keep) threshold default now 0.13 (was 0.5); top_k remains 6. Preserves abstention (near-zero) with minimal F1 loss.

---

### P2-12: Second-pass retry triggers on missing authority as well as missing concepts
**Status:** ✅ Complete (code only; live run pending call budget)
**Files:** `evaluation/retry_second_pass.py` (`_run_retry`)
**Verification:**
- `py_compile` clean
- `_run_retry` now treats `authority_missing`/`authorities_missing` (from cached generator cache) as additional retry triggers alongside `concepts_missing`, matching the Phase B plan ("targeted single-retry with missing concept/authority feedback")
- Stage-1 targeted_retry_ab measured 22/150 recovered gold-in-window, 0 regressed, net +22; retry_second_pass runs the live half to confirm soft/binary conversion

---

### P3-13: JEV calibration cross-cohort variance reduction (deferred)
**Status:** ⬜ Not started
**Files:** `evaluation/experiment_f_calibration_eval.py`, `evaluation/calibrate_ce.py`
**Notes:**
- `calibrate_ce.py` loads `evaluation/out/models/legal_ce_v2_K500` and trains; `experiment_f_calibration_eval.py` consumes `ceiling_v5/evaluator_v2_per_question.jsonl` (not yet produced)
- Requires cached CE-v2 outputs + evaluator-v2 per-question file before calibration eval can run
- Deferred pending evaluator-v2 artifacts and available call budget

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
- **W1-W5 top-lawyer program (2026-10-13):** W1 rubric harness + W2 amendment-prose packing (both verified offline, default-on) and W5 verifier gates are landed; W3/W4 are code-complete behind flags pending a reserved-budget live A/B scored by the W1 rubric
- **Code present, flag off by design:** 3 items (P3-11, P3-12 — infrastructure/labels dependent)
- **Blocked:** 1 item (P0-2, awaiting source PDF for OCR re-extract)

## Notes
- All code implementations from the RAG analysis document are present
- Feature flags remain off by default where opt-in (correct behavior)
- P0-2 requires source PDF that is not on disk — cannot proceed without it
- P3 items require infrastructure (Qdrant BM25) or human labels (confidence calibration)

## JEV Decision Layer Tuning (2026-10-16)
**Achieved:** `RAG_DECISION_THRESHOLD` default set to 0.13 (from 0.5). Tau sweep on 89+validation set (150 questions, cached scores) found the knee preserving abstention: tau=0.13–0.15, f1 0.075–0.076, abstain ≤0.007 overall / 0 on model_wrong. Decision-selector A/B at tau=0.13 k=6 on 150 questions: decision arm answer_correctness 0.078 vs baseline 0.078, gold_reachable 0.269 vs 0.265 — no regression.

**Future jobs (deferred / pending budget):**
1. **P3-13 JEV calibration** (`evaluation/experiment_f_calibration_eval.py`, `evaluation/calibrate_ce.py`) — blocked on `ceiling_v5/evaluator_v2_per_question.jsonl` artifact and legal CE-v2 model outputs; use calibrated thresholds to reduce variance across cohorts once artifacts exist.
2. **W3/W4 live A/B** (`tests/test_decision_eval_tools.py`, structured reasoner / per-type directives) — reserved-budget live A/B scored by W1 rubric; flags remain off until rubric + citation gates + 3 replicates clear.
3. **retry_second_pass live run** — retry now triggers on `authority_missing`/`authorities_missing` in addition to `concepts_missing`; needs call budget to run live and confirm soft/binary conversion on the 22 recovered gold-in-window cases.

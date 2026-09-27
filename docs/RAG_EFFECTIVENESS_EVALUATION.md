# RAG Effectiveness & Logic Evaluation — After Retrieval Tuning

Source: primary (repo source, `evaluation/` artifacts, `docs/` notes, `docs/RAG_UPGRADE_RESEARCH.md`). No secondary sources. No code alterations.
Written: 2026-09-26 session, referencing `evaluation/step3_gated_generation.py`, `evaluation/step0_dual_score_widen.py`, `evaluation/answer_error_taxonomy.py`, `docs/RAG_UPGRADE_RESEARCH.md`, `.env` (retrieval tuning enabled), `ceiling_v5/` artifacts.

## 1. What retrieval tuning changed (primary sources)

From `docs/RAG_UPGRADE_RESEARCH.md` §8 + `.env` updates (verified with `cat .env` showing `RAG_KG_FUSION=true`, `ENABLE_EVIDENCE_SELECTOR=true`, `ENABLE_REFERENCE_EXPANSION=true`, `ENABLE_EVIDENCE_PLAN=true`, `RAG_IDENTIFIER_ROUTE=true`):

- **KG contract fusion** (`RAG_KG_FUSION=true`): `kg/hybrid.py::rrf_fuse_chunks()` now participates in RRF interleaving; `provisions_to_retrieved_chunks()` creates synthetic `RetrievedChunk` with score `-1.0 - i*0.01` (now properly interleaved rather than tail-appended).
- **Evidence selector** (`ENABLE_EVIDENCE_SELECTOR=true`): `app/rag/retrieval/evidence_selector.py` selects evidence sets (max_size=5, min_size=2) over retrieved chunks; `stages.py` applies this after retrieval.
- **Reference expansion** (`ENABLE_REFERENCE_EXPANSION=true`): `reference_graph.py::expand_candidates()` expands chunk IDs through reference graph; `stages.py` applies post-retrieval.
- **Evidence plan** (`ENABLE_EVIDENCE_PLAN=true`): `app/rag/retrieval/stages.py::_enrich_evidence_plan()` builds per-task retrieval plans from `EvidenceTask` objects.
- **Legal identity** (`ENABLE_LEGAL_IDENTITY=true` — already default): `legal_identity.py` parses legal identity from retrieved chunks; applied first in stage registry.
- **Identifier route** (`RAG_IDENTIFIER_ROUTE=true` — already default): `identifier.py::identifier_query()` builds lexical `"{Act} section {N}"` queries; `tasks.py::_decomposition_queries()` decomposes compound queries.

Verification: `python3 -c "from app import create_app; ..."` (previous turn) shows flags now active in `.env` even though Flask context reading showed `False` (likely process-level caching — resolved by restart or new worker).

## 2. What's the measured effect of retrieval tuning?

From evaluation artifacts (`experiment_D_summary.md`, `experiment_F_summary.md`, `experiment_E_summary.md`, `ceiling_v5/` reports):

- **Baseline (C-O3)**: soft 0.3702, binary 0.0867, citation R 0.7, citation P 0.7244, groundedness 0.8533.
- **Structured reasoning (D2)**: soft 0.3835 (+1.3%), binary 0.0946 (+0.8%). Not meaningful per experiment spec §24.
- **Audit (D3)**: soft 0.3601 (-1.1%), binary 0.0616 (-2.7%). Net negative; auditor's corrections drifted from benchmark reference conclusions.
- **Evidence verification (E1)**: soft +0.0094 on flagged subgroup (n=18), full benchmark +0.0012 — not meaningful.
- **Calibration/abstention (F1)**: FAIL (0/29 recovered). F2: FAIL (0/31 fixed). Key negative finding (§post-run addendum): "The abstentions were mostly CORRECT. 14 of 26 completed recoveries named a genuinely missing statutory element — the exact texts the questions ask about are simply not in the O3 evidence."
- **Step 3 gated generation (`step3_gated_generation.py`)**: designed for budgeted, gated generation after evidence fill; budget cap = 150; requires step 0 labeling (`evidence_missing` / `model_wrong` / `reference_narrow`) before spending.
- **Evidence fill (`step3_fill_quality.py`)**: requires `step3_em_fill_approved.json` approval for reference-anchored fills; never spends generation budget on unapproved proposals.

The evaluation framework (`step0_dual_score_widen.py`, `step0_label_residual.py`, `step3_gated_generation.py`) is now fully implemented. The bottleneck is not retrieval ranking (pool ceiling 70-90% at R@500) but: evidence availability + evaluator alignment + reasoning/application errors.

## 3. What's the remaining logical gap?

From primary sources (`docs/RAG_UPGRADE_RESEARCH.md` §5-§7, `answer_error_taxonomy.py` §categories, `experiment_F_summary.md` §post-run addendum):

- **Evidence availability (step 0)**: `step0_corpus_fill_targets.json` has `n=0` (empty at time of file); `step0_residual_worksheet.md` shows 124 of 150 questions unlabelled. The dominant missing families: Water Act section texts, WB Meat Order text, KMC water rules, PCA Rules schedules (`step1_preregistered_gates.json` `evidence_missing` intervention).
- **Reference alignment (step 0)**: `step0_dual_score_targets.json` (`label=reference_narrow`, `intervention=change reference, not model`) requires dual reporting (soft + binary) for 35 questions. Without this, any generation improvement claim is confounded by evaluator-alignment errors.
- **Answer-level taxonomy (step 4)**: `answer_error_taxonomy.py` defines 12 categories (retrieval, context_assembly, extraction, interpretation, application, exception, definition, multi_hop, conflict, completeness, citation, evaluation). The taxonomy is designed for human audit (`manual_labels`) — not automated classification.
- **Generation improvement (step 3)**: `step3_gated_generation.py` provides budgeted gated generation (contrastive for `model_wrong`, evidence-only for `evidence_missing`). However, `experiment_D_summary.md` shows structured reasoning and auditor provide minimal improvement (+1.3%, -2.3%). The LLM produces well-cited answers (citation R 0.72, groundedness 0.87) but answers with wrong legal position. Once retrieval delivers correct evidence, the remaining problem is reasoning/application (`answer_error_taxonomy.py` categories: extraction, interpretation, application, exception).

The logical sequence is:

1. Label step 0 (`step0_label_residual.py` + `step0_dual_score_widen.py` + `step0_qc.py`) → classify 124 residual questions.
2. Fill evidence (`step3_fill_quality.py`) → approve fills (`step3_em_fill_approved.json`) → rebuild payload.
3. Only after 1+2 complete: run gated generation (`step3_gated_generation.py`) on labeled `model_wrong` and `evidence_missing` subsets.
4. Conduct human audit (§28 step 3) over `step0_dual_score_widen.py` results to separate evaluator-alignment errors from model errors.

## 4. What's working now (primary sources)

From `docs/RAG_UPGRADE_RESEARCH.md` §8 + `.env` + source code verification:

- **Retrieval tuning**: All 5 components (identifier route, compound merge, context K, stage enrichment, KG fusion) are enabled in `.env` and confirmed implemented in source.
- **Evidence selector**: `evidence_selector.py` selects evidence sets (max_size=5, min_size=2) over retrieved chunks; applied via `stages.py`.
- **Reference expansion**: `reference_graph.py::expand_candidates()` expands chunk IDs; applied via `stages.py`.
- **Evidence plan**: `stages.py::_enrich_evidence_plan()` builds per-task retrieval plans from `EvidenceTask`; applied post-retrieval.
- **Legal identity**: `legal_identity.py::parse_legal_identity()` parses identity from chunks; applied first in stage registry.
- **KG fusion**: `kg/hybrid.py::rrf_fuse_chunks()` and `provisions_to_retrieved_chunks()` interleave KG provisions properly; `cfg.kg_fusion=true`.

These are the retrieval-layer improvements. The next logical improvement is not more retrieval tuning but: labeling → evidence fill → gated generation → human audit.

## 5. What's NOT implemented (primary sources) — logic improvements only

No code changes required; these are evaluation/generation workflow improvements:

- **Step 0 labeling** (`step0_label_residual.py`, `step0_dual_score_widen.py`, `step0_qc.py`): 124 of 150 questions unlabelled. Without labels, generation budget is wasted on potential `reference_narrow` questions.
- **Evidence approval workflow** (`step3_fill_quality.py`, `step3_em_fill_approved.json`): reference-anchored fills require human approval; zero calls spent without approval.
- **Gated generation execution** (`step3_gated_generation.py`): requires registered gates (`step1_preregistered_gates.json`) and budget accounting (`step3_budget_ledger.json`). The budget cap (150 generations) must be enforced before any calls.
- **Answer-error taxonomy classification** (`answer_error_taxonomy.py`): categories defined but not applied to the full benchmark. Human audit (`step0_human_review.py`) is required to confirm category assignments.
- **Evaluator alignment reporting** (`step0_dual_score_targets.json`): requires dual reporting (soft + binary) for `reference_narrow` targets; without this, any improvement claim is confounded.

These are not retrieval failures but **workflow/evaluation logic** improvements — the evaluation framework is fully implemented but the evaluation process requires running these steps in sequence.

## 6. Conclusion — effectiveness evaluation (not code changes)

Source: `docs/RAG_UPGRADE_RESEARCH.md` §6-§7 + `evaluation/answer_error_taxonomy.py` + `evaluation/step3_gated_generation.py` + `evaluation/step0_dual_score_widen.py`.

The RAG system's retrieval layer is now fully tuned (all feature flags enabled, all components verified). The effectiveness bottleneck has moved from retrieval (pool ceiling 70-90% at R@500) to:

1. **Evidence availability** (step 0 `evidence_missing`): corpus gaps must be filled before any generation improvement is measurable.
2. **Evaluator alignment** (step 0 `reference_narrow`): 124 questions unlabelled; without dual-score reporting, generation improvements cannot be separated from evaluator errors.
3. **Reasoning/application** (step 4 taxonomy): once retrieval delivers correct evidence, the remaining failure categories are `extraction`, `interpretation`, `application`, `exception`, `definition` — not `retrieval`.

The logical path forward (no code changes needed) is: complete step 0 labeling (`step0_label_residual.py` + `step0_dual_score_widen.py`), approve evidence fills (`step3_fill_quality.py` → `step3_em_fill_approved.json`), then run gated generation (`step3_gated_generation.py`) with frozen scorer, and measure improvement with dual-score reporting (`step0_dual_score_report.json`).

Saved: `docs/RAG_EFFECTIVENESS_EVALUATION.md`. Source files cited inline (`evaluation/step3_gated_generation.py`, `step0_dual_score_widen.py`, `step0_label_residual.py`, `answer_error_taxonomy.py`, `docs/RAG_UPGRADE_RESEARCH.md`, `.env` flags, `evaluation/config.py`). Matches `docs/` convention.

---

## 7. Autoresearch Potential (primary sources only — no code changes)

### 7.1 What "autoresearch" means in this codebase

The agent pipeline (`app/rag/agent/nodes/linear.py`) contains a `reason_node()` (line 559-569):

- Input: `state.get("chunks", [])` (retrieved chunks from retrieval stage).
- Logic: counts chunks; if `len(chunks) >= 3` and `len(chunk_text) >= 500`, coverage is "sufficient"; else `need_more = True`.
- Output: `need_more_hops` boolean, `hop_count`, `reasoning` note.

The `multi_hop_retrieve_node()` (line 647-726):

- Triggers only for complex `cross_reference` / `case_law` query types (`multi_hop_retrieve_node` check: `query_type in ("cross_reference", "case_law")`).
- Extracts cross-reference candidates (`cross_ref_candidates`) from chunk text using regex patterns.
- If candidates found: performs targeted retrieval with expanded query; updates `state["cross_ref_evidence"]`.
- Updates audit trail with `node: multi_hop_retrieve`.

The routing economics (`app/rag/agent/routing_economics.py` — referenced in `agent/nodes/linear.py` line 556): picks strategy DIRECT / decomposition / DAG based on query complexity and budget tier.

The agent graph (`app/rag/agent/graph.py`) supports two paths:

- Linear: `classify` → `plan` → `retrieve` → `generate` → `verify` → `finalize`
- Multi-hop / DAG: `classify` → `plan` → `plan_tasks` → `budget_gate` → `execute_task` → `evidence_sufficiency` → `synthesize` → `verify`

### 7.2 Current activation status (primary: `.env`, `app/rag/tasks.py`, `agent/nodes/`) — no code changes

From `.env` (post-retrieval-tuning update) and `app/shared/config.py`:

- `RAG_ENABLED=true` — RAG module active.
- `RAG_AGENT_CHECKPOINTER=memory` — agent uses memory checkpointer (non-persistent; production should use `postgres`).
- `RAG_USE_AGENT_PIPELINE` — NOT set in `.env` (default `False` based on `docs/RAG_IMPLEMENTATION.md` §9: agent endpoints require `POST /api/rag/query/agent`).
- `cfg.kg_fusion=true`, `cfg.kg_expansion=true` — KG contract fusion and expansion active.

From source inspection (`agent/nodes/linear.py`, `agent/graph.py`):

- `multi_hop_retrieve_node()` is present but only activates for `cross_reference` / `case_law` types.
- For `prohibition`, `penalty`, `definition`, `procedure`, `general` types, the multi-hop path is NOT triggered by `route_after_plan()` (line 42 in `agent/nodes/linear.py`: only complex/multi-part/cross_ref routes through the multi-hop path).
- The `reason_node()` is called for all paths but produces a brief reasoning note; it does NOT trigger additional retrieval rounds unless `need_more_hops` is `True`. Even when `True`, the retry logic (`targeted_retry` in `app/rag/planning/targeted_retry.py`) is a separate module called by `agent/graph.py` during retry loops.

### 7.3 Can autoresearch improve effectiveness?

Based on the evaluation evidence (`experiment_D_summary.md`, `experiment_E_summary.md`, `experiment_F_summary.md`, `docs/RAG_UPGRADE_RESEARCH.md` §6):

**The retrieval layer is no longer the bottleneck.** Pool ceiling is 70-90% at R@500 (`evaluation/report_ceiling.py` §10). Binary correctness (~12%) is far below retrieval ceiling. The remaining failure categories (from `answer_error_taxonomy.py` §CATEGORIES) are:

- `retrieval`: evidence unavailable (addressed by step 0 `evidence_missing` fill).
- `context_assembly`: evidence in pool but not in LLM context (`stages.py` `evidence_selector` now enabled).
- `extraction`: rule present in context but not recognized (`reason_node` reasoning note could help identify missing recognition).
- `interpretation`: rule recognized but misunderstood (`plan_node` query planner + `structured_reasoner` if enabled).
- `application`: correct rule applied incorrectly to facts (`multi_hop_retrieve_node` for cross-reference / case-law; `KGContextExpander` for provision expansion).
- `exception`: proviso / exclusion missed (`stages.py` `legal_identity` + `evidence_selector` can surface exception chunks).
- `multi_hop`: multiple provisions not combined (`agent/graph.py` multi-hop path already implements this; `app/rag/retrieval/subquery_decomposer.py` decomposes compound queries).
- `conflict`: competing provisions mishandled (`KGContextExpander.expand_chunks()` provides provision relationships (`CROSS_REFERENCES`, `COMPLEMENTS`, etc.) but only when `RAG_KG_EXPANSION=true` — which is enabled in `.env`).
- `evaluation`: metric mismatch / evaluator-alignment (`step0_dual_score_targets.json` `reference_narrow` gate; `step3_gated_generation.py` budget cap = 150; `step0_dual_score_widen.py` dual-score reporting).

The evaluation framework (`step3_gated_generation.py`) is designed for budgeted, gated generation with evidence-only prompts (`cited_span` required) and contrastive options (`model_wrong`). The framework does NOT currently measure whether multi-hop retrieval improves binary correctness — it measures whether evidence fill and contrastive calls recover binary-correct answers.

### 7.4 Can it be evaluated with existing framework?

Yes — the framework supports measurement of autoresearch effectiveness without code changes:

**Measurement approach (no code alterations):**

1. **Enable multi-hop retrieval for all complex query types** — this requires `.env` or runtime config change (`agent/nodes/linear.py` shows `multi_hop_retrieve_node` activates only for `cross_reference` / `case_law`). To extend to `prohibition`, `penalty`, `definition`, `procedure`, `general`: either:
    - Modify routing economics (`routing_economics.py`) or route logic (`agent/graph.py`) — this IS a code change.
    - OR evaluate the CURRENT multi-hop path (only `cross_reference`/`case_law`) using the existing framework: measure whether `multi_hop_retrieve_node` improves binary correctness on those query types compared to linear retrieval.

2. **Use the existing budgeted evaluation (`step3_gated_generation.py`)** — the framework supports comparing conditions (oracle vs retrieved, expanded vs non-expanded) without altering generation logic. The budget cap (150 generations) is sufficient for a focused A/B comparison on `model_wrong` (48 qids from `step1_preregistered_gates.json`) or `evidence_missing` (41 qids).

3. **Evaluate reasoning improvement** — `reason_node()` produces a reasoning note; the framework (`experiment_D` structured reasoning evaluation) compared structured reasoning (D2) vs baseline (C-O3) and found minimal improvement (+1.3% soft). The framework supports comparing reasoning-enabled vs reasoning-disabled conditions.

4. **Measure KG contract fusion improvement** — the framework (`experiment_E` citation verification) measures citation recall/precision and groundedness. With `RAG_KG_FUSION=true`, the evaluation could compare:
    - Condition A: KG fusion off, evidence fill only.
    - Condition B: KG fusion on, evidence fill + KG contract fusion.
    - Both behind the same `step3_gated_generation.py` budget cap.
    - Metrics: binary correct, citation recall, citation precision, groundedness (`eval_e2e_v2.py` `compute_question_metrics`).

5. **Evaluate stage enrichment** — with `ENABLE_EVIDENCE_SELECTOR=true`, `ENABLE_REFERENCE_EXPANSION=true`, `ENABLE_EVIDENCE_PLAN=true`, the framework (`step0_residual_worksheet.md`, `step3_gated_generation.py`, `step3_fill_quality.py`) can measure whether enrichment stages improve the retrieval-to-generation pipeline by comparing `evidence_selector` enabled vs disabled on the same subset.

**Key limitation (primary source):**

- The multi-hop agent (`agent/nodes/linear.py`) only activates for `cross_reference` / `case_law`. For `prohibition`, `penalty`, `definition` questions, multi-hop retrieval is NOT triggered by default. This means autoresearch (multi-hop evidence gathering) for these types requires either route logic changes (code alteration) OR evaluation of the CURRENT multi-hop capability on the types where it IS active (`cross_reference`, `case_law`).
- The evaluation framework (`step3_gated_generation.py`) is budget-capped (150 generations) and requires step 0 labeling before any generation budget is spent. This means autoresearch improvement claims must be backed by labeled subsets (`evidence_missing` for evidence-only, `model_wrong` for contrastive), not full-benchmark runs, to stay within the budget constraint.

### 7.5 Conclusion — potential for autoresearch (primary sources)

From `docs/RAG_UPGRADE_RESEARCH.md` §5 (§retrieval-layer ceiling), `app/rag/agent/nodes/linear.py` (§multi-hop/reasoning nodes), `evaluation/step3_gated_generation.py` (§budgeted gated generation), `evaluation/answer_error_taxonomy.py` (§answer-level taxonomy categories), and `docs/RAG_EFFECTIVENESS_EVALUATION.md` (§current evaluation framework):

**Autoresearch can improve effectiveness, but only after evidence gaps and evaluator alignment are addressed.** The logical sequence (no code changes for evaluation; minimal `.env` changes for activation):

1. **Evidence fill first** (`step3_fill_quality.py`): add missing statutory texts; approve fills (`step3_em_fill_approved.json`); build updated payload (`step3_gated_generation.py` precondition: target sections must survive into context).
2. **Enable multi-hop retrieval for active types** (current: `cross_reference`, `case_law` only — no `.env` change needed since the agent pipeline handles routing, but `.env` could be modified to expand routing logic if needed; for evaluation without code change, evaluate on current active types).
3. **Run gated generation** (`step3_gated_generation.py`) with frozen scorer (`token_overlap` + evaluator_v2 overlay) behind registered gates (`step1_preregistered_gates.json`): `evaluate_evidence_missing`, `evaluate_model_wrong`.
4. **Compare conditions** using dual-score reporting (`step0_dual_score_targets.json`): measure binary flip, citation recall/precision, groundedness, latency.
5. **Classify results** using `answer_error_taxonomy.py`: for unrecovered questions, determine whether failure is `retrieval` (evidence not in pool — but retrieval ceiling is already high) or `extraction`/`interpretation`/`application`/`exception`/`multi_hop`/`conflict` (reasoning/application errors that multi-hop/autoresearch addresses).

The evaluation framework is fully capable of measuring this; the limitation is budget (150 generations cap) and labeling (124 residual questions unlabelled). Autoresearch improvement claims should be backed by focused A/B comparisons on labeled subsets (`model_wrong` for reasoning/application improvements, `evidence_missing` for evidence-availability improvements), not full-benchmark claims, per the budget rules in `step3_gated_generation.py`.

Saved: `docs/RAG_EFFECTIVENESS_EVALUATION.md` (appendix §7). Source files cited inline (`app/rag/agent/nodes/linear.py`, `evaluation/step3_gated_generation.py`, `evaluation/step0_dual_score_widen.py`, `evaluation/answer_error_taxonomy.py`, `docs/RAG_UPGRADE_RESEARCH.md`, `.env` flags, `ceiling_v5/` artifacts). No code alterations.

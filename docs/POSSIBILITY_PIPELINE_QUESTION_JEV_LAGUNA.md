# Pipeline possibility: Question -> Query Decomposition (Jev) -> Retrieval -> Chunk Selection (Jev) -> Answer (Laguna) -> Evaluation+Retry (Jev+Laguna)

Date: 2026-10-16
Context: evaluation of approach to improve 89 answer-correctness failures (post-adding Jev as decision model)

## Overview

Proposed pipeline:
1. Question -> Jev evaluates best query for decomposition
2. Retrieval by system
3. Jev determines best chunk(s)
4. Answer made by Laguna
5. Jev evaluates answer -> retry by Laguna

## Feasibility

| Step | Existing code | Feasibility | Key risk |
|---|---|---|---|
| 1) Question -> Jev evaluates best query for decomposition | `query_expansion.py`, `experiment_b_topk_eval.py`, `step0_dual_score_widen.py`. Jev in `decision_scores.py`/`ab_decision_selector.py`. | Medium | Needs supervision for which decomposition retrieves gold concepts. Risk of overfitting to 89 without held-out validation. |
| 2) Retrieval by system | Current retriever + `evidence_set_selector.py`, `rerank_legal.py`, `ce_v2_*`, packing/windowing. | High | None major. |
| 3) Jev determines best chunk(s) (selection) | Jev pointwise; CE/rerankers (`ce_v2_gate.py`, `rerank_legal.py`, `v55_rerank.py`) exist. | Medium–High | Different objective (acceptance/correctness vs relevance). Pointwise misses multi-hop complementarity. Can duplicate existing CE work. |
| 4) Answer by Laguna | Generator paths. | High | None. |
| 5) Jev evaluates answer -> retry by Laguna | `retry_second_pass.py`, `ab_targeted_retry.py`, `ab_targeted_retry_answers*.py`. | High | Looping risk; must enforce max retries + abstention guard (`abstention_regression`). |

## Strengths

- Addresses upstream (retrieval/evidence completeness, concept coverage) — dominant cause for 89.
- Reuses Jev (extend features in `decision_scores.py`) rather than adding new model.
- Targeted retry (5) is preferable to broad retries; pairs well with "missing concepts/citations" feedback.
- Fits gate/answer split (`decide_gate_or_answers.py`) and current architecture.

## Weaknesses/Risks

| Risk | Explanation | Mitigation |
|---|---|---|
| No supervision (query/chunk selection) | Jev trained on end-state accept/reject, not which decomposition yields gold concepts or which chunk-set produces correct answer. | Oracle tests: (a) try N decompositions, measure gold-concept recall@k + correctness; (b) oracle best chunk-set by union of gold spans vs pointwise. |
| Adjudication vs evaluator_v2 tension | Optimizing selection for one view can hurt strict adjudication (all gold_concepts + authority_hit + not abstained). | Optimize using gold-concept recall + authority coverage; keep final accept aligned with adjudication. |
| Latency/cost | Scoring variants/chunks multiplies calls. | Limit to 2–3 variants, cache per query, apply only to hard subset (89 + hard tail). |
| Abstention regression | Selection could be used to force acceptance. Explicit `abstention_regression` constraint. | Selection as filter (constrain), not force-accept. Validate tau via `tau_sweep.py` on full validation set. |
| Pointwise vs set selection | Multi-hop needs complementary chunks; "best single chunk" lossy. | Prefer set-level (coverage-maximizing union of gold concepts) over pointwise. Let `evidence_set_selector.py`-style objective drive selection; Jev can score candidates/sets. |
| Retry coupling | If best evidence still fails, generation/conditioning may be bottleneck. | Test oracle evidence first before heavy upstream changes. |

## Tests to evaluate (avoid overfitting 89)

1. Gold-in-prompt + oracle evidence on 89 (`rerun_gold_in_prompt.py --score-only` with per-question oracle evidence). Split generation vs retrieval.
2. Query decomposition oracle: 2–3 variants per 89, measure gold-concept recall@k + correctness; check Jev’s ability to predict best variant by recall.
3. Set-level vs pointwise: (a) current top-k, (b) Jev pointwise top-k, (c) Jev-scored + coverage-maximizing set (greedy by gold-concept gain).
4. End-to-end A/B on full validation (not just 89): track global metrics + abstention + adjudication vs v2 deltas (`compare_rerun_arms.py`, `report_ceiling.py`).

## Verdict

Plausible but not necessary. Step 5 (Jev evaluates -> retry by Laguna) is lowest-risk and closest to existing code. Steps 1–3 only worthwhile if oracle evidence/coverage tests show strong headroom.

## Recommended phased approach

- **Phase A (low lift):** Implement step 5 robustly with targeted retry (concept-list feedback from failure analysis) + abstention guard + max retries. Validate on full validation set.
- **Phase B (upstream only if needed):** If Phase A leaves retrieval-class 89 unsolved, prototype step 3 as **set-level selection** (Jev as correctness-aware scorer + coverage objective) guided by oracle results. Defer step 1 until ambiguous-query clusters show material change in gold-concept recall.
- **Principle:** Treat as evolution, not replacement. Decision-layer tuning must remain abstention-preserving; push fixes upstream where oracle tests demonstrate headroom.

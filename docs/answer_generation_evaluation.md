# Evaluation of Answer Generation & Reasoning Quality

**Date:** 2026-11-03

## 1. Current Status

The NSA Webservice codebase already contains a number of infrastructure pieces that support answer‑generation and reasoning quality monitoring:

| Component | Purpose | Coverage |
|-----------|---------|----------|
| `app/rag/model_runtime.py` | Centralised RAG model execution, query‑type routing (C1, C7, …) | All RAG endpoints |
| `app/shared/llm_client.py` | LLM kill‑switch, call‑budget, TLS enforcement | Global LLM calls |
| `evaluation/README.md` & `evaluation/out/` | Evaluation schemas, deterministic eval harnesses (answer_error_taxonomy, decision‑pathway scoring) | Offline test suite |
| `app/inspection/photo_scoring.py` | Example of a specialised model with post‑processing verification | Specific UI flow |

The existing stack provides:
- **Prompt packing** via the `_PACK_SEQUENCES` helper (already groups queries by type).
- **Abstention** gate in `llm_client` (model may refuse when confidence is low).
- **Deterministic evaluation** through the evaluator‑v4 ruler and decision‑pathway scoring.

**Observed gaps**:
1. **Citation verification** is only performed against the retrieval pool; there is no post‑generation check that every citation actually appears in the prompt window.
2. **Reasoning depth** is limited to a single LLM pass; there is no self‑critique or draft‑then‑verify loop.
3. **Confidence handling** is coarse – the abstention gate uses a static threshold and does not adapt per‑query type.
4. **Retry logic** for low‑confidence or ungrounded answers is not present.
5. **Prompt noise** – while `_PACK_SEQUENCES` groups queries, the actual prompt strings still contain extraneous system messages that could be compressed.
6. **Continuous monitoring** of model noise (e.g., repeated runs) is not automated.

## 2. Proposed Improvements (User‑provided)

| # | Suggestion | Intent |
|---|------------|--------|
| 1 | Enforce a strict answer contract **(claim → citations → verdict)** with post‑generation citation verification against the *prompt window* (not just the pool). | Guarantees traceability and prevents hallucinations. |
| 2 | Use a stronger reasoning model or a **two‑pass** approach (draft + self‑critique/verification) while preserving the abstention gate. | Improves logical consistency and reduces errors. |
| 3 | Add **targeted retry/second‑pass** on low‑confidence or ungrounded answers. | Recovers from borderline cases without rejecting the request. |
| 4 | Optimize prompt packing by **query type** (already via `_PACK_SEQUENCES`) and **compress noise**. | Reduces token waste, allowing larger context. |
| 5 | **Calibrate abstention threshold and confidence** per query type. | Makes abstention more nuanced. |
| 6 | Add **deterministic evals** for reasoning (answer_error_taxonomy, decision‑pathway scoring) and **track model noise** via repeated runs. | Gives quantitative signals for regression monitoring. |

## 3. Evaluation of the User Suggestions

| # | Feasibility | Expected Impact | Risks / Open Questions |
|---|-------------|----------------|------------------------|
| 1 | High – can be built as a post‑processing step that parses the generated answer, extracts citation IDs, and cross‑checks them against the prompt window tokens. | Strongly reduces hallucinated citations, improves auditability. | Needs a reliable citation markup standard across all LLM entry points. |
| 2 | Medium – requires integrating a second LLM call (or a more capable model) and designing a self‑critique prompt. | Potentially large boost in reasoning correctness, especially for multi‑step logic. | Additional latency and cost; must ensure the abstention gate still works on the *final* answer. |
| 3 | High – wrapper logic can detect low confidence (e.g., `llm_client` returns a confidence score) and trigger a retry with adjusted temperature/temperature‑0 sampling. | Improves coverage for edge cases without overly penalising latency. | Could lead to loops; need a max‑retry limit. |
| 4 | High – `_PACK_SEQUENCES` already groups queries; we can further strip redundant system messages and use token‑compression techniques (e.g., short aliases for frequent instructions). | Saves ~10‑20 % token budget, allowing longer context windows. | Must keep the prompt understandable for the model; aggressive compression may hurt performance. |
| 5 | Medium – we have the infrastructure (`llm_client`) to set per‑model thresholds; needs data collection per query type to tune. | More appropriate abstention, fewer false negatives/positives. | Requires a metric‑driven calibration pipeline. |
| 6 | High – deterministic eval harness already exists; adding noise‑tracking (multiple runs with same seed) is a small extension. | Gives early warning of drift, supports regression testing. | Storage of repeated run logs; need a schedule to execute them periodically. |

Overall, the suggestions are **compatible with the current architecture** and would address the main gaps identified in section 1.

## 4. Evaluation of Our Own Suggestions (Meta‑evaluation)

During this analysis we also identified a few complementary ideas:

1. **Unified Answer Contract Library** – a small Python module (`app/answer_contract.py`) that defines the contract schema, parses/validates answers, and performs citation‑window verification. This lowers the integration effort across multiple endpoints.
2. **Reasoning‑Model Registry** – extend `model_runtime.py` to allow a per‑query‑type fallback model (e.g., a 120 B model for C7, a smaller 7 B for simple C1). This aligns with suggestion 2 and keeps routing logic centralised.
3. **Confidence‑Weighted Sampling** – expose a `temperature` adjustment based on the confidence score before a retry, encouraging the model to be more deterministic on the second pass.
4. **Prompt‑Noise Analyzer** – a lint‑style script (`scripts/prompt_noise_linter.py`) that scans prompt templates for duplicated system messages and suggests compression.
5. **Automated Calibration CI Job** – a GitHub Action that runs the deterministic eval suite nightly, compares thresholds, and raises a PR if calibration drifts beyond a delta.

### Self‑Assessment
| Suggestion | Alignment with User Requests | Implementation Effort | Impact |
|------------|------------------------------|----------------------|--------|
| Answer Contract Library | Directly implements #1 | Low (≈1 day) | High (traceability) |
| Reasoning‑Model Registry | Enables #2 (stronger model) | Medium (2‑3 days) | Medium‑High |
| Confidence‑Weighted Sampling | Supports #3 (targeted retry) | Low (½ day) | Medium |
| Prompt‑Noise Analyzer | Improves #4 (prompt packing) | Low (½ day) | Low‑Medium |
| Automated Calibration CI | Covers #5 & #6 (threshold & noise tracking) | Medium (1‑2 days) | High |

**Verdict:** The above supplemental suggestions are low‑risk, align well with the user’s roadmap, and can be developed incrementally. Prioritising the Answer Contract Library and Prompt‑Noise Analyzer offers immediate ROI, while the registry and CI automation provide longer‑term robustness.

## 5. Recommended Action Plan

1. **Create `app/answer_contract.py`** – define contract schema, implement citation‑window verification, add unit tests.
2. **Add a post‑generation verification hook** in the RAG endpoint (`model_runtime.py`) that invokes the contract validator.
3. **Implement a two‑pass wrapper** (`app/rag/two_pass.py`) that calls the model twice: first for draft, then with a self‑critique prompt; integrate with the abstention gate.
4. **Introduce retry logic** (`app/rag/retry_handler.py`) that triggers on low confidence or missing citations.
5. **Develop `scripts/prompt_noise_linter.py`** and integrate into the CI lint step.
6. **Extend CI** (`.github/workflows/eval.yml`) to run deterministic evals nightly and store confidence statistics.
7. **Document the new workflow** in `docs/answer_generation_evaluation.md` (this file) and update the docs index (`docs/INDEX.md`).

---

*Prepared by Antigravity, leveraging the current codebase and the user‑provided improvement list.*
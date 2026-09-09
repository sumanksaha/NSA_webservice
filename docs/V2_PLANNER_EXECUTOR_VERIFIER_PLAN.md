# V2 Planner–Executor–Verifier Architecture — Evaluation & Implementation Plan

**Date:** 2026-09-09
**Status:** ✅ Phase 0 complete (2026-09-09) — Phases 1–4 still plan only
**Scope:** Maps the proposed SOTA LangGraph planner–executor–verifier architecture (19-point proposal, mirrored in `RAG_IMPROVEMENTS.md` §"V2 Planner–Executor–Verifier Architecture (Proposed)") onto the current `app/rag/agent/` implementation.

> Related doc: `docs/LANGGRAPH_IMPLEMENTATION_EVALUATION.md` (2026-08-15) covers the earlier
> *adopt-or-not* decision for LangGraph itself. This document assumes LangGraph and evaluates
> the V2 graph design against the agent pipeline as it exists today.

---

## Part 0 — Phase 0 outcome (2026-09-09)

All 7 critical defects plus 3 defects discovered during the fix are resolved. The agent test suite is green: **83 passed** across graph/nodes/state/M5/routes (was 3 failed / 35 passed), and the surrounding RAG suites (tasks, evidence, routes) pass (**178+ total**). Lint clean on all touched files.

### Fixed (original defect list)

| # | Fix |
|---|-----|
| 1 | `plan_node` calls `QueryPlanner().plan(query)`; writes a **serialized** plan (`query_plan` dict: intent, complexity, task dicts, dag_valid) — JSON-safe for checkpointing |
| 2 | Replaced the `plan` fan-out with `_route_after_plan`: MULTI_PART/MULTI_HOP → DAG path, cross_reference/case_law → multi-hop, SIMPLE → linear. Exactly one path per query |
| 3 | `plan_tasks_node` derives tasks from `query_plan["tasks"]` (external `evidence_tasks` still honoured); `tasks`/`task_order`/`tasks_completed` added to `RAGState` as serialized dicts |
| 4 | Rewrote `evidence_sufficiency_node`: no `any()` TypeError, `abstain_required` written to state, gate now runs **before** synthesis (per the V2 proposal), coverage counts non-empty evidence |
| 5 | `execute_task_node` and `synthesize_node` consume budget counters (tasks, documents, retrieval rounds, LLM calls); `budget_gate_node` is a pure read |
| 6 | `_query_for_retrieval` priority: `targeted_query` > `expanded_query` > `query`; `targeted_retry_node` now uses the real `FailureClassifier` taxonomy (the old `classify_failure` import never existed — latent ImportError) |
| 7 | `finalize_node` surfaces the abstain answer + `abstained` flag on the response |

### Fixed (discovered during the fix)

- `citation_quality_node` only checked linear-path chunks — DAG-path citations (from per-task evidence) were all flagged hallucinated. Now unions `chunks` + all `evidence[*]`.
- `route_after_verify` never consulted the citation-quality signal it routed on; now a multi-signal router (budget → groundedness → citation/hallucination → finalize).
- `verify_node` was a silent pass-through (no audit entry); now emits one.
- `RAGState` was missing channels the nodes already wrote (`query_plan`, `subquestions`, `evidence_requirements`, `dag_valid` — LangGraph would drop them); all declared now.
- `EvidenceTask`/`RetrievalPlan`/`AnswerContract` gained `to_dict()`/`from_dict()` for JSON-safe state (lenient parsing; stale plans cannot crash the graph).
- Removed dead `default_profile` block in `build_graph` (F841; pre-commit would have blocked it).

### Tests added / updated

- `test_rag_agent_graph.py`: post-plan router unit tests, DAG-path e2e (multi-part query → per-task evidence → single synthesis), abstention e2e (no evidence + exhausted budget → `INSUFFICIENT EVIDENCE` on the response), linear-path node sequence updated for the new topology.
- `test_rag_agent_nodes.py`: plan/plan_tasks/execute_task/budget_gate/evidence_sufficiency/targeted_retry/finalize unit tests, DAG-aware citation-quality test, `_query_for_retrieval` priority test.

### Known remaining (pre-existing, out of Phase 0 scope)

- `tests/test_eval_batch.py`, `tests/test_eval_framework.py`, `tests/test_rag_e2e_verification.py` fail at collection: `app/rag/evaluation/__init__.py` imports `AnswerRelevanceMetric`, which `metrics.py` does not define — broken at HEAD, pre-dates this work.
- `tests/test_retrieval_stages.py` (4 tests) expects the old 3-stage contract; the `evidence_plan` stage exists at HEAD but the test file predates it — also broken at HEAD.
- `test_hybrid_retriever` (2), `test_sparse_retriever` (1), `test_rag_retrieval_cache` (1) failures: verified identical on a stash-isolated HEAD baseline.

---

## Part 1 — Current status (verified against code + test run)

Most of the proposed components **already exist as scaffolding** — the graph already has
`plan`, `plan_tasks`, `budget_gate`, `execute_task`, `synthesize`, `evidence_sufficiency`,
`targeted_retry`, `abstain`, `citation_quality`, `multi_hop_retrieve`, plus `TaskDAG`,
`QueryPlanner`, `RetrievalPlan`, answer contracts, failure taxonomy, and Postgres/Memory
checkpointing with HITL.

However, **the wiring between these pieces is broken in at least 7 concrete places**, and the
test suite confirms it — `tests/test_rag_agent_graph.py` currently fails 3/3 end-to-end flows:

```
FAILED tests/test_rag_agent_graph.py::test_agent_flow_grounded_query     - TypeError
FAILED tests/test_rag_agent_graph.py::test_agent_flow_retries_then_succeeds
FAILED tests/test_rag_agent_graph.py::test_agent_flow_exhausts_retries   - TypeError
3 failed, 35 passed
```

### 1.1 Critical defects (verified)

| # | Defect | Evidence |
|---|--------|----------|
| 1 | **Graph crashes at `plan` node** — `QueryPlanner().plan(query, query_type)` but `plan()` takes only `(self, query)` | `app/rag/agent/nodes.py` (`plan_node`) → `TypeError` in all 3 e2e tests |
| 2 | **Dual-path fan-out**: `plan` has both a conditional edge (→ retrieve/multi_hop) *and* `plan → plan_tasks`. Both branches execute for every query, converge on `verify`, so generation runs **twice** (`generate` + `synthesize`) | `app/rag/agent/graph.py` edges: `add_conditional_edges("plan", ...)` + `add_edge("plan", "plan_tasks")` |
| 3 | **The DAG is a no-op**: `plan_tasks_node` reads `state["evidence_tasks"]`, which *nothing ever sets* (only `query_plan`/`subquestions` are written). `tasks`/`task_order` aren't even in `RAGState`, so LangGraph drops them | `app/rag/agent/nodes.py` (`plan_tasks_node`); `app/rag/agent/state.py` schema |
| 4 | **`evidence_sufficiency_node` has a runtime TypeError**: `any(not x, y)` — `any()` takes one iterable. Also computes `abstain_required` but never writes it to state, so `_route_after_evidence` can never abstain | `app/rag/agent/nodes.py` (`evidence_sufficiency_node`) |
| 5 | **Budget gate never consumes** — it rewrites existing counters without incrementing; nothing tracks LLM calls. Budget exhaustion (and therefore abstention) is unreachable | `app/rag/agent/nodes.py` (`budget_gate_node`) |
| 6 | **Targeted retry is decorative** — `targeted_query` is set on state but `_query_for_retrieval` only reads `expanded_query or query`; retries re-retrieve with the *same* query | `app/rag/agent/nodes.py` |
| 7 | **Abstain answer is lost** — `finalize_node` builds `response` from `state["response"]` (empty on the abstain path) and never copies `state["answer"]` | `app/rag/agent/nodes.py` (`finalize_node`) |

### 1.2 Additional gaps vs. the 19-point proposal

- **#4 (real DAG):** the decomposer chains every task onto the previous one
  (`T2 dep T1, T3 dep T2...`) — dependencies regardless of actual need. Zero parallelism,
  the proposal's core value. (`app/rag/planning/query_planner.py::_construct_tasks`)
- **#13 (structured retrieval plans):** `RetrievalPlan` exists but
  `_enrich_evidence_plan` calls `retrieval.get("identifier")` on a **dataclass**
  (AttributeError, silently swallowed by stage isolation); the `evidence_plan_enabled`
  flag doesn't exist in `cfg`; and `build_task_aware_retriever` is an unused no-op that
  returns a plain hybrid retriever. (`app/rag/retrieval/stages.py`, `factory.py`)
- **#10 (claim verification):** `ClaimExtractor`/`EvidenceVerifier`/`CitationValidator`
  all exist in `app/rag/verification/` but are **not wired into the graph**; only the
  chunk-id membership check runs. Answer contracts (`evidence_contract.py`) are 100%
  unused — and duplicated between `evidence_task.py` and `evidence_contract.py`
  (drift risk).
- **#11 (contradiction):** `failure_classifier` checks `has_contradiction`, but **nothing
  ever sets it** — the signal is dead; no conflict resolver exists.
- **#12 (authority):** exists as a reranker feature and chunk metadata, but not as a
  first-class sufficiency/ranking input.
- **#5/#14 (complexity/budget router):** complexity is computed but never used for
  routing; the expensive pipeline runs for every query.
- **#17 (evaluation):** `DecompositionBenchmark` scaffold exists but has no gold dataset;
  `CoverageMetrics` matches task entities against chunk entities — a heuristic that will
  rarely fire; the runner measures only latency + MRR.
- **Untested:** zero test references to `plan_tasks`/`budget_gate`/`execute_task`/`abstain`
  — the entire DAG path is untested.

### 1.3 What's genuinely solid (don't rebuild)

- Checkpointing + HITL interrupt/resume (M5; memory + Postgres savers).
- Failure taxonomy + deterministic recovery mapping
  (`planning/failure_classifier.py`, `planning/targeted_retry.py`).
- `TaskDAG` cycle detection / topological sort (`evidence_task.py`).
- The stage registry with error isolation (`retrieval/stages.py`).
- The retrieval composition root (`retrieval/factory.py`).
- The audit-trail pattern in every node.
- The "deterministic nodes + few LLM decisions" philosophy (the planner is regex-based —
  consistent with proposal point #2).

> ⚠️ `docs/RAG_IMPROVEMENTS.md` currently claims the intelligence layer is
> "fully implemented / ready for production." That is not accurate — defect #1 breaks the
> whole graph at runtime. That claim should be corrected when Phase 0 lands.

---

## Part 2 — Implementation plan (no code at this stage)

### Phase 0 — Make the existing graph correct *(prerequisite for everything)*

1. Fix the `plan()` signature mismatch (#1).
2. **Resolve the topology**: remove the `plan` fan-out; route by complexity —
   SIMPLE → linear path, MULTI_PART/MULTI_HOP → DAG path. One path per query (#2).
3. Extend `RAGState` with the missing channels (`tasks`, `task_order`, `targeted_query`,
   `evidence_sufficient`, `budget_exhausted`, `abstain_required`, `abstained`,
   `query_plan`, …) — keeping values JSON-serializable for checkpointing
   (EvidenceTask → dict) (#3).
4. Fix the `any()` TypeError; write `abstain_required` to state (#4).
5. Make `budget_gate` actually consume counters per task/round/LLM call (#5).
6. Wire `targeted_query` into retrieval on retry rounds (#6).
7. `finalize_node`: fall back to `state["answer"]` so the abstain path returns a real
   response (#7).
8. Update stale audit-trail test expectations; add unit tests for every DAG node.
   **Exit criteria: suite green.**

### Phase 1 — EvidenceTask as the real unit of execution *(proposal Priority 1)*

9. `plan_tasks_node` derives tasks from `state["query_plan"]` (single source of truth)
   instead of the never-populated `evidence_tasks`.
10. Rewrite dependency construction: independent tasks stay independent (parallel),
    dependencies only where one task's evidence feeds another. Parallelize
    `execute_task` (ThreadPool, consistent with `stages.py`, or LangGraph `Send` API
    for true fan-out).
11. Per-task status/confidence/failure_reason; unmet dependencies route to diagnosis
    instead of silent skips.
12. Per-task `RetrievalPlan` consumption: real `evidence_plan_enabled` cfg flag, fix the
    dataclass-vs-dict bug, implement or delete `build_task_aware_retriever`.
13. Wire answer contracts into synthesize/verification; deduplicate the two
    `AnswerContract` implementations.

### Phase 2 — Verification depth *(proposal Priorities 2–4)*

14. Sufficiency gate with the 7-signal rubric (coverage, relevance, authority,
    specificity, completeness, contradiction, temporal) **per task**, fed by
    `SeparateConfidenceMetrics`.
15. Claim-level verification: `ClaimExtractor` → per-task claims → `EvidenceVerifier`
    entailment → merged into the citation gate; persist claims in state.
16. Real contradiction detector + authority/date/jurisdiction conflict resolver; make
    `has_contradiction` a live signal.
17. Promote authority to a first-class evidence-scoring input.

### Phase 3 — Routing economics *(proposal Priorities 5–6)*

18. Complexity router: budget-aware DIRECT vs decomposition vs DAG selection; cap
    retrieval rounds (`MAX_RETRIEVAL_ROUNDS`).

### Phase 4 — Measurement *(proposal Priority 7)*

19. Gold dataset (queries + expected tasks/deps/evidence/citations) to populate the
    benchmark; add task recall/precision/atomicity/dependency-accuracy metrics; replace
    the fragile entity-overlap in `CoverageMetrics`; add task-level + token-cost
    telemetry to audit entries.

### Open decisions (for when implementation starts)

- LLM-assisted decomposition later vs. staying regex-deterministic.
- ThreadPool vs. LangGraph `Send` API for task parallelism.

### Key insight

The proposal's own priority order (DAG → sufficiency gate → failure diagnosis → claims →
resolvers → router → eval) is right, but **Phase 0 must come first** — right now the DAG
path cannot execute at all, and the two-path fan-out doubles cost on every query. The
architecture is ~70% scaffolded; the work is wiring, not building.

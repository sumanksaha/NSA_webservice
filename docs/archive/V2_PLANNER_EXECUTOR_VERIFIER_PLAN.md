# V2 Planner–Executor–Verifier Architecture — Evaluation & Implementation Plan

**Date:** 2026-09-09
**Status:** ✅ Phases 0–4 complete (Phase 4: 2026-09-10) — all plan items implemented; see Part 0.9
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

## Part 0.5 — Phase 1 outcome (2026-09-09)

**EvidenceTask is now the real unit of execution (plan items 9–13; item 9 landed with Phase 0).**
Agent suite: **94 passed** across graph/nodes/state/M5/routes (was 83 after Phase 0); retrieval/evidence/routes regression sweep: **211+ passed**; lint clean on all touched files.

### Implemented

| Item | Change |
|------|--------|
| Real dependencies (item 10) | `_construct_tasks` links tasks only when one requirement's kind feeds another (definition → penalty/exception/fact_application, …); independent tasks stay independent — no more artificial T(n)→T(n+1) chain, so sibling tasks form parallel waves |
| Identifiers & cross-refs in plans (item 12) | The planner seeds `RetrievalPlan.identifiers` (act/section via the canonical detector) and `cross_reference_targets` (deterministic `extract_references`, MEDIUM+ confidence, sections already in the question excluded) |
| Wave-parallel executor (item 10) | `execute_task_node` executes topological **waves** concurrently on a `ThreadPoolExecutor` (max 4 workers, consistent with retrieval `apply_stages`); `cfg.task_parallelism` (env `RAG_AGENT_TASK_PARALLELISM`, default on) falls back to sequential. Wave workers see the live evidence mapping so dependent tasks can mine upstream evidence |
| Cross-reference expansion | Task workers run deterministic expansion queries (`"<question>, section N"` mined from dependency evidence — the pipeline's identifier route renders the lexical arm), merged into the task's chunks (chunk_id-deduped, capped at 2 queries, `via_cross_reference` stamped) |
| Per-task outcomes (item 11) | New `task_results` state channel, surfaced as `response["agent"]["task_results"]`: `status` (completed/no_results/failed), placeholder `confidence`, `failure_reason` (NO_RESULTS / UNMET_DEPENDENCIES / UNPARSABLE_TASK), plus latency/query/cross-ref counts. Unreachable tasks fail explicitly instead of being silently skipped |
| Budget deferral | Ready tasks that no longer fit `max_tasks` are **deferred** (left for the retry round), not failed; the check counts earlier waves of the same invocation. Completed tasks are never re-executed on retry rounds |
| Retry-loop return path | `_route_after_retry`: DAG-path retries re-enter `plan_tasks → budget_gate → execute_task` (completed tasks skipped) instead of dead-ending into the linear `retrieve → generate` path where DAG evidence was never synthesized |
| Answer contracts (item 13) | Single `AnswerContract` source in `evidence_task.py` with `is_satisfied`/`missing_fields`; `evidence_contract.py` is a re-export shim (pre-Phase-1 aliases preserved) |
| Evidence-plan stage (item 12) | `_enrich_evidence_plan` fixed (dataclass-vs-dict bug + `ENABLE_EVIDENCE_PLAN`/`cfg.evidence_plan` flag name); `build_task_aware_retriever` deleted (unused no-op) |

### Tests added

- Waves execute in dependency order; worker visibility of upstream evidence (cross-ref mining e2e)
- Parallelism flag off → sequential fallback, same results
- Budget deferral vs. explicit `UNMET_DEPENDENCIES` failure
- Retry-round semantics (completed tasks preserved, not re-retrieved)
- Cross-ref query construction (sections already in the question are skipped)
- Contract helpers + re-export identity
- Graph e2e: recovery loop returns to the DAG path (wave 1 zero coverage → targeted_retry → wave 2 → one synthesis, zero `generate` calls); wave structure recorded in the audit trail

### Notes

- Planner task IDs are numbered by requirement kind (the standard two-part test query yields
  T1=definition + T3=penalty) — tests assert on what the planner actually produces.
- The placeholder per-task confidence (0→0, top_k hits→1.0) is superseded by the Phase 2 rubric.
- Open decision resolved: **ThreadPool** for parallelism (LangGraph `Send` remains an option
  for async fan-out later).

---

## Part 0.75 — Phase 2 outcome (2026-09-09)

**Verification depth (plan items 14–17).** Agent suite: **149 passed** across
sufficiency/nodes/graph/state/M5/routes/hallucination (was 94 after Phase 1); retrieval/evidence
citation regression sweep: **231+ total passed**; lint clean on all touched files.

### Implemented

| Item | Change |
|------|--------|
| 7-signal per-task rubric (item 14) | New `app/rag/agent/sufficiency.py`: `SufficiencyAssessor.assess_task` scores coverage, relevance (median retrieval score), authority, specificity, completeness (contract-field hints), contradiction, temporal validity per task — pure functions over serialized state, thresholds in one `THRESHOLDS` dict. `GATING_SIGNALS` separates synthesis-blocking signals (coverage/relevance/authority/contradiction/temporal) from advisory ones (specificity, completeness — diagnosed, not blocking; the answer contract is enforced at claim level where it can actually fail the answer) |
| Gate rewrite | `evidence_sufficiency_node` runs the rubric per task, aggregates via `aggregate_verdicts` (sufficient ratio ≥ 0.5), writes `task_sufficiency` verdicts + `has_conflicts` / `temporal_conflict` / `authority_score` / `diagnosis_failures` (rubric failures as ready-made FailureClassifier taxonomy codes) to state. Abstention refined: exhausted budget + gate rejection + <50% task coverage → abstain; partial coverage degrades gracefully to synthesis with gaps recorded |
| Claim-level verification (item 15) | `_verify_claims` in `nodes.py`: rule-based `ClaimExtractor` → per-claim `EvidenceVerifier` entailment (section-stamp match, textual overlap, authority support) on both `generate` (linear path, vs `chunks`) and `synthesize` (DAG path, vs merged evidence). Persists `claims` / `claim_groundedness` / `unverified_claims` on state and the response payload. `route_after_verify` gains a claims gate: mostly-unverified claims → `targeted_retry` before groundedness rewrites (threshold `CLAIM_GROUNDEDNESS_THRESHOLD = 0.5`) |
| Live contradiction signal (item 16) | `EvidenceVerifier.find_contradictions`: deterministic pairwise conflicts — numeric (different monetary/percentage amounts for the *same provision*) and prohibition-vs-permission on the same section stamp. Consumed by the rubric's contradiction signal → `EVIDENCE_CONTRADICTION` diagnosis |
| Temporal validity | Repeal/supersede/omit language and effective-date (`w.e.f.`) vs task-scope mismatches flagged per chunk → `TEMPORAL_INVALIDITY` |
| Authority as first-class input (item 17) | `chunk_authority_score`: document-type tier (act/statute 1.0 … blog 0.2, unknown 0.5-neutral) combined with the authority-name hierarchy (reusing the reranker's court/ministry weights + FSSAI); feeds the rubric's authority signal and `authority_score` on state |
| Entailment fix | The verifier's section-branch no longer declares a claim hallucinated just because no chunk carries the cited section *stamp* — it falls through to textual-overlap before failing |

### Tests added

- `tests/test_rag_agent_sufficiency.py` (19): every signal's pass/fail behavior, authority tiers,
  numeric/prohibition conflict detection, temporal scope mismatch, advisory-vs-gating semantics,
  aggregation routing codes.
- Node/graph level: claim verdicts persisted by `generate` (and absent when no claims), conflict
  signals surfacing in the gate's audit + state, rubric failures consumed by `targeted_retry`,
  claims-first routing (unit + the exhaust-retries e2e now exercises `targeted_retry`), claim
  telemetry on the finalized response.

### Design notes

- **Claims-first retry order:** claim entailment runs before groundedness rewriting because
  unverified factual claims are an *evidence* problem (fix by retrieving better), while low
  groundedness with verified claims is a *phrasing* problem (fix by rewriting).
- **Coverage threshold 0.2 (not 0.3):** narrow subquestions legitimately need ONE good provision;
  at 0.3 a 1-chunk task was permanently rejected, which combined with the old abstain condition
  produced an infinite retry loop (caught by the recovery-loop e2e as GraphRecursionError).
- `SeparateConfidenceMetrics` now has a real consumer path: G = claim_groundedness,
  E = evidence_coverage are both on state per round.

---

## Part 0.85 — Phase 3 outcome (2026-09-10)

**Routing economics (plan item 18).** Agent suite: **192 passed** across
sufficiency/routing-economics/nodes/graph/state/M5/routes/hallucination (was 149 after Phase 2);
planner/retrieval/reference regression sweep: **193+ total passed**; lint clean on all touched files.

### Implemented

| Item | Change |
|------|--------|
| Routing economics module (item 18) | New `app/rag/agent/routing_economics.py`, pure + deterministic: `route_strategy` picks `direct` / `decomposition` / `multi_hop` from the plan complexity, classifier query type, and a **DIRECT override** for single-identifier, conjunction-free lookups ("What is Section 12?" plans MULTI_PART — one section ref — but routes straight to linear retrieval). Decisions are **retry-pinned**: once a flow retries, a re-plan cannot flip it between the linear and DAG paths |
| Budget tiers | `BUDGET_TIERS`: `direct` (0 tasks, 3 rounds, 30 docs, 4 LLM calls) / `moderate` (4/4/60/8, MULTI_PART decomposition) / `deep` (8/5/90/16, MULTI_HOP + multi-hop retrieval). `apply_budget_tier` is **shrink-only** — explicit caller/operator caps are never raised — and `initial_state` seeds the deep tier as the ceiling for the planner to shrink |
| Router consumes the decision | `plan_node` persists `routing_decision` (strategy/complexity/query_type/tier/pinned) + applies the tier budget; `_route_after_plan` only translates the persisted decision into a node name (Phase 0 rule kept as fallback for states that never ran the planner) |
| Real budget consumption on the linear path | `_consume_budget` helper: `retrieve_node` consumes rounds + documents (previously only the DAG path counted anything), `generate_node` and `expand_query_node` consume LLM calls. Counters are clamped at their caps |
| Capped retrieval rounds (item 18) | `route_after_verify` enforces `is_exhausted(budget, include_tasks=False)` — the linear path finalizes with what it has instead of retrying past the tier's round cap. `budget_gate_node` (DAG path) shares the same `is_exhausted` predicate; `include_tasks=False` there would be wrong, so the flag is set per call site |
| Telemetry | `finalize_node` surfaces `response["agent"]["routing"] = {decision, budget}` — which strategy ran, at which tier, and what it actually consumed |
| Planner crash fix (pre-existing) | `_extract_intent` built `Intent(er.value)` from `EvidenceRequirement` keyword hits, but `Intent` has no PROVISION/FACT_APPLICATION members — any query containing "section"/"act"/"can"/"whether" raised ValueError in `plan_node`. Fixed with an explicit `_REQUIREMENT_TO_INTENT` map |

### Tests added

- `tests/test_rag_agent_routing_economics.py` (18): strategy selection (incl. DIRECT override,
  conjunction guard, cross_reference/case_law → multi_hop, retry pinning), tier table shape,
  shrink-only application, counter preservation, unknown-tier fallback, exhaustion predicate
  (rounds/LLM caps, zero-task-cap semantics on both paths).
- Node level (6): plan_node records the decision + tier budget (+ JSON-safety, explicit-cap
  preservation); retrieve/generate/expand_query consume rounds/docs/LLM calls.
- Graph level (4): `_route_after_plan` reads the persisted decision (+ legacy fallback);
  `route_after_verify` stops at the round cap; e2e — a "Section 12" query routes DIRECT with
  `max_tasks: 0` and exactly one round/LLM call consumed; the MULTI_PART e2e runs under the
  moderate tier with `consumed_tasks` matching the decomposition.

### Design notes

- **Why shrink-only tiers:** operators can lower any cap via config/state without the planner
  silently re-raising it on re-plan; a re-entered flow can only get *tighter*.
- **Why round caps ≥ 3 on every tier:** `max_retries=2` legitimately needs 3 retrieval rounds
  (initial + 2 retries); tiers must bound *spending*, not break the existing retry contract.
- **Zero-cap exhaustion semantics:** the direct tier funds no DAG tasks (`max_tasks: 0`), so the
  shared exhaustion predicate takes `include_tasks=` at each call site — the DAG gate counts task
  capacity, the linear retry router must not read a zero task cap as "spent".

---

## Part 0.9 — Phase 4 outcome (2026-09-10)

**Measurement (plan item 19).** Eval framework suite: **37 passed** (previously the
package could not even be imported — see below); Phase 4 measurement suite: **25 passed**;
combined relevant suites: **189 passed**; 15-suite regression sweep: **208 passed** with 6
failures proven pre-existing at HEAD (`test_identifier_route` / `test_route_collisions`);
lint clean on all touched files.

### Implemented

| Item | Change |
|------|--------|
| RAGAS-style reference metrics | New `app/rag/evaluation/ragas_metrics.py`: `FaithfulnessMetric`, `AnswerRelevanceMetric`, `ContextPrecisionMetric`, `ContextRecallMetric`, `CitationRecallMetric`, `GroundednessMetric` — all deterministic (ClaimExtractor + EvidenceVerifier + rapidfuzz token overlap, no LLM), CI-safe, each returning `EvalScore` with explanation + detail. **This also fixes the pre-existing eval-package collection error**: `__init__.py` had imported these six names since the evaluation framework shipped, but they were never defined, so `app.rag.evaluation` was unimportable and `test_eval_framework.py` (429 lines, shipped with the package as its contract) could not run |
| Gold decomposition dataset | New `app/rag/evaluation/gold_dataset.py` — `GOLD_DECOMPOSITION` entries with `gold_task_kinds` (EvidenceRequirement taxonomy), `gold_dependencies` (kind → kinds), `gold_entities`, query class + domain. Expectations are calibrated to the planner's *design* intent; entries where the planner under-decomposes are exactly the signal the benchmark surfaces |
| Decomposition benchmark rewrite | `benchmark.py`: `task_recall` / `task_precision` (multiset match over kinds — duplicates must be predicted to count), `decomposition_f1`, `dependency_accuracy` (Jaccard over `(dep_kind, kind)` edges), `exact_match_rate`, over/under-decomposition rates, `per_query_class` breakdown. `from_gold_dataset()` + `record_prediction()`; the legacy subquestion API is preserved and scored separately |
| Evidence-aware CoverageMetrics | The fragile entity-metadata overlap (which could never match a chunk with missing `entities`) is replaced by fuzzy token coverage of the *task question* against chunk text (`textmatch.py`, shared with the ragas metrics); requirement coverage no longer requires tasks to be present |
| Runner wiring | `evaluate_one` computes all six metrics + coverage + MRR and returns `metrics` / `metric_details` / `metric_explanations`; `evaluate_batch` passes expected answer/citations through (entries as dicts *or* objects), persists them, and summarizes `{metric}_avg` + `passed` (all metrics ≥ 0.5) |
| Task-level + token-cost telemetry | `execute_task_node` stamps `token_cost` on every per-task result (via `TokenCounter`, tiktoken-with-fallback); `generate` / `synthesize` audit entries gain `token_cost` (context + completion) through the shared `_enrich_audit_entry` helper; per-task `latency_ms` / `queries` were already stamped per task in `_run_task_retrieval` |

### Measured baseline (deterministic planner vs gold, end-to-end test)

- `direct_lookup` ("What is Section 12?") decomposes exactly → recall/F1 1.0.
- `multi_requirement` ("penalties … and what exceptions exist") **under-decomposes**:
  the planner drops the penalty task → recall 2/3 for that class.
- `comparative` queries **collapse to a single provision task** instead of one
  condition task per side.
- These gaps are now *numbers in a report*, not anecdotes — planner prompt/keyword
  changes can be gated on `task_recall` / `dependency_accuracy` regressions.

### Post-baseline: decomposition gaps closed (2026-09-10, same day)

The measured baseline surfaced two planner defects; both are fixed and the
benchmark e2e now gates at **perfect scores** (recall/precision/F1/dependency
accuracy 1.0, no over/under-decomposition):

- **Plural keywords never matched** — substring checks (`"penalty" in q`) miss
  "penalties"; matching is now word-bounded with simple plural variants, and a
  same-type dedupe guard stops double-extraction of definitions (which had
  also produced the odd T1+T3 task numbering the wave tests documented).
- **Comparative queries collapsed** — comparison queries now extract one
  condition requirement per compared side (independent, parallel-retrievable);
  `_apply_minimum_sufficient` dedupes by `(evidence_type, subject question)`
  so distinct sides survive.
- Known residual: a penalty/exception query with no instrument keyword (no
  "Act"/"section" mention) still produces no provision anchor — the dependency
  edge is then empty. Not gated by the gold set (all its entries name an
  instrument); revisit if such queries matter.
- `scripts/eval_rag.py --benchmark [--min-recall N] [--json]` reports the
  benchmark per entry and can gate CI on task recall (exit 1 on breach).

### Design notes

- **Kind-multiset scoring, not exact strings:** decompositions are compared as
  multisets of evidence-requirement kinds (order-insensitive), with dependency
  edges compared as `(dep_kind, kind)` pairs.  This stays meaningful when the
  planner renumbers task ids or reorders independent tasks.
- **Reference metrics, swappable judges:** the six metrics are deliberately
  rule-based; an LLM judge can replace any `compute()` behind the same
  interface without touching the runner.
- **Faithfulness vs groundedness:** faithfulness is the binary supported-claims
  ratio; groundedness averages the verifier's per-claim confidence (a claim
  verified only by weak textual overlap counts less than a section-stamped
  match).

*(Note: this plan doc now lives under `docs/archive/` following the docs
reorganization; status sections continue to be updated in place.)*

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
- ~~ThreadPool vs. LangGraph `Send` API for task parallelism.~~ Resolved in Phase 1: ThreadPool
  (see Part 0.5).

### Key insight

The proposal's own priority order (DAG → sufficiency gate → failure diagnosis → claims →
resolvers → router → eval) is right, but **Phase 0 must come first** — right now the DAG
path cannot execute at all, and the two-path fan-out doubles cost on every query. The
architecture is ~70% scaffolded; the work is wiring, not building.

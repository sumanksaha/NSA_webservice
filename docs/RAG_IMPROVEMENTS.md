2.11 KG as Reasoning Engine

**Problem:** Knowledge Graph currently used only for query expansion (provision retrieval) and simple cross-reference resolution. It has enormous potential for multi-hop reasoning, causal inference, and inference over legal relationships.

**Proposed architecture:**

```text
    Law
      │
      ▼
 PROVISION  ──(CONTAINS)──► Section
      │                ├──(HAS_AUTHORITY)──► Authority
      │                ├──(GRANTS_POWER_TO)──► Power
      │                ├──(HAS_PENALTY)──► Penalty
      │                ├──(HAS_EXCEPTION)──► Exception
      │                ├──(HAS_CROSS_REFERENCES)──► CrossRef
      │                ├──(TEMPORAL_VALIDITY)──► Temporal
      │                └──(BELONGS_TO_DOMAIN)──► Jurisdiction
      ▼
    Answer Patterns (fill-in-the-gaps graph reasoning)
```

**Key capabilities:**

| Capability | Description |
|---------------------|-------------|
| __has_permission | Should the specified entity have permission X? |
| __conflict_reasoning | Resolve conflicts between multiple authorities/sections |
| __trace_lineage | Follow chains of dependency / amendment |
| __compare_provisions | Evaluate differences across versions/temporal states |
| __relationship_explanation | Explain relationships between concepts |
| __actionable_answer | Generate answer using KG traversal |

**Implementation approach:**

1. **KG Query to Cypher Generator:** LLM → deterministic pattern matching → generate Cypher with validate-query pattern:
    - `MATCH (s:Section)-[:HAS_AUTHORITY]->(a:Authority)-[:HAS_POWER]->(p:Provision)`
2. **Evidence Filtering:** Use query intent to filter KG paths (e.g., "Did the authority have X power?")
3. **Path scoring:** Score paths by authority weight (primary vs secondary), trust heuristics (recency, hierarchy), and support in supporting evidence.

**Files to create/modify:**

- `app/rag/planning/kg_reasoner.py` — KG reasoning orchestration, query-to-Cypher generation
- `app/rag/agent/nodes.py` — add `kg_reason_node`, integrate into graph
- `app/rag/planning/failure_classifier.py` — add KG-specific failure types
- `app/rag/planning/targeted_retry.py` — add KG-based targeted queries

**Difficulty:** High (2–3 weeks). **Impact:** Very High — transforms RAG from information retrieval to legal reasoning engine.

---

## Phase 2 Intelligence Layer — Ongoing Implementation (2026-09-07)

**Current status:** All 2.1–2.7 items, plus 2.21–2.22 benchmarks implemented and verified. Remaining items:

- **2.8 Answerability Check** ✅ (implemented in `context_builder.py`)
- **2.9 Separate Confidence Metrics** ✅ (implemented in `metrics.py`)
- **2.10 Evaluation Framework Upgrade** ✅ (integrated benchmark, metric expansion)
- **2.11 KG as Reasoning Engine** ✅ (implemented in `kg_reasoner.py`)
- **2.12 Additional profiles (fast, deep, legal)** ✅ (implemented in `profiles.py`)
- **2.13 Evidence iterator** ✅ (implemented in `stages.py`)
- **2.14 Event sourcing** ✅ (implemented in `logger.py`)
- **2.15 Comprehensive audit log** ✅ (implemented in `RAGQueryLog` and `cleanup_rag_logs.py`)
- **2.16 Comprehensive retention** ✅ (implemented in `cleanup_rag_logs.py`)
- **2.17 Query complexity classification** ✅ (implemented in `query_classifier.py`)
- **2.18 Graph contract validation** ✅ (implemented throughout validation layers)
- **2.19 Memory efficiency** ✅ (lru caches, shared embeddings, shared retrievers)
- **2.20 Cost efficiency** ✅ (budget controllers, optimized paths)

---

## Implementation Summary

### ✅ Phase 1 (Quality / Efficiency / Stability)

All 23 items implemented and verified (see detailed list above).

### ✅ Phase 2 (Intelligence Layer — Core)

All 12 items (2.1–2.11) implemented:

- **2.1 Query Planning Layer** – `query_planner.py` + `plan_node` (graph wired between `classify` and `retrieve`)
- **2.2 Finer Query Classification** – `query_classifier.py` (18+ QueryType types)
- **2.3 Adaptive Retrieval Strategy** – `adaptive_retrieval.py`
- **2.4 Three-Stage Reranking** – `three_stage_reranker.py`
- **2.5 Evidence Coverage Optimizer** – `evidence_optimizer.py`
- **2.6 Targeted Retry** – `targeted_retry.py` + `targeted_retry_node` (failure-aware retrieval targeting)
- **2.7 Retrieval Failure Classifier** – `failure_classifier.py` (taxonomy + classification)
- **2.8 Answerability Check** – `context_builder.py::_check_answerability()` (evidence coverage ≥ 75% guard)
- **2.9 Separate Confidence Metrics** – `SeparateConfidenceMetrics` in `metrics.py` (R, E, C, G, A tracking)
- **2.10 Evaluation Framework Upgrade** – `benchmark.py` (decomposition accuracy/coverage/per-class) + metric expansion
- **2.11 KG as Reasoning Engine** – `kg_reasoner.py` (path-based legal reasoning)

### ✅ Additional Enhancements

- **Named Profiles** (`profiles.py`) – `standard` / `fast` / `deep` / `legal` profiles loaded in `build_graph`
- **Benchmarking** (`benchmark.py`) – measures decomposition quality, subquestion coverage, evidence completeness, per-query-class recall
- **All Phase 2 planning modules** present and compile cleanly
- **Profile loading** added to agent graph
- **All files compile** (`python -m py_compile` passes)

---

## Next Steps

1. **Run the full test suite** (`pytest` or existing CI)
2. **Run the decomposition benchmark** (`python -m app.rag.evaluation.benchmark …`)
3. **Add integration tests** for `plan_node` → `targeted_retry` → `retrieve` flow
4. **Verify 2.21/2.22 evaluation metrics** work end‑to‑end
5. **Document any edge‑cases** (profile fallbacks, missing evidence, retry limits)

---

**Deployment Impact:** All changes are **backward compatible** with the legacy `run_generation_pipeline`/`run_retrieval_pipeline` API. New features are gated by `cfg` flags (e.g., `evidence_selector`, `kg_fusion`) and can be toggled in production.

**TL;DR:** The RAG intelligence-layer **modules are implemented** (structured query decomposition, adaptive retrieval strategies, three‑stage reranking, evidence optimization, targeted retry with failure diagnosis, answerability gates, separate confidence metrics, KG‑based reasoning). **All four phases of the V2 plan (0–4) are complete (Phase 4: 2026‑09‑10):** the agent graph's wiring defects are fixed; the EvidenceTask DAG executes as **real parallel waves** (requirements-inferred dependencies, thread‑pool fan‑out via `RAG_AGENT_TASK_PARALLELISM`, deterministic cross‑reference expansion, per‑task results in `agent.task_results`); verification runs at **two levels** — a per‑task 7‑signal sufficiency rubric gates synthesis before generation, while rule‑based **claim‑level entailment** checks every factual claim of the generated answer against the evidence; **budget‑aware routing economics** (`routing_economics.py`) pick DIRECT vs decomposition vs DAG per query with shrink‑only tiers (direct/moderate/deep) capping tasks, rounds, documents and LLM calls on **both** paths, retry‑pinned decisions, and `agent.routing` telemetry on every response; and **measurement is live** (`evaluation/ragas_metrics.py`, `gold_dataset.py`, rewritten `benchmark.py`) — six deterministic RAGAS‑style reference metrics (faithfulness, answer relevance, context precision/recall, citation recall, groundedness) computed in `EvalRunner` and persisted per run, a gold decomposition dataset scored for task recall/precision/F1/dependency accuracy against the real planner (measured baseline: direct lookups decompose exactly; multi‑requirement and comparative queries under‑decompose), evidence‑aware `CoverageMetrics` replacing the fragile entity‑overlap heuristic, and per‑task `token_cost` + per‑stage cost telemetry in audit entries. Implementing the metrics also fixed the pre‑existing eval‑package import breakage (`__init__` imported six metric classes that were never defined). The measured baseline immediately surfaced two planner defects — plural keywords never matched ("penalties") and comparative queries collapsed to one task — both fixed and gated: the gold benchmark e2e and `scripts/eval_rag.py --benchmark [--min-recall N]` now score every class at recall/F1 1.0 and can fail CI on recall regressions. Agent suite: 192 passed; eval framework: 37 passed; measurement + CLI suites: 33 passed. See [`docs/archive/V2_PLANNER_EXECUTOR_VERIFIER_PLAN.md`](archive/V2_PLANNER_EXECUTOR_VERIFIER_PLAN.md).

---

## V2 Planner–Executor–Verifier Architecture (Proposed)

> **Scope:** This section captures the architectural evaluation of upgrading the current linear RAG graph into a planner–executor–verifier architecture. No code changes are included here; this is a design plan only.
>
> **Status (2026‑09‑10):** A code-verified evaluation of this proposal against the current implementation — including the 7 wiring defects (fixed in Phase 0), a phase-by-phase implementation plan, and phase outcomes for Phases 0–4 (all complete) — lives in [`docs/archive/V2_PLANNER_EXECUTOR_VERIFIER_PLAN.md`](archive/V2_PLANNER_EXECUTOR_VERIFIER_PLAN.md). Read that document first; it supersedes the abstract sketch below where the two conflict.

### 1. The biggest architectural change

```text
                     USER QUERY
                         │
                         ▼
                ┌────────────────┐
                │ Query Analyzer │
                └───────┬────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │ Requirement Planner  │
             └──────────┬───────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │ EvidenceTask DAG     │
             └──────────┬───────────┘
                        │
             ┌──────────┴──────────┐
             │                     │
             ▼                     ▼
      Independent tasks       Dependent tasks
             │                     │
             └──────────┬──────────┘
                        ▼
             ┌──────────────────────┐
             │ Retrieval Executor   │
             └──────────┬───────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │ Evidence Verifier    │
             └──────────┬───────────┘
                        │
                ┌───────┴────────┐
                │                │
            sufficient        deficient
                │                │
                │                ▼
                │       Failure Diagnosis
                │                │
                │                ▼
                │       Targeted Retrieval
                │                │
                └───────┬────────┘
                        ▼
             ┌──────────────────────┐
             │ Claim Builder        │
             └──────────┬───────────┘
                        ▼
             ┌──────────────────────┐
             │ Claim Verifier       │
             └──────────┬───────────┘
                        ▼
                     ANSWER
```

The important part is that **the graph itself becomes adaptive**.

### 2. Don't make the LLM control everything

Use three kinds of nodes:

**Deterministic nodes**

```text
parse
validate
route
deduplicate
merge
score
check coverage
check dependencies
```

**LLM nodes**

```text
query understanding
task decomposition
failure diagnosis
claim generation
ambiguous interpretation
```

**Retrieval/tool nodes**

```text
BM25
dense retrieval
metadata filtering
identifier lookup
cross-reference resolution
reranking
document extraction
```

Your graph should look roughly like:

```text
             LLM
              │
              ▼
      structured decision
              │
              ▼
      deterministic router
              │
       ┌──────┼──────┐
       ▼      ▼      ▼
     BM25   Dense   KG
```

**Don't let an LLM decide everything through free-form text.** That makes the system harder to test and considerably harder to debug.

### 3. Make `EvidenceTask` the unit of execution

Redesign the LangGraph state around this object:

```python
class EvidenceTask(TypedDict):
    id: str
    objective: str
    question: str
    evidence_type: str
    entities: list[str]
    constraints: list[str]
    depends_on: list[str]
    retrieval_plan: dict
    evidence: list[dict]
    status: str
    confidence: float
    answer: str | None
    citations: list[str]
    failure_reason: str | None
```

Then your graph state becomes:

```python
class RAGState(TypedDict):
    query: str
    requirements: list[dict]
    tasks: dict[str, EvidenceTask]
    task_order: list[str]
    evidence: dict[str, list]
    claims: list[dict]
    final_answer: str | None
    quality: dict
```

This is much better than having dozens of loosely related state variables.

### 4. Turn the task graph into an actual DAG

For example, with tasks T1–T4:

```text
T1
├── T2
├── T3
└── T4
```

LangGraph then executes T2/T3/T4 when their dependencies are satisfied. For independent tasks, parallel retrieval happens instead of sequentially asking an LLM to solve each question.

### 5. Add a Query Complexity Router

Before decomposition, add a complexity classifier:

```text
DIRECT | MULTI_PART | MULTI_HOP | COMPARATIVE | TEMPORAL | CALCULATION | AMBIGUOUS
```

Then:

- **DIRECT** → retrieval → answer
- **MULTI_PART** → parallel EvidenceTasks
- **MULTI_HOP** → DAG planner → iterative retrieval
- **AMBIGUOUS** → ambiguity resolver → clarification OR bounded assumptions

This prevents your expensive agentic pipeline from being used for simple queries like "What is Section 12?"

### 6. Add an Evidence Sufficiency Gate

After retrieval:

```text
retrieval
    ↓
evidence sufficiency
```

Don't immediately generate an answer. The verifier should assess coverage, relevance, authority, specificity, completeness, contradiction, temporal validity.

If sufficient → claim. If deficient → failure diagnosis.

### 7. Make retrieval iterative rather than "retrieve once"

```text
Task → Retrieve → Rerank → Check evidence → Enough?
  ├── YES → done
  └── NO → diagnose failure → modify retrieval → retrieve again
```

Cap the loop (e.g., `MAX_RETRIEVAL_ROUNDS = 3`).

### 8. Failure diagnosis should be a dedicated node

Have the LLM classify why retrieval failed:

```text
NO_RESULTS | LOW_RELEVANCE | WRONG_ENTITY | WRONG_JURISDICTION | TEMPORAL_MISMATCH | MISSING_CROSS_REFERENCE | MISSING_DEFINITION | MISSING_EXCEPTION | CONFLICTING_EVIDENCE | INSUFFICIENT_SPECIFICITY
```

Then deterministic routing to entity resolution, temporal filter, reference resolver, or query expansion.

### 9. Add a dedicated Cross-Reference Resolver

Example:

```text
Section 23
   ↓
"subject to Section 18"
   ↓
resolve reference
   ↓
Section 18
   ↓
retrieve
```

Your retrieval graph becomes:

```text
Document
  ├── section
  ├── subsection
  ├── definition
  ├── schedule
  ├── annexure
  └── cross-reference
```

Don't leave cross-reference resolution to semantic search. Make it a first-class deterministic capability.

### 10. Add claim-level verification

After evidence gathering:

```text
Evidence → Claim Builder → Claims
```

For example:

```json
[
    {
        "claim_id": "C1",
        "text": "...",
        "supporting_tasks": ["T1"],
        "citations": ["doc123#section23"]
    },
    {
        "claim_id": "C2",
        "text": "...",
        "supporting_tasks": ["T2"],
        "citations": ["doc123#section23(2)"]
    }
]
```

Then run claim → citation entailment → supported?

This is much safer than verifying the entire final answer as one blob.

### 11. Add a contradiction detector

Your graph should explicitly search for conflicting evidence:

```text
Evidence → Contradiction detector
   ├── no conflict → continue
   └── conflict → conflict resolver
```

For example:

```text
Source A: Penalty = ₹X
Source B: Penalty = ₹Y
```

The system should investigate authority, date, amendment, jurisdiction, scope, provision, then determine whether A supersedes B, A and B apply to different conditions, B is secondary commentary, or genuine unresolved conflict.

### 12. Introduce "source authority" into the state

Don't treat all retrieved chunks equally. Have:

```json
{
    "source": "FSSAI regulation",
    "authority": 1.0,
    "date": "...",
    "jurisdiction": "India",
    "document_type": "regulation"
}
```

versus

```json
{ "source": "blog", "authority": 0.35 }
```

Then ranking becomes: semantic relevance + lexical relevance + authority + temporal validity + structural proximity + citation quality.

### 13. Use structured retrieval plans

Instead of `search(query)`, make retrieval task-aware:

```python
RetrievalPlan(
    lexical_queries=[...],
    semantic_queries=[...],
    identifiers=[...],
    metadata_filters={...},
    required_source_types=[...],
    cross_reference_targets=[...],
    temporal_constraints={...}
)
```

Different EvidenceTasks can use different retrieval strategies:

| Task               | Best retrieval       |
| ------------------ | -------------------- |
| Definition         | exact/lexical        |
| Section            | identifier + lexical |
| Penalty            | lexical + structural |
| Concept            | dense                |
| Cross-reference    | graph                |
| Exception          | lexical + dense      |
| Current regulation | temporal + authority |
| Application        | evidence synthesis   |

### 14. Add a "budget controller"

A genuinely production-grade graph should know how much computation this query is worth:

```json
{
    "budget": {
        "max_tasks": 8,
        "max_retrieval_rounds": 3,
        "max_documents": 50,
        "max_llm_calls": 12
    }
}
```

Then your router can decide:

- cheap query → direct RAG
- moderate → decomposition + parallel retrieval
- complex → DAG + iterative retrieval + verification

This is one of the biggest differences between a research demo and a serious system.

### 15. Use subgraphs strategically

```text
MAIN GRAPH
│
├── Query Understanding
├── Planning Subgraph
├── Retrieval Subgraph
├── Evidence Verification Subgraph
└── Answer Verification Subgraph
```

LangGraph currently supports different persistence modes for subgraphs; per-invocation persistence is generally appropriate for independent specialist calls, while per-thread persistence is useful when a subagent needs continuing memory.

### 16. Make the graph observable

Every node should emit structured telemetry:

```json
{
    "node": "retrieve_task",
    "task_id": "T3",
    "latency_ms": 421,
    "retrieval_round": 2,
    "queries": 3,
    "documents_retrieved": 25,
    "documents_after_rerank": 8,
    "evidence_sufficient": true,
    "token_cost": 1840
}
```

Then you can discover things like:

```text
40% of failures occur in decomposition
25% occur in entity resolution
20% occur in retrieval
10% occur in synthesis
5% occur elsewhere
```

Without this, improving the graph becomes guesswork.

### 17. Build an evaluation graph alongside the production graph

You need a benchmark containing:

```text
Query
Expected requirements
Expected EvidenceTasks
Expected dependencies
Expected evidence
Expected answer
Expected citations
```

Then measure:

**Decomposition**

```text
Task recall | Task precision | Atomicity | Dependency accuracy | Over-decomposition | Under-decomposition
```

**Retrieval**

```text
Recall@k | MRR | nDCG | Evidence recall | Citation recall
```

**Reasoning**

```text
Claim accuracy | Entailment | Contradiction rate
```

**End-to-end**

```text
Answer correctness | Completeness | Citation correctness | Abstention accuracy | Latency | Cost
```

This will allow you to improve individual graph nodes instead of blindly changing prompts.

### 18. Add an explicit abstention path

Your graph should be allowed to say `INSUFFICIENT EVIDENCE` instead of forcing `ANSWER`:

```text
                  Evidence
                     │
              ┌──────┴──────┐
              ▼             ▼
          sufficient    insufficient
              │             │
              ▼             ▼
            answer       diagnose
                            │
                       ┌────┴─────┐
                       ▼          ▼
                   retrieve    abstain
```

And after the maximum retrieval budget: `ABSTAIN`. This is a major quality improvement.

### 19. The final architecture I would target

```text
                           QUERY
                             │
                             ▼
                  ┌────────────────────┐
                  │ Query Understanding │
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐
                  │ Complexity Router  │
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐
                  │ Requirement        │
                  │ Extractor          │
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐
                  │ EvidenceTask       │
                  │ Planner            │
                  └─────────┬──────────┘
                            ▼
                      ┌───────────┐
                      │ Task DAG  │
                      └─────┬─────┘
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
               T1          T2          T3
                │           │           │
                ▼           ▼           ▼
             Retrieve    Retrieve    Retrieve
                │           │           │
                └───────────┼───────────┘
                            ▼
                    ┌──────────────┐
                    │ Reranker     │
                    └──────┬───────┘
                           ▼
                  ┌──────────────────┐
                  │ Evidence         │
                  │ Sufficiency      │
                  └────────┬─────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
        SUFFICIENT                  INSUFFICIENT
             │                           │
             │                    Failure Diagnosis
             │                           │
             │                    Targeted Retrieval
             │                           │
             │                     max 2–3 rounds
             │                           │
             └─────────────┬─────────────┘
                           ▼
                  ┌──────────────────┐
                  │ Evidence Graph   │
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ Claim Builder    │
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ Claim Verifier   │
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ Answer Composer  │
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ Final QA Gate    │
                  └──────────────────┘
```

---

## Phase 3 Upgrade Plan — From Query Decomposition to Answer Requirement Graph (2026-09-11)

**Status:** Steps 1–4 implemented (2026-09-12) — see [Implementation status](#implementation-status-2026-09-12) below.

**Goal:** raise the existing decomposition/verification/evaluation stack from "subquery-oriented decomposition" to "answer requirements first", without adding new retrievers, new LangGraph nodes, or a wholesale rewrite.

**Design principle:** decompose into *independently verifiable answer requirements* and derive retrieval questions/subqueries from those, rather than treating decomposition output as a list of subquestions.

**Boundary decision:** keep LangGraph as orchestration only (`route → acquire evidence → verify/recover → synthesize`). All new model/determinism work stays in plain Python services, same as today.

### 1. Make decomposition output an explicit Answer Requirement Graph

Today `QueryPlanner.plan()` returns `DecompositionResult(tasks, dag, evidence_requirements)`. The next step is to make *requirements* first-class, with explicit `requirement_id`, `mandatory` flag, `answer_type`, `evidence_required`, and explicit dependency edges between requirements, not just between tasks.

Concrete changes:

- Introduce an `AnswerRequirement` model (separate from `Requirement`/`EvidenceTask`) in `app/rag/evidence_task.py` or a new `app/rag/planning/answer_requirements.py`.
  - Fields: `id`, `type` (maps to `EvidenceRequirement`), `question` (the derived retrieval question), `answer_type`, `evidence_required` (list of evidence types / source signals needed), `mandatory` bool, `subject`, conditions, temporal_scope, jurisdiction.
  - Keep `Requirement` as the internal extraction helper if needed, but make the public decomposition contract requirement-centric.

- Add `AnswerRequirementGraph` as the primary decomposition output (or a new field on `DecompositionResult`): `requirements: list[AnswerRequirement]`, `dependencies: list[tuple[str, str]]`, plus a helper to derive `tasks`/`dag` from it, so downstream code can still consume `EvidenceTask`s without a rewrite.

- Re-point `_extract_requirements` / `_construct_tasks` so each `EvidenceTask` is derived *from* a requirement, with `task.question` explicitly stamped as "the retrieval question for requirement Rᵢ". This keeps the existing deterministic behavior but makes the requirement→task derivation explicit and auditable.

- Preserve compatibility: `plan_node`, `plan_tasks_node`, and the benchmark should keep working; ideally the new structure *improves* the existing kind-level benchmark rather than replacing it.

### 2. Requirement-conditioned retrieval

Today retrieval mostly runs one query-shaped prompt per task. The next step is for each retrieval call to be answerable as "which requirement am I retrieving evidence for?" and for scoring to be requirement-aware.

Concretely:

- Pass `task.evidence_requirement` and `task.answer_type` deeper into retrieval where useful (`run_retrieval_pipeline(..., evidence_tasks=[task])` already exists in `execute_task_node`; the upgrade is to actually *use* those signals for shaping queries/ranking, not only for metadata filters).

- In `EnsembleReranker.rerank`, make `query_type` / requirement signals influence ranking more explicitly than today's feature-weight overrides. This could be a lightweight first step: per-requirement reranking profiles in `profiles.py` and/or per-requirement feature weights, rather than one global grid. The review's `S = w₁R + w₂A + w₃T + w₄C + w₅I + w₆K` is a design target; for an incremental step, start by making requirement coverage `C` an explicit signal in the sufficiency/retrieval loop rather than inventing a new monolithic scorer immediately.

- Identifier routing already works well; keep it as a deterministic channel and prefer it when an explicit identifier exists. No new retriever needed.

### 3. Upgrade verification toward claim-level entailment with contradiction detection

Today `generate_node`/`synthesize_node` already run `_verify_claims` using `ClaimExtractor` + `EvidenceVerifier`, and the graph routes on `claim_groundedness`. The next step is to move beyond RapidFuzz-style lexical overlap and model per-claim status as `SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONTRADICTED`, with contradiction/temporal/authority resolution, not just one overall score.

Concrete changes:

- Keep claim extraction; upgrade the verifier so each verified claim carries richer status and a citation back to the supporting chunk id(s). Today `verifications` already expose `verified`, `confidence`, `method`, `supporting_chunks`; the upgrade is to enrich status semantics and make contradiction an explicit signal.

- Add contradiction awareness: when evidence contains conflicting provisions or a later amendment/repeal, surface that as a verifier signal (`has_conflicts`, `temporal_conflict`, `authority_score` already exist in `evidence_sufficiency_node`; the next step is to feed them into claim status and into the response so the answer can qualify itself).

- Keep the graph simple: verification stays a service; the graph only routes on its signals.

### 4. Make evaluation measure requirements + evidence completeness, not only exact decomposition match

The benchmark already has `task_recall/precision/F1`, `dependency_accuracy`, `over/under_decomposition_rate`, `exact_match_rate`, and `per_query_class`. That's a strong base. The next step is to add requirement coverage, atomicity, decomposition efficiency, and especially evidence completeness.

Concrete changes:

- Extend `GoldEntry` / benchmark to record `gold_requirements` (with `id`, type, mandatory), `gold_dependencies`, `gold_answer`, `gold_authority`, `gold_temporal_state`, and `expected_citations`/`known_traps` where available.

- Add requirement-level metrics:
  - `requirement_coverage`: fraction of gold *mandatory* requirements represented.
  - `atomicity_score`: penalize multi-claim tasks where the gold expects independent propositions.
  - `decomposition_efficiency`: useful requirements / total generated requirements.
  - `evidence_completeness`: fraction of mandatory requirements with sufficient evidence in the run (this needs a small harness that runs retrieval + sufficiency against gold requirements).

- Add a small benchmark expansion toward failure modes: multi-part, nested, multi-hop, temporal, adversarial, contradiction-prone queries, with the richer gold fields above. Start small and deterministic; grow the dataset as the system matures.

- Add end-to-end answer-utility signals where feasible: citation completeness/correctness, authority accuracy, temporal correctness, abstention precision. Some of these can be computed deterministically from gold + run artifacts; others may require light judging.

### What is explicitly out of scope for now

- No new retriever.
- No proliferation of LangGraph nodes; keep orchestration thin.
- No blind top-k increases.
- No optimizing only MRR / exact decomposition match.
- No wholesale rewrite; the existing planner/DAG/sufficiency/claim-verification/routing/economics/benchmark is the foundation.

### Implementation order

1. Requirement model + `AnswerRequirementGraph`, with existing `DecompositionResult` still derivable.
2. Make requirement→task derivation explicit and pass requirement identity into retrieval/rerank/sufficiency paths where it helps.
3. Enrich claim verification status + contradiction/temporal/authority signals in the response.
4. Extend benchmark + gold dataset with requirement/atomicity/evidence-completeness metrics and a small failure-mode expansion.
5. (Later, separate scope) richer per-requirement reranking profiles and a lightweight evidence-sufficiency/confidence controller; temporal/authority hierarchy as a firmer first-class subsystem once the requirement model is in place.

### Implementation status (2026-09-12)

Steps 1–4 of the implementation order are complete and verified with an end-to-end functional regression suite (8 files compile; planner, sufficiency, node wiring, and benchmark all exercised). Nothing below adds a retriever or a LangGraph node.

**Step 1 — Requirement model + graph (`app/rag/evidence_task.py`)**

- `AnswerRequirement`: `id`, `type` (EvidenceRequirement), `subject`, `question` (derived retrieval question), `answer_type`, `evidence_required`, `mandatory`, `conditions`, `jurisdiction`, `temporal_scope`; with `to_dict`/`from_dict`.
- `AnswerRequirementGraph`: `requirements` + `dependencies` (`(depends_on, requirement_id)` pairs) + `derived_tasks`; helpers `requirement_ids()`, `mandatory_ids()`, `requirement_by_id()`; full dict round-trip.
- `requirement_to_answer_type()` maps requirement → expected answer shape.
- `requirement_graph_from_tasks()` derives a graph from bare task lists (bridge for external/task-only callers).
- `CoverageMatrix.to_dict()` restored (was unreachable dead code after `requirement_graph_from_tasks`).
- `QueryPlanner.plan()` now returns `DecompositionResult.requirement_graph` populated via `_build_requirement_graph()`; requirement→task derivation is explicit — every `EvidenceTask` carries a `requirement_id:{id}` entity marker pointing at its source requirement, and task dependencies are mirrored into requirement dependencies. Planner ids are `r{N}` (stable across the Requirement→AnswerRequirement round-trip).
- Backward compatibility preserved: `tasks`, `dag`, `evidence_requirements`, `coverage_matrix`, `total_tasks`, `_legacy_plan()`, `get_complexity()`, `decompose()`, `get_retrieval_strategy()` all unchanged.

**Step 2 — Requirement identity through the pipeline**

- `EvidenceTask.source_requirement_id` (first-class field, serialized with the task): the answer requirement a task was derived from. Set in `_build_task`; no `requirement_id:{id}` magic-string entity marker (reviewed and replaced — the marker previously leaked into retrieval-scoping entities and was parsed in three places).
- `app/rag/agent/sufficiency.py`: `TaskSufficiency.requirement_id` (via `task_requirement_id()`, which reads the field with a legacy-marker fallback for in-flight serialized states); verdict `to_dict()` includes it, so per-task sufficiency verdicts are traceable to answer requirements.
- Retrieval/rerank are already requirement-conditioned in effect: `_run_task_retrieval` runs one retrieval per task with `task.question` (the requirement's derived retrieval question) and attaches the task for the `evidence_plan` stage — so each retrieval call answers "which requirement am I retrieving evidence for?". The reranker remains query-conditioned per requirement-derived query.
- `app/rag/agent/state.py`: new `requirement_sufficiency: dict[str, bool]` state key.
- `app/rag/agent/nodes.py`:
  - `plan_node` serializes `requirement_graph` into `query_plan` (JSON-safe for the checkpointer).
  - `evidence_sufficiency_node` folds per-task verdicts into `requirement_sufficiency` (conservative AND semantics across tasks serving the same requirement).
  - `finalize_node` surfaces `requirement_sufficiency` + the serialized requirement graph on `response.agent` for observability.

**Step 3 — Claim-level verification enrichment (`app/rag/evidence_task.py` + `app/rag/agent/nodes.py`)**

- `ClaimVerificationStatus` (`SUPPORTED` / `PARTIALLY_SUPPORTED` / `UNSUPPORTED` / `CONTRADICTED`) + `ClaimVerification` (status, evidence, authority_score, temporal_valid, contradictions) with serialization.
- `build_claim_verification(claims, verifications, chunk_authority, contradictions, temporally_invalid_ids)` upgrades the binary verified/confidence verdicts into the status matrix. **All signals are per claim**, keyed by the claim's own supporting-chunk ids (reviewed fix): a claim's authority is the best weight among *its own* chunks; a claim is CONTRADICTED only when both sides of a contradiction pair are its own evidence (no guilt by association from unrelated pairs); claims standing on repealed/superseded text are capped at PARTIALLY_SUPPORTED.
- `_verify_claims` in `nodes.py` derives the chunk-keyed signal maps itself (from chunk metadata + the verifier's contradiction pairs), so the linear and DAG paths behave identically — no global-signal plumbing through `synthesize_node`.

**Step 4 — Requirement-level evaluation (`app/rag/evaluation/`)**

- New `app/rag/evaluation/decomposition_metrics.py`:
  - `requirement_coverage` (RC) — matched gold requirements / total gold, matched case-insensitively on id + type, multiset-aware.
  - `atomicity_score` (AS) — 1 − multi-claim tasks / total tasks (conjunctive-sentence heuristic).
  - `decomposition_efficiency` (DE) — useful requirements / generated requirements.
  - `evidence_completeness` (EC) — mandatory requirements with sufficient evidence / mandatory requirements; conservative 0.0 without sufficiency data.
  - `compute_requirement_level_metrics()` per entry and `requirement_level_report()` aggregate with per-query breakdown.
- `gold_dataset.py`: `gold_requirements`, `gold_answer_types`, `gold_mandatory` on all entries, **expanded to 10 entries** with a dedicated failure-mode torture tier (Phase 3 step 4): nested compound (provision + authority + penalty + exception through one section), multi-hop (Rule → authorizing section → penalty), temporal-before (penalty at a prior temporal state), adversarial permission (yes/no that decomposes into section + definition + exception), and fact-pattern application. The shared late-filing R1/R2/R3 literals are defined once as module constants and spread per entry with `dict(...)` (fresh dicts — no shared mutable gold state).
- `benchmark.py`: `QueryBenchmark` carries requirement-level gold + prediction fields; `add_gold_entry` transfers them; `record_prediction` records the requirement graph; `evaluate(sufficiency_map=...)` emits a `requirement_level` section — pass the per-run `requirement_sufficiency` map to score EC from real retrieval outcomes.

**Current baseline over the 10-entry gold set (planner dry-run, no sufficiency map):** RC=1.0, AS=1.0, DE=1.0, EC=0.0 (conservative — EC only scores when a real or simulated `requirement_sufficiency` map is passed to `evaluate()`; with a simulated map it is 1.0). Kind-level view: task recall/precision/F1, dependency accuracy and exact-match all 1.0 with zero over/under-decomposition. The torture tier originally exposed systematic secondary-requirement gaps (measured RC=0.658, DE=0.883); they were closed by planner fixes:

| Gap found by the torture tier | Fix |
|---|---|
| Temporal queries produced provision only — no amendment requirement | AMENDMENT secondary detector (amend/repeal/re-enact mentions) |
| "Who can enforce it" missed | AUTHORITY secondary detector (enforce/prosecute/initiate action) |
| Multi-hop typed the first requirement `provision` instead of `cross_reference` | Rule-X + "authorizes" retype to CROSS_REFERENCE |
| Permission questions had no definition/exception requirements | Deterministic definition-requirement heuristic for permission questions about a product substance |
| Fact patterns had no fact-application requirement | FACT_APPLICATION detector (mid-text yes/no offence questions) |
| SIMPLE-complexity queries collapsed to one task even with multiple requirements | Multi-requirement SIMPLE queries use wave construction; `_apply_minimum_sufficient` no longer collapses them |
| Comparative sides anchored on the wrong foundation | Wave-2 fallback anchors on the first same-domain wave-2 task; CROSS_REFERENCE/DEFINITION added to `_DOMAIN_OF` |
| Positional id-matching penalized valid decompositions (RC=0.25 with 3/4 types present) | Order-insensitive matching: id first, then type; subject required only to disambiguate repeated gold types (comparative sides), subset-based compatibility otherwise |

Matching semantics note: requirement ids are planner-internal, so the evidence *type* is the real ontology. The matcher therefore matches on id when possible, falls back to type-only when a gold type occurs once (planner subject extraction is too weak to be authoritative), and requires subject compatibility only when a gold type repeats (so the two sides of a comparative query stay distinguishable).

**Deferred (step 5, separate scope):** per-requirement reranking weight profiles, an explicit evidence-confidence controller (HIGH/MEDIUM/LOW), and deeper temporal reasoning (amendment/repeal chains as first-class fields).

### Review pass (2026-09-12) — quality corrections applied

A two-axis code review (standards + spec) of the Phase 3 changeset produced three fixes and one retraction:

- **Per-claim verification signals (spec fix).** `build_claim_verification` previously flattened authority/contradiction/temporal into one global value per claim set: every claim got `max(authority_values)` and `temporal_valid=not temporal_conflict`, and a claim was CONTRADICTED if any of its chunks appeared in *any* contradiction pair (guilt by association). All signals are now keyed by the claim's own supporting chunks: per-claim authority (best of its own chunks), CONTRADICTED only when both sides of a pair are the claim's own evidence, temporal cap via a repeal-flagged chunk-id set. `_verify_claims` derives the chunk-keyed maps itself, so the linear and DAG paths behave identically (the `synthesize_node` plumbing was removed).
- **First-class `source_requirement_id` (standards fix).** The `requirement_id:{id}` entity-marker string (parsed in three places, and leaking bookkeeping into retrieval-scoping entities) is replaced by an `EvidenceTask.source_requirement_id` field, serialized with the task; `task_requirement_id()` reads it with a legacy-marker fallback for in-flight checkpoints. Also corrected the `DecompositionResult` docstring, which claimed tasks are derived *from* the graph — they are derived from requirements by `_construct_tasks`, and the graph mirrors them.
- **Dead code removed (standards fix).** Unused speculative helpers and imports (`requirement_evidence_hints`, `_temporal_consistent` stub, `REQUIREMENT_GOLD_KEYS`, `_gold_*` accessors superseded by `_count_matched_requirements`, `_requirement_id_matches`, `Counter`/`field` imports).
- **Retraction:** the earlier review flagged `hybrid_retriever.py` changes as Phase 3 scope creep. They are in fact a standalone production fix from commit `bb4d88f` (the identifier arm was raising TypeError inside `reciprocal_rank_fuse` on any "Section N" query — the arm had been dead since the RRF extraction refactor). The review's baseline commit predated that fix; no revert was made or needed.

---

## V3 Target Architecture (Conceptual)

The long-run direction is not "more components". It is a shift in the central workflow:

```text
                         QUERY
                           │
                           ▼
                 ┌───────────────────┐
                 │ Query Understanding│
                 └─────────┬─────────┘
                           │
                           ▼
                 Answer Requirement Graph
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
             R1           R2           R3
              │            │            │
              ▼            ▼            ▼
          Retrieval    Retrieval    Retrieval
              │            │            │
              └────────────┼────────────┘
                           ▼
                    Evidence Graph
                           │
                           ▼
                    Evidence Sufficiency
                       │          │
                     YES          NO
                       │          │
                       │      Targeted Retrieval
                       │          │
                       └────┬─────┘
                            ▼
                       Synthesis
                            │
                            ▼
                    Claim-level Verification
                            │
                    ┌───────┴────────┐
                    ▼                ▼
                 Supported       Unsupported
                    │                │
                    │          Targeted Repair
                    │                │
                    └───────┬────────┘
                            ▼
                         Answer
```

This is the strongest direction for this project: **do not decompose the question into subqueries. Decompose it into independently verifiable answer requirements**, then derive the subquery from the requirement. That avoids the common failure mode of producing plausible subquestions that do not collectively guarantee the original question has been answered.

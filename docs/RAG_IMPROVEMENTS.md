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
|---------------------|-------------|n|__has_permission|Should the specified entity have permission X? |
|__conflict_reasoning|Resolve conflicts between multiple authorities/sections |
|__trace_lineage|Follow chains of dependency / amendment |
|__compare_provisions|Evaluate differences across versions/temporal states |
|__relationship_explanation|Explain relationships between concepts |
|__actionable_answer|Generate answer using KG traversal |

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

**TL;DR:** The RAG intelligence-layer **modules are implemented** (structured query decomposition, adaptive retrieval strategies, three‑stage reranking, evidence optimization, targeted retry with failure diagnosis, answerability gates, separate confidence metrics, KG‑based reasoning). **All four phases of the V2 plan (0–4) are complete (Phase 4: 2026‑09‑10):** the agent graph's wiring defects are fixed; the EvidenceTask DAG executes as **real parallel waves** (requirements-inferred dependencies, thread‑pool fan‑out via `RAG_AGENT_TASK_PARALLELISM`, deterministic cross‑reference expansion, per‑task results in `agent.task_results`); verification runs at **two levels** — a per‑task 7‑signal sufficiency rubric gates synthesis before generation, while rule‑based **claim‑level entailment** checks every factual claim of the generated answer against the evidence; **budget‑aware routing economics** (`routing_economics.py`) pick DIRECT vs decomposition vs DAG per query with shrink‑only tiers (direct/moderate/deep) capping tasks, rounds, documents and LLM calls on **both** paths, retry‑pinned decisions, and `agent.routing` telemetry on every response; and **measurement is live** (`evaluation/ragas_metrics.py`, `gold_dataset.py`, rewritten `benchmark.py`) — six deterministic RAGAS‑style reference metrics (faithfulness, answer relevance, context precision/recall, citation recall, groundedness) computed in `EvalRunner` and persisted per run, a gold decomposition dataset scored for task recall/precision/F1/dependency accuracy against the real planner (measured baseline: direct lookups decompose exactly; multi‑requirement and comparative queries under‑decompose), evidence‑aware `CoverageMetrics` replacing the fragile entity‑overlap heuristic, and per‑task `token_cost` + per‑stage cost telemetry in audit entries. Implementing the metrics also fixed the pre‑existing eval‑package import breakage (`__init__` imported six metric classes that were never defined). Agent suite: 192 passed; eval framework: 37 passed; measurement suite: 25 passed. See [`docs/archive/V2_PLANNER_EXECUTOR_VERIFIER_PLAN.md`](archive/V2_PLANNER_EXECUTOR_VERIFIER_PLAN.md).

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
                  └────────
```

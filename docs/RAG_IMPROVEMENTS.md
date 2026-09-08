# RAG Improvement Analysis — NSA Webservice

> **Purpose:** Structured analysis of improvement opportunities across **answer quality**, **code efficiency**, and **long-term stability**, grounded in source-code reading.
>
> **Phase 1 (Quality / Efficiency / Stability):** ALL 23 items implemented and verified as of 2026-09-07. See compacted session summary for details.
>
> **Phase 2 (Intelligence Layer):** New suggestions from architecture/design review. Not yet implemented — this section is the current backlog.
>
> **Last updated:** 2026-09-07 | **Base commit:** `2b98a82`

---

## Phase 1 Status: ✅ Complete

All items from sections 1–3 of the original analysis are implemented:

| #   | Item                           | Status                                          |
| --- | ------------------------------ | ----------------------------------------------- |
| 1.1 | KG Contract Fusion enabled     | ✅ `RAG_KG_FUSION=True`                         |
| 1.2 | Multi-hop retrieval wired      | ✅ `multi_hop_retrieve_node` in graph           |
| 1.3 | Answer decomposition           | ✅ `SubQueryDecomposer` wired into `tasks.py`   |
| 1.4 | MMR diversity                  | ✅ `mmr_rerank` in hybrid_retriever             |
| 1.5 | Identifier route fallback      | ✅ Verified in `identifier.py`                  |
| 1.6 | CitationValidator wired        | ✅ In generation pipeline                       |
| 1.7 | Token constants unified        | ✅ `app/rag/constants.py`                       |
| 2.1 | Retrieval cache enabled        | ✅ `RAG_RETRIEVAL_CACHE=True`                   |
| 2.2 | Retriever instance cache       | ✅ `lru_cache` + `clear_retriever_cache`        |
| 2.3 | Query embedding reuse          | ✅ `dense_vector` shared                        |
| 2.4 | KG Cypher combine              | ✅ Parallel in `kg/hybrid.py`                   |
| 2.5 | Parallel post-retrieval stages | ✅ `ThreadPoolExecutor` in `apply_stages`       |
| 2.6 | Query-type budget              | ✅ `ContextBuilder` per-type params             |
| 2.7 | Reranker encoder cache         | ✅ Covered by retriever cache                   |
| 3.1 | Dead code removed              | ✅ `nodes.py` cleaned                           |
| 3.2 | Narrow exception handling      | ✅ 6 locations                                  |
| 3.3 | Metrics export                 | ✅ `exporter.py` wired into `tasks.py`          |
| 3.4 | Corpus versioning              | ✅ `version_id`/`is_latest` + Alembic migration |
| 3.5 | `Any` types narrowed           | ✅ Key files                                    |
| 3.6 | `ce_head` default fix          | ✅ 20→30 in `reranker.py`                       |
| 3.7 | RAGQueryLog retention          | ✅ `cleanup_rag_logs.py`                        |
| 3.8 | Config linting                 | ✅ `config_lint.py` + `cfg.pyi`                 |
| 3.9 | Per-domain circuit breaker     | ✅ `circuit_breaker.py`                         |

---

## Phase 2: Intelligence Layer _(New — from architecture review)_

> **Core finding:** The system is a strong retrieval stack but behaves as a **retrieval-and-retry pipeline**, not an intelligent research agent. The bottleneck shifted from _retrieval mechanisms_ to _decision-making architecture_. The single largest improvement is a **query planning layer** that decides _what evidence is needed_ before retrieval.

### 2.1 Query Planning Layer _(Highest Priority)_

**Problem:** Current flow is `query → classify → retrieve → generate → verify → retry`. No explicit sub-question graph. A compound query like "What penalty applies, who enforces it, does it apply to licensed businesses?" is treated as a single retrieval. The classifier routes to one type, but the question has 4 independent evidence requirements.

**Proposed architecture:**

```text
USER QUERY
    ↓
Query Understanding (intent + entities + temporal scope)
    ↓
Query Decomposer → Evidence Requirements
    ↓
Retrieval Plan / DAG
    ↓
Parallel targeted retrieval per sub-question
    ↓
Evidence optimizer → Synthesis
```

**Output format:**

```json
{
    "intent": "legal_compliance_analysis",
    "domain": "fssai",
    "entities": { "instrument": "Food Safety and Standards Act, 2006" },
    "subquestions": [
        { "id": "q1", "type": "obligation", "question": "...", "evidence_required": ["provision"] },
        {
            "id": "q2",
            "type": "penalty",
            "question": "...",
            "depends_on": ["q1"],
            "evidence_required": ["penalty_provision"]
        },
        {
            "id": "q3",
            "type": "authority",
            "question": "...",
            "depends_on": ["q2"],
            "evidence_required": ["authority_provision"]
        },
        {
            "id": "q4",
            "type": "applicability",
            "question": "...",
            "depends_on": ["q1"],
            "evidence_required": ["scope_definition"]
        }
    ]
}
```

**Files to create/modify:**

- `app/rag/planning/query_planner.py` — new module: intent extraction + decomposition + evidence planning
- `app/rag/agent/graph.py` — insert planner between classify and retrieve
- `app/rag/agent/nodes.py` — add `plan_node`, `decompose_node`

**Difficulty:** High (1–2 weeks). **Impact:** High — enables all other Phase 2 items.

---

### 2.2 Finer Query Classification

**Problem:** Current classifier has 4–6 broad classes (`penalty`, `authority`, etc.), sufficient for routing but insufficient for reasoning.

**Proposed 18+ types:**

```text
IDENTIFICATION | LOOKUP | DEFINITION | PROHIBITION | DUTY | RIGHT
POWER | PENALTY | EXCEPTION | PROCEDURE | APPLICABILITY | COMPARISON
TEMPORAL | JURISDICTION | CROSS_REFERENCE | CASE_LAW | MULTI_HOP
FACT_PATTERN | COMPLIANCE_ASSESSMENT
```

Allow multiple intents per query (primary + secondary list).

**Files to modify:** `app/rag/retrieval/query_classifier.py`

**Difficulty:** Medium (3–5 days). **Impact:** High — drives adaptive retrieval.

---

### 2.3 Adaptive Retrieval Strategy

**Problem:** Retrieval strategy is mostly predetermined. Same pipeline runs for every query type.

**Proposed strategy selection:**

```text
exact section → identifier + BM25
conceptual   → dense + BM25 + CE
cross-ref    → identifier + KG
historical   → temporal KG + metadata
multi-hop    → iterative retrieval
case law     → case-law collection + authority filter
```

**Files to modify:** `app/rag/retrieval/factory.py`, `app/rag/tasks.py`

**Difficulty:** Medium (1 week). **Impact:** High — avoids over-retrieving for simple queries.

---

### 2.4 Three-Stage Reranking

**Problem:** Current reranker is two-stage (dense sparse fusion → CE). Legal identity features are underutilized.

**Proposed:**

```text
Candidate pool (100)
    ↓
Legal identity / metadata filter
    ↓
Deterministic legal ranker (section proximity, authority hierarchy)
    ↓
CE top 30
    ↓
Evidence diversity / coverage ranker
    ↓
Top 8–12
```

**Files to modify:** `app/rag/retrieval/reranker.py`, `app/rag/retrieval/hybrid_retriever.py`

**Difficulty:** Medium (1 week). **Impact:** Medium–High.

---

### 2.5 Evidence Coverage Optimizer

**Problem:** Relevance-only ranking can return 4 chunks on the same section while missing 4 other sections needed for a complete answer.

**Proposed:** `EvidenceSelector` promoted to central generation-stage component. Maximizes section/coverage diversity, not just per-chunk relevance.

```text
Evidence requirements
    ├── obligation → Section 24
    ├── exception  → Section 25
    ├── penalty    → Section 26
    └── authority  → Section 27
         ↓
  Evidence optimizer → minimal sufficient set
```

**Files to modify:** `app/rag/retrieval/stages.py`, `app/rag/retrieval/hybrid_retriever.py`

**Difficulty:** Medium (1 week). **Impact:** High — directly addresses answer completeness.

---

### 2.6 Targeted Retry (Replace Generic Expansion)

**Problem:** Current retry expands query with `"explain"`, `"define"`, `"example"`. Generic. Doesn't address what actually failed.

**Proposed:** `VERIFY → Failure diagnosis → Missing evidence classification → Targeted query generation → Targeted retrieval → Evidence merge → Regenerate`

**Files to modify:** `app/rag/agent/nodes.py`, `app/rag/verification/`

**Difficulty:** Medium (1 week). **Impact:** High — eliminates wasted retrieval rounds.

---

### 2.7 Retrieval Failure Classifier

**Problem:** When verification fails, the system doesn't know _why_.

**Proposed failure taxonomy:**

```text
RETRIEVAL_FAILURE
├── missing_provision → identifier search
├── wrong_provision   → collection reroute
├── wrong_act         → identifier search
├── wrong_jurisdiction→ temporal filter
├── missing_exception → hierarchy + reference graph
├── missing_definition→ definition search
├── missing_cross_ref → KG traversal
├── temporal_conflict → temporal retrieval
├── insufficient_authority → authority retrieval
├── insufficient_case_law  → case-law retrieval
└── unsupported_fact_inference → dense expansion
```

Each failure maps to a specific recovery strategy.

**Files to create:** `app/rag/planning/failure_classifier.py`

**Difficulty:** Medium (1 week). **Impact:** High — enables self-correcting RAG.

---

### 2.8 Answerability Check Before Generation

**Problem:** System proceeds to generation even when evidence is insufficient, wasting LLM tokens on incomplete answers.

**Proposed:**

```text
retrieval
    ↓
Evidence Sufficiency Check
    ├── sufficient → generate
    └── insufficient → retrieve more → generate
```

```json
{
    "required_evidence": 4,
    "found_evidence": 3,
    "coverage": 0.75,
    "missing": ["penalty_provision"],
    "answerable": false
}
```

**Files to modify:** `app/rag/generation/context_builder.py`, `app/rag/tasks.py`

**Difficulty:** Low–Medium (3–5 days). **Impact:** Medium — strong anti-hallucination guard.

---

### 2.9 Separate Confidence Metrics

**Problem:** Single `groundedness_score` conflates distinct quantities.

**Proposed explicit tracking:**

```text
R = retrieval confidence
E = evidence coverage
C = citation correctness
G = claim groundedness
A = answer completeness

Final confidence = f(R, E, C, G, A)
```

**Files to modify:** `app/rag/evaluation/metrics.py`, `app/rag/tasks.py`

**Difficulty:** Low (2–3 days). **Impact:** Medium — better observability.

---

### 2.10 Evaluation Framework Upgrade

**Problem:** Current 6 metrics don't measure legal-specific quality.

**Proposed additions:**

| Metric                  | What it measures                                |
| ----------------------- | ----------------------------------------------- |
| Legal Identity Accuracy | Correct Act + section identified?               |
| Provision Completeness  | Every materially necessary provision retrieved? |
| Citation Entailment     | Does citation _support_ the claim?              |
| Temporal Accuracy       | Was provision valid at requested date?          |
| Cross-reference Recall  | Indirectly referenced provisions recovered?     |
| Exception Recall        | Both main rule + exception retrieved?           |
| Answer Completeness     | Required subquestions answered / total          |

**Plus:** Decomposition benchmark (per-query gold subquestions) and per-query-class evaluation (13 classes: exact lookup, act ID, definition, single-hop, cross-ref, exception, multi-provision, comparison, temporal, jurisdiction, case law, fact-pattern, multi-hop).

**Files to modify:** `app/rag/evaluation/metrics.py`, benchmark scripts

**Difficulty:** Medium (1 week). **Impact:** High — required to measure Phase 2 improvements.

---

### 2.11 KG as Reasoning Engine

**Problem:** KG currently used for query→provisions and

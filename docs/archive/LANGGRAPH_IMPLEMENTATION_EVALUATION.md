# LangGraph Implementation Evaluation — NSA Webservice

**Date:** 2026-08-15  
**Status:** ⚠️ Evaluation / Planning (not yet approved)  
**Author:** Agent review of codebase state as of v0.8.0

---

## 1. Current Architecture Summary

### 1.1 What Exists Today

The NSA Webservice is a **Flask 2.x monolith** (v0.8.0) with 20+ registered blueprints. The RAG subsystem is **fully implemented** (695 tests, all passing) but uses a **purpose-built, sequential, function-call pipeline** architecture — explicitly designed *without* LangChain or LangGraph.

The current RAG pipeline (`app/rag/tasks.py::run_generation_pipeline`) is a linear composition of service calls:

```
run_generation_pipeline(query)
├── run_retrieval_pipeline(query)                    [Phase 1 — Retrieval]
│   ├── QueryClassifier.classify(query)
│   ├── DenseRetriever.search(query)                 → Qdrant vector search
│   ├── SparseRetriever.search(query)                → Qdrant BM25 / rapidfuzz fallback
│   ├── IdentifierRetriever.search(query)            → lexical "{Act} section {N}" route
│   ├── HybridRetriever.rrf_fuse(...)                → RRF (k=60)
│   ├── EnsembleReranker.rerank(chunks)              → sec_act + cross-encoder ensemble
│   └── RetrievalLogger.log(...)
├── KGContractFusion (optional, RAG_KG_FUSION=true)
├── KGContextExpansion (optional, RAG_KG_EXPANSION=true)
├── GroundedGenerationService.generate(query, chunks)  [Phase 2 — Generation]
│   ├── ContextBuilder.build(chunks)
│   ├── PromptTemplate.render(context)
│   ├── GroundedLLMClient.call(prompt)               → httpx → OpenRouter/OpenAI (or stub)
│   ├── CitationTracker.extract(response)
│   ├── ResponseSanitizer.sanitize(response)
│   └── GenerationLogger.log(...)
├── HallucinationDetector.verify(response, chunks)     [Phase 3 — Verification]
│   ├── ClaimExtractor.extract(response)
│   ├── EvidenceVerifier.verify(claims, chunks)
│   ├── CitationValidator.validate(citations, chunks)
│   ├── GroundednessScorer.score(...)
│   └── HallucinationReport
└── return RAGResponse (dict)                          [Phase 5]
```

**Key architectural characteristics:**
- **No state persistence between steps:** Context flows as function arguments and return values
- **No conditional routing:** Every pipeline step runs unconditionally
- **No checkpointing:** If the pipeline fails midway, there is no way to resume
- **No parallel execution:** Dense, sparse, and identifier retrieval run sequentially
- **No human-in-the-loop:** LLM response is returned directly without review
- **No iterative refinement:** If hallucination is detected, the pipeline does not retry

### 1.2 What the Documentation Says

| Document | LangGraph Status |
|---|---|
| **README.md** (Target Stack, Levels 5–10) | ✅ Listed as the target orchestration layer |
| **AGENTS.md** (§3.2 Key Patterns) | ❌ "Keep Flask" — documented architectural decision (do not re-litigate) |
| **RAG_AUDIT_REPORT.md** (§5.2, §9.3) | ❌ "Not part of project stack. The system uses its own purpose-built pipeline architecture. Adding these frameworks would be a major architectural change." |
| **ENGINEERING_ASSESSMENT.md** (2026-07-26) | ❌ "LangGraph: LOW feasibility, 5% readiness, Complete rewrite required" |
| **plan.md** (pending phases) | ❌ Phases 15, 17, 19, 20 pending — no LangGraph mention |

**Tension identified:** The README's "Target Stack" envisions a future migration to FastAPI + LangGraph, but AGENTS.md (canonical reference, 2026-08-10) explicitly says "Keep Flask" and the RAG_AUDIT_REPORT says LangGraph is "not a missing dependency" but "a major architectural change."

---

## 2. What LangGraph Would Add

LangGraph is a framework for building **stateful, multi-step, conditional LLM agents**. If integrated:

### 2.1 StateGraph Architecture
Instead of sequential function calls, the pipeline becomes a directed graph:
- **Nodes** = individual steps (retrieve, generate, verify, expand_query, etc.)
- **Edges** = transitions, including conditional routing
- **State** (a `TypedDict`) persists across all nodes and is checkpointed

### 2.2 Capabilities Currently Missing

| Capability | Current Status | How LangGraph Adds It |
|---|---|---|
| **Conditional routing** | All steps run unconditionally | `add_conditional_edges` — e.g., "if groundedness < 0.7, route to expand_query" |
| **Iterative refinement** | One-shot pipeline | Graph loops — retrieve -> verify -> if fail, expand query and re-retrieve |
| **Parallel execution** | Sequential retrieval | `Send` — fan out dense + sparse + identifier retrieval in parallel |
| **Checkpointing** | No state persistence | `MemorySaver` (dev) / `PostgresSaver` (prod) — persist state, resume after failure |
| **Human-in-the-loop** | No review gate | `interrupt()` — pause pipeline for human approval |
| **Multi-agent coordination** | Single service orchestrator | Sub-graphs — separate agents for retrieval, generation, verification |
| **Conversation memory** | Stateless per-request | `MessagesState` — thread conversation history across turns |

### 2.3 Specific Use Cases in This Codebase

**Use Case 1: Self-Critique Loop (Phase 3 Verification -> Regeneration)**
- Current: `HallucinationDetector` runs after generation; result returned directly even if ungrounded claims detected.
- With LangGraph: After verify node, conditional edge checks `groundedness_score < 0.7` -> route to `expand_query` -> `retrieve` -> `generate` -> `verify` loop (max retry count = 2).

**Use Case 2: Query Expansion on Poor Retrieval**
- Current: `run_retrieval_pipeline` always uses the same query. No expansion if retrieval quality is poor.
- With LangGraph: Quality gate checks `max(scores) < 0.3` or `len(chunks) < 5` -> route to `expand_query` node (LLM reformulation) -> re-retrieve.

**Use Case 3: Parallel Retrieval Arms**
- Current: DenseRetriever, SparseRetriever, IdentifierRetriever run sequentially in `run_retrieval_pipeline`.
- With LangGraph: Use `Send` to fan out all three in parallel, then join results for RRF fusion. Could cut retrieval latency by ~60%.

**Use Case 4: Evidence Set Selection as a Graph Step (V8 Integration)**
- Current: V8 selectors exist in `app/rag/retrieval/evidence_selector.py` but are not wired into generation pipeline (feature flag `ENABLE_EVIDENCE_SELECTOR` default false).
- With LangGraph: After reranking, route to `evidence_selector` node applying V8 selectors (TopK, MMR, LegalStructure, HierarchyAware, Hybrid) before context building.

**Use Case 5: KG Contract Fusion as Conditional Branch**
- Current: KG contract fusion (`RAG_KG_FUSION`) applies to every query unconditionally when enabled.
- With LangGraph: Add `kg_contract` node that only runs when query type is a provision lookup.

---

## 3. Integration Approach Options

### Option A: Gradual Integration (Recommended)
Create a **new agent mode** alongside the existing pipeline, gated by `RAG_USE_AGENT_PIPELINE` flag. The existing pipeline remains untouched as default.

**Pros:** Zero risk to 695-test suite, A/B testing possible, gradual migration.
**Cons:** Temporary code duplication, feature flag overhead.

### Option B: Full Pipeline Replacement
Replace `run_generation_pipeline` with a `StateGraph` compiled app.

**Pros:** Clean architecture, no duplication.
**Cons:** Breaks all 695 RAG tests, high risk, contradicts AGENTS.md "purpose-built architecture" principle.

### Option C: Targeted Enhancement (Hybrid)
Keep existing pipeline but add LangGraph only for the retry/refinement loop.

**Pros:** Minimal change, adds most valuable capability.
**Cons:** Doesn't unlock parallel execution or full stateful workflows.

---

## 4. Implementation Plan (Phases)

### Phase 1: Foundation (1-2 weeks, 20% risk)
**Goal:** Install dependencies, define state schema, create minimal prototype graph.

1. Add `langgraph>=0.2.0` to `pyproject.toml`
2. Create `app/rag/agent/` package:
   - `state.py` — `RAGState` TypedDict (reuses existing `RetrievedChunk`, `RAGResponse`)
   - `nodes.py` — thin wrappers around existing services (classify, retrieve, generate, verify)
   - `graph.py` — `StateGraph` assembly (linear: classify -> retrieve -> generate -> verify -> END)
   - `routes.py` — `POST /api/rag/query_agent` endpoint
3. Add `RAG_USE_AGENT_PIPELINE` config flag (default false)
4. 10-15 tests for adapter nodes (mocking existing services)

**Design decision:** Nodes are **thin adapters** calling existing service classes. No business logic is duplicated.

### Phase 2: Conditional Routing & Retry (1 week, 20% risk)
**Goal:** Add stateful conditional routing and iterative refinement.

1. Add `retry_count: int` and `audit_trail: list[str]` to `RAGState`
2. Add `expand_query_node` — reuses `GroundedLLMClient` for query reformulation
3. Add conditional edge after verify: `if groundedness_score < 0.7 and retry_count < 2: route to expand_query`
4. 5-10 tests for routing logic

### Phase 3: Checkpointing & Human-in-the-Loop (1 week, 15% risk)
**Goal:** Add state persistence and human review gates.

1. Add `langgraph-checkpoint-postgres` for production checkpointing
2. Wire `PostgresSaver` to existing `DATABASE_URL`
3. Add `interrupt()` before returning final answer when confidence < threshold
4. Add `POST /api/rag/query_agent/continue` endpoint to resume after human input
5. 5 tests for checkpoint/resume

### Phase 4: Parallel Execution & Multi-agent (1-2 weeks, 25% risk)
**Goal:** Fan out retrieval and add query-reformulation sub-agent.

1. Use `Send` to run DenseRetriever + SparseRetriever + IdentifierRetriever in parallel
2. Create `QueryReformulationAgent` sub-graph
3. 10-15 tests for parallel execution and sub-agent composition

### Phase 5: API Migration & Deprecation (1 week, 10% risk)
**Goal:** Wire agent pipeline into existing endpoint.

1. Modify `app/rag/routes.py::query()` to check `RAG_USE_AGENT_PIPELINE`
2. Add A/B test metrics to `RAGQueryLog`
3. 5 tests for route delegation

**Total estimate:** 5-7 weeks, 180-200 hours, moderate risk.

---

## 5. Risks & Trade-offs

### 5.1 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Dependency bloat** | HIGH | LangGraph pulls in `langchain-core`, `pydantic-settings`, `tenacity`. Pin minimal transitive deps. |
| **Architectural tension** | HIGH | AGENTS.md says "Keep Flask," README says LangGraph. Needs governance decision. |
| **Memory constraints** | MEDIUM | Render Free (512MB) already exceeded by RAG deps. LangGraph adds ~50-80MB. |
| **Test paradigm shift** | HIGH | Current tests use dataclass-based stubs. LangGraph needs `langgraph.test` or `pytest-asyncio`. |
| **Breaking changes** | HIGH | Option B breaks all 695 RAG tests. Option A avoids this. |
| **Flask vs FastAPI** | MEDIUM | README says FastAPI, AGENTS.md says keep Flask. LangGraph works with both (sync mode). |

### 5.2 Dependencies to Install

- `langgraph>=0.2.0` (pulls in `langchain-core`, `pydantic-settings`, `tenacity`)
- `langgraph-checkpoint-postgres>=2.0.0` (for production checkpointing)

**Note:** `pydantic>=2.0` is already installed. Total new footprint: ~3 packages, ~50-80MB.

### 5.3 Deployment Considerations

- **Flask compatibility:** LangGraph sync mode works within Flask. `PostgresSaver` reuses existing `DATABASE_URL`.
- **Render:** Current `render.yaml` deploys Flask + Celery. LangGraph runs in-process. No new infrastructure needed.
- **QStash:** LangGraph graphs can be invoked within QStash-triggered Celery tasks.

---

## 6. V7/V8 Status Corrections

The context summary provided with this task contains several inaccuracies verified against the actual codebase:

### 6.1 V8: Test Count and Failing Tests

**Claim:** 2 tests fail (`test_exclude_duplicate_provisions`, `test_parent_child_evidence`) because `LegalDocument.parents` returns `None`.

**Reality (verified via `pytest --collect-only` and `pytest -v`):**
- There are **39 tests** in `tests/test_evidence_set_selector.py` (verified: `collected 39 items`), NOT 28 and NOT the named failing tests.
- The named tests `test_exclude_duplicate_provisions` and `test_parent_child_evidence` **do not exist** in the file.
- **`LegalDocument` (`app/models/rag.py`, lines 121-152) has no `parents` field** — and the 3 actual failing tests do not reference `LegalDocument` at all.
- The test classes and counts (verified via pytest collection): `TestTokenizer` (5), `TestBuildCandidates` (6), `TestTopK` (2), `TestMMR` (4), `TestLegalStructureDiversity` (2), `TestHierarchyAware` (3), `TestHybrid` (3), `TestCandidatesToArmResult` (2), `TestComputeRedundancy` (4), `TestRegistry` (2), `TestDeterminism` (2), `TestScoreQuestionIntegration` (4). Total: **39**.
- **3 tests actually fail (36/39)**:
  1. `TestHierarchyAware::test_preserves_section_subsection_chain` — `HierarchyAwareSelector.select()` pulls in `parent` (HL=1) as a free add when selecting `child` (HL=2, score=0.90), consuming slot 1. Then `child` fills slot 2. `child2` (HL=3) is never reached — k=2 slots are exhausted before the deepest child is iterated. Root cause: parent-pull consumes a selection slot before the parent's children can be considered.
  2. `TestHybrid::test_kg_complementarity` — `HybridEvidenceSetSelector.select()` Phase 1 (MMR loop) fills all k=5 slots with chunks (c1-c5), leaving no room for Phase 2 (KG section complementarity). The KG item covering section 82 is never selected. Root cause: Phase 2 runs after Phase 1, but Phase 1 already exhausted all k slots.
  3. `TestComputeRedundancy::test_all_same_section` — `compute_redundancy()` computes `duplicate_provision_rate = 1.0 - (unique_keys / total_keys) = 1.0 - (1/4) = 0.75`, but the test expects `> 0.99`. Root cause: the formula deduplicates by `(family, section)` tuples (from `section_keys`); 4 items sharing one key yields 1/4 = 0.25, so dup_rate = 0.75. The test expectation doesn't match the formula.
- The `CandidateItem` dataclass (used by these selectors) has `parent_key` field (derived from `parent_chunk_id`), and `LegalChunk` (line 155-190 of `app/models/rag.py`) has `parent_id`, NOT `parents`.

### 6.2 V8: `run_evidence_set_selection.py` Syntax Error
**Claim:** SyntaxError — malformed f-string `f"Evidence Coverage: {avg_recall}%"}`.
**Reality:** The actual error is `IndentationError: unexpected indent` at **line 320** (verified with `python -c "import ast; ast.parse(...)"`). Line 319 has `path.write_text(...)` at 4-space indent; line 320 has `logger.info(...)` at 8-space indent. There is no `f"Evidence Coverage"` f-string in the file.

**Fix:** Dedent line 320 to 4 spaces:
```python
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("wrote %s", path.name)
```

### 6.3 V7: Script Validity
**Claim:** V7's `evaluation/v7_gap_metadata.py` was "not confirmed to have been run."
**Reality:** The script is **syntactically valid** (verified with `ast.parse`). It loads from cached ARM F retrieval results (`evaluation/out/cache/payload_index.jsonl` and `evaluation/out/ceiling_v5/`). The report `V7_METADATA_GAP_REPORT.md` exists at `evaluation/out/ceiling_v7/V7_METADATA_GAP_REPORT.md` (2,118 bytes, timestamp 2026-08-13T14:36:10Z) showing 100.0% pool ceiling (86/86, 0.0% gap). Reproducible if the cached data files exist.

### 6.4 V8 Evidence Set Report
**Claim:** "The V8 evaluation report has never been generated."
**Reality:** Verified — no `V8_EVIDENCE_SET_REPORT.md` exists in `evaluation/out/`. The `backfill_summary_apply.json` in `evaluation/out/ceiling_v8/` records 7 newly-resolvable gold units. The V8 report could not be generated because of the IndentationError described above.

---

## 7. Test Strategy

### 7.1 Existing Coverage to Preserve
- **2,203 tests** collected total (verified via `pytest --collect-only`, 2026-08-15). AGENTS.md's 1,757 figure was from an Aug 10 collect and is now stale.
- **694 RAG-related tests** (per AGENTS.md collect; the audit counts 695 — the collect is authoritative).
- **39 V8 evidence-set selector tests** (`tests/test_evidence_set_selector.py`): 36 pass, 3 fail (see Section 6.1).
- All must remain green (Option A ensures this).

### 7.2 New Tests for LangGraph Integration

| Test File | Tests | Covers |
|---|---|---|
| `tests/test_rag_agent_state.py` | 5 | RAGState schema, initialization, serialization |
| `tests/test_rag_agent_nodes.py` | 12 | Node adapters wrap services correctly, error handling |
| `tests/test_rag_agent_graph.py` | 8 | Graph compilation, state flow, conditional routing, retry loop |
| `tests/test_rag_agent_routes.py` | 6 | `/api/rag/query_agent` endpoint, 200/400/422/503 |
| `tests/test_rag_agent_checkpoint.py` | 5 | State persistence, resume after failure, human-in-loop interrupt |
| `tests/test_rag_agent_parallel.py` | 4 | Parallel Send fan-out, result merging |

**Total new tests:** ~40, all using stub LLM mode (no network required).

---

## 8. Recommendation

### 8.1 Should LangGraph Be Implemented?

**Yes, with caveats.** The README's Target Stack explicitly lists LangGraph as the desired orchestration layer. The current pipeline is functional but lacks conditional routing, iterative refinement, checkpointing, and human-in-the-loop — all capabilities LangGraph provides natively.

### 8.2 Recommended Approach

**Adopt Option A (Gradual Integration):**

1. Start with Phase 1 only (minimal prototype) to validate LangGraph adapters can wrap existing services.
2. Use **sync mode** to align with Flask + Celery stack.
3. Gate behind `RAG_USE_AGENT_PIPELINE` feature flag — existing `/api/rag/query` stays on current pipeline.
4. Add `/api/rag/query/agent` as new endpoint for LangGraph pipeline.
5. **Do NOT attempt FastAPI migration** as part of this effort.
6. Prioritize Phase 2 (conditional routing + retry) over Phase 4 (parallel execution).

### 8.3 Prerequisites Before Implementation

1. **Governance decision on Flask vs FastAPI** — AGENTS.md says "Keep Flask," README Target Stack says FastAPI.
2. **Fix V8 runner IndentationError** — line 320 of `evaluation/run_evidence_set_selection.py` (1-line dedent fix).
3. **Run V8 test baseline** — `pytest tests/test_evidence_set_selector.py` to confirm current status (result: **36 passed, 3 failed of 39 tests**).
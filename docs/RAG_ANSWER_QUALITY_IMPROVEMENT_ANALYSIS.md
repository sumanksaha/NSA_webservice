# Legal RAG — Answer Quality Improvement Analysis

**Date:** 2026-10-04  
**Scope:** Present implementation audit of `app/rag` plus measured bottlenecks from existing eval docs  
**Goal:** Increase capacity of the system to provide correct and better answers  
**Companion canvas:** Cursor canvas `RAG-Improvement-Review.canvas.tsx` (same findings, interactive layout)

> **Corrected 2026-10-04 against the repo.** Three claims in the first draft were
> wrong and are fixed in place: (1) the human audit is **complete (150/150)**, not
> open; (2) the ~9–12% binary baseline is **inflated by 38% evaluator misses** —
> human-adjudicated accuracy is ~61%; (3) P0-1's `evidence_set` is a
> **`dict`, not an `EvidenceSet`**, and spans three call layers. See §1, §5.3, §6.

---

## 1. Executive verdict

Retrieval is largely solved. Answer correctness is not.

| Signal | Approx. value | Source |
|---|---:|---|
| Soft answer correctness @ CE K=100 | **38.4%** | Answer-correctness roadmap / upgrade research |
| Soft correctness (oracle O3 full support) | **~37%** | Experiment C |
| Binary correctness | **~9–12%** (mechanically computed) | Upgrade research |
| Binary correctness, human-adjudicated | **~61%** | `evaluation/out/ceiling_v5/full_review_tabulation.md` |
| Retrieval R@100 | **~88%** | Experiment A/B |
| Groundedness | **~0.78–0.87** | Roadmap / upgrade research |
| Citation recall / precision | **~0.69–0.72 / ~0.68–0.78** | Roadmap / upgrade research |

**Core diagnosis:** As retrieval availability rises (K=1→100: recall ~29%→88%), answer correctness barely moves (~33%→38%). Even oracle gold/full-support context stays near ~37% soft. The remaining gap is primarily:

> retrieved legal evidence → interpretation → application → final answer

plus a **soft Jaccard metric** that is a weak proxy for legal correctness.

**Corpus gaps are NOT a material driver** — measured at **1.6% of gold units** (4/248), affecting 3 of 150 questions, all in one instrument (§5.2). They were originally listed here from prose; measurement contradicts that.

**The 9–12% binary figure is substantially an evaluator artifact, not a model failure.** The human audit is complete (150/150 reviewed, `evaluation/out/ceiling_v5/full_review_tabulation.md`): of the reviewed set, **38.0% were `evaluator_miss`** (grader wrong), **59.3% `model_wrong`**, 2.7% `reference_narrow`. Adjudicated model-side accuracy is therefore ~61%, not ~10%.

Do **not** treat this as “need more K” or “CE alone is broken.” Do **not** treat O3≈37% soft as a hard LLM ceiling. Do **not** treat 9–12% binary as model accuracy — the grader undercounted, and 38 evaluator misses remain unresolved as evaluator-v3 candidates.

---

## 2. Current architecture (what exists)

Pipeline phases (from `docs/RAG_IMPLEMENTATION.md` and code):

1. **Ingestion** — load → clean → dedup → chunk → embed → Qdrant index  
2. **Retrieval** — classify → hybrid dense+sparse (+ optional identifier arm) → RRF → CE/ensemble rerank → optional stages  
3. **Generation** — optional subquery merge → KG fusion/expansion → grounded generation → sanitizer / hallucination detector  
4. **Verification** — claims, fuzzy entailment, citations, groundedness  
5. **Evaluation** — deterministic RAGAS-style + coverage / evidence / advisor metrics  
6. **LangGraph agent (opt-in)** — classify → plan → retrieve / multi-hop / DAG → verify → retry / finalize → optional advisor  

**Default production path today:** legacy `run_retrieval_pipeline` → `run_generation_pipeline` in `app/rag/tasks.py`.  
**Agent path:** gated by `RAG_USE_AGENT_PIPELINE` (default **false**).

### Key modules

| Area | Paths |
|---|---|
| Pipeline entry | `app/rag/tasks.py` |
| Feature flags | `app/shared/config.py` |
| Hybrid / RRF / rerank | `app/rag/retrieval/hybrid_retriever.py`, `rrf.py`, `reranker.py` |
| Stages | `app/rag/retrieval/stages.py`, `evidence_selector.py` |
| Agent graph | `app/rag/agent/graph.py`, `agent/nodes/linear.py`, `agent/nodes/dag.py` |
| Generation | `app/rag/generation/grounded_service.py`, `context_builder.py`, `structured_reasoner.py` |
| Verification | `app/rag/verification/hallucination_detector.py`, `evidence_verifier.py`, `claim_extractor.py` |
| KG reasoner | `app/rag/planning/kg_reasoner.py` (+ `kg.hybrid`) |
| Chunking | `app/rag/chunker.py`, legal paragraph engine adapter |
| Eval | `app/rag/evaluation/ragas_metrics.py`, `metrics.py`, `evidence_metrics.py` |
| Benchmark / scoring harness | `evaluation/eval_e2e_v2.py`, `evaluation/grading.py`, `evaluation/abstention_rule.py` |
| Answer-failure taxonomy | `evaluation/answer_error_taxonomy.py` |

---

## 3. What is already strong

| Area | Status | Notes |
|---|---|---|
| Hybrid dense + sparse + RRF + CE | **Live / strong** | Identifier route recovered +13.3pp candidate-pool ceiling |
| Legal query typing + identifier route | **On by default** | Important lexical arm for Act/section questions |
| Food-intent retrieval path | **Strong** | v1.1 bench: retrieval metrics → 1.00 (LLM stubbed; answer quality not measured) |
| Legal paragraph chunking / hierarchy | **Strong at ingest** | Section inheritance, parent reconstruction on food path |
| Grounded generation | **Live** | Context budgets, citations, sanitizer |
| Hallucination detector | **On by default** | Claim extract + fuzzy verify + citation checks |
| KG fusion / expansion | **On** (fusion wins if both set) | Neo4j-backed; degrades gracefully |
| LangGraph agent | **Implemented, off by default** | Retries, citation gate, DAG sufficiency, HITL hooks |
| Structured reasoner + auditor | **Code present, flags off** | IR: issue → rules → exceptions → conditions → conclusion |
| FSO advisor / confidence | **Code present, flag off** | Deterministic act selection + heuristic confidence |

---

## 4. Feature flags that gate answer quality

| Flag | Default | Effect when left off |
|---|---|---|
| `RAG_USE_AGENT_PIPELINE` | **false** | No self-correcting graph on main query path |
| `ENABLE_EVIDENCE_SELECTOR` | **false** | No compact complementary evidence set |
| `ENABLE_STRUCTURED_REASONER` | **false** | No structured IR before answer |
| `ENABLE_LEGAL_AUDITOR` | **false** | No audit/revise loop (also needs reasoner) |
| `ENABLE_REFERENCE_EXPANSION` | **false** | No reference-graph candidate expansion |
| `ENABLE_EVIDENCE_PLAN` | **false** | No task-aware retrieval plan stage |
| `FSO_ADVISOR_ENABLED` | **false** | No Act advisory / calibrated confidence on agent path |
| `RAG_QDRANT_BM25` | **false** | Client-side sparse instead of cluster BM25 |
| `RAG_CE_SECTION_PREFIX` | **false** | CE sees raw text without §-identity prefix |
| `RAG_HALLUCINATION_DETECTOR` | **true** | Claim-level detector in generation |
| `RAG_IDENTIFIER_ROUTE` / `RAG_LEGAL_QUERY_TYPING` / `ENABLE_LEGAL_IDENTITY` | **true** | Already-on quality levers |
| `RAG_KG_FUSION` / `RAG_KG_EXPANSION` | **true** (mutually exclusive at runtime) | KG context injection |

---

## 5. Concrete gaps that hurt correctness

### 5.1 Evidence selector does not narrow generation context (P0)

- **Files:** `retrieval/evidence_selector.py`, `retrieval/stages.py`, `agent/nodes/linear.py` (`evidence_node`, `generate_node`), `generation/context_builder.py`
- **Gap:** Flag default off. When on, `evidence_set` is computed/stored, but `evidence_node` is a pass-through and `generate_node` still feeds **full** `chunks` into `run_generation_pipeline`. ContextBuilder ranks/truncates by score, not by selected evidence types.
- **Why it hurts:** Irrelevant high-scoring chunks dominate the prompt → definition-anchoring, diluted citations, wrong primary provision.

### 5.2 Missing statute corpus — MEASURED, and much smaller than assumed (P2, not P0)

- **Status: gap measured at 1.6%, downgraded from P0 to P2.** `evaluation/corpus_gap_scope.py` against the indexed corpus (27,351 payloads) and the 150 benchmark questions: **147 covered, 3 not in corpus (2.0%)**, and **4 of 248 gold units missing (1.6%)**.
- **The gap is one instrument, not four.** All three affected questions (Q033, Q034, Q042) are the **Prevention of Cruelty to Animals Rules, 2017**. The acts named in the original draft of this document — Water Act, WB Meat Order, KMC water rules, PCA schedules — are **not** missing from the corpus.
- **Why it matters less than assumed:** a 1.6% unit gap cannot explain the ~89 `model_wrong` cases. Corpus absence was inferred from failure volume without being measured; measurement contradicts the inference.
- **Still worth doing**, and cheap (`ingest_corpus_task` and `LegalParagraphChunker` already exist) — but it is procurement, not a correctness lever.

### 5.3 Soft metric ≠ legal correctness (P0)

- **Status: audit COMPLETE (150/150, 0 blank).** `evaluation/out/ceiling_v5/full_review_tabulation.md`. Verdicts: `model_wrong` 89 (59.3%), `evaluator_miss` 57 (38.0%), `reference_narrow` 4 (2.7%). Model-side targets from the audit: `abstention_gate` 31, `provision_check` 35, `needs_deeper_reasoning` 23.
- **Gap:** Soft score is token-overlap / Jaccard-style. The mechanically computed binary correctness (~9–12%) is far below human-adjudicated accuracy (~61%) because the grader misses valid answers — 38 misses remain unresolved as evaluator-v3 candidates.
- **Why it hurts:** Optimizing soft alone can reward lexical mimicry and punish legally acceptable rewrites (auditor Exp D showed soft drops from reference drift). Equally, **trusting the mechanical binary number overstates model failure by ~50pp** and will misdirect effort toward verification work that the audit does not support.
- **What remains:** Fold binary correctness into the reported scorecard alongside soft / citation p-r / groundedness (§9). Per-category labeling machinery already exists in `evaluation/answer_error_taxonomy.py` with a `manual_labels` human-override path; it needs surfacing in reports, not rebuilding.

### 5.4 Agent self-correction off by default (P1)

- **Files:** `agent/routes.py`, `agent/graph.py`
- **Gap:** `RAG_USE_AGENT_PIPELINE=false` → main path has no claim-retry, citation-quality retry, DAG abstain, or HITL.
- **Why it hurts:** Quality loops exist in code but do not protect live answers.

### 5.5 Verification is fuzzy, not true entailment (P1)

- **Files:** `verification/claim_extractor.py`, `evidence_verifier.py`, `hallucination_detector.py`
- **Gap:** Sentence-split claims; section-number matches; rapidfuzz ≥70 counts as verified; empty-claims edge cases can look fully faithful.
- **Why it hurts:** Wrong polarity, wrong numbers, or missed exceptions can still “verify” and finalize.

### 5.6 Targeted retries are often placeholders (P1)

- **Files:** `planning/targeted_retry.py`, `agent/nodes/linear.py` (`targeted_retry_node`)
- **Gap:** Strategies such as `_target_collection` return the original query; identifier targeting may only append `" (identifier)"`.
- **Why it hurts:** Failures are classified but retries do not reliably fetch the missing provision class.

### 5.7 Subquery decomposition is shallow (P1)

- **Files:** `retrieval/subquery_decomposer.py`, `planning/query_planner.py`
- **Gap:** Decomposer mainly splits when ≥2 “Section N” + and/or. Richer EvidenceTask planner is mostly on the agent DAG path.
- **Why it hurts:** Multi-part non-section questions get one contaminated retrieval pass.

### 5.8 Structured reasoner + auditor gated off; prior soft gains small (P2)

- **Files:** `generation/structured_reasoner.py`, `agent/nodes/reasoning.py`, `agent/nodes/auditor.py`
- **Gap:** Defaults false. Reasoner is one LLM JSON pass with empty-skeleton fallback. Exp D: ~+1.3pp soft / +0.8pp binary; auditor soft declined from reference drift.
- **Why it hurts:** Default path jumps evidence → final answer with no systematic exception/condition IR — but enabling without better evidence packing + dual metrics can look like a regression.

### 5.9 KGReasoner implemented but unwired (P2)

- **Files:** `planning/kg_reasoner.py`, `agent/nodes/linear.py` (`kg_reason_node`, `reason_node`)
- **Gap:** Fusion/expansion injects related text; structured Cypher path scoring is **not** in `build_graph()`. `reason_node` is a chunk-count heuristic.
- **Why it hurts:** Multi-hop lineage and conflict reasoning stay incomplete; expansion can add text without structured selection.

### 5.10 Legal hierarchy underused at select/generate (P2)

- **Files:** `chunker.py`, `legal_sections.py`, `retrieval/legal_identity.py`, `parent_reconstruction.py`
- **Gap:** Hierarchy metadata is strong at ingest; generation mostly score-truncates. `RAG_CE_SECTION_PREFIX` default false.
- **Why it hurts:** Primary / definition / exception / temporal balance is not forced into the prompt window.

### 5.11 Advisor confidence heuristic (P3)

- **Files:** `advisor/confidence.py`, `advisor/selector.py`
- **Gap:** Flag off; `HeuristicConfidence` (~0.7 + anchors); isotonic fitting needs outcome labels.
- **Why it hurts:** Confidence can look probabilistic without calibration → overtrust when eventually enabled.

### 5.12 Hybrid weights / server BM25 (P3)

- **Files:** `retrieval/hybrid_retriever.py`, `sparse_retriever.py`
- **Gap:** `dense_weight` / `sparse_weight` ignored (pure RRF). `RAG_QDRANT_BM25` default false; sparse may fall back to rapidfuzz.
- **Why it hurts:** Lexical misses when sparse vectors absent; less tunable for identifier-heavy legal corpora.

---

## 6. Prioritized improvement plan & technical specifications

### P0 — Do first (highest leverage)

| # | Improvement | Key files | Action & Technical Specification | Effort | Expected impact |
|---|---|---|---|---|---|
| 1 | Wire `evidence_set` into generation context | `app/rag/generation/context_builder.py`, `app/rag/agent/nodes/linear.py`, `app/rag/tasks.py`, `app/rag/generation/grounded_service.py` | **Context packing filter:**<br>• Update `ContextBuilder.build(query, chunks, query_type, evidence_set=None)` to accept the selected evidence set.<br>• **Type is a serialized `dict`, not an `EvidenceSet` object** — `_enrich_evidence_set` (`retrieval/stages.py`) calls `.to_dict()`, and `AgentState.evidence_set` is typed as a dict.<br>• Pack provisions by legal role order: Primary $\to$ Exceptions $\to$ Definitions $\to$ Penalties $\to$ Cross-References. Retain unselected chunks as overflow only.<br>• **Reuse `_EVIDENCE_TYPE_PRIORITY`** (`retrieval/evidence_selector.py:341`) rather than defining a second order, so the two cannot drift.<br>• Forward `evidence_set` from `run_retrieval_pipeline` / `state["evidence_set"]` into `run_generation_pipeline` and `generate_node`. Note `GroundedGenerationService.generate()` also lacks the parameter — three layers, not one.<br>• **Keep `_check_answerability` on the full pool, before packing.** It early-returns `enough_evidence=False`; filtering first would shrink the pool it judges and could reject queries that pass today.<br>• Annotate `<document>` tags with `role="{evidence_type}"` to prevent definition-anchoring.<br>• **Land behind `ENABLE_EVIDENCE_SELECTOR` (default false).** This changes every live prompt when on.<br>• **Write characterization tests for the `_check_answerability` early-return first** — it currently has no direct coverage, so there is no regression guard for the interaction above. | Low | **Highest win:** converts high retrieval recall into relevant LLM context without diluting primary provisions. Targets the largest model-side audit bucket (`provision_check`, n=35). |
| 2 | Fill missing statute corpus (**DOWNGRADED P0→P2**) | `ingestion/`, `tasks.py` (`ingest_corpus_task`), Qdrant index | **Measured scope:** the gap is **4 of 248 gold units (1.6%)**, 3 of 150 questions, and entirely the **Prevention of Cruelty to Animals Rules, 2017** — not the four acts named in the original draft.<br>• Ingest the PCRA Rules 2017 with `LegalParagraphChunker` + act/section metadata.<br>• Re-run `evaluation/corpus_gap_scope.py` to confirm the rate reaches 0.<br>• Do **not** budget further procurement against this row: it cannot move the ~89 `model_wrong` cases. | Low | Removes 3 benchmark questions from artificial abstention. Much smaller than the original "eliminates hallucinations" claim. |
| 3 | Dual-metric reporting (audit already done) | `evaluation/eval_e2e_v2.py`, `evaluation/grading.py`, `evaluation/answer_error_taxonomy.py` | **Reporting, not new measurement:**<br>• Audit is complete (150/150) — see §5.3. Do **not** re-run it.<br>• Emit dual scorecards: `binary_correctness` alongside `soft_jaccard_score`, `citation_p/r`, `groundedness`, always together (§8.5).<br>• Triage the 38 unresolved `evaluator_miss` cases into an evaluator-v3 overlay; 19 were already fixed by v2.<br>• Re-baseline the binary target against adjudicated ~61%, not the mechanical 9–12%. | Low | Stops misreading evaluator error as model error; makes P0-1's measured delta trustworthy. |

### P1 — Close quality & verification loops

| # | Improvement | Key files | Action & Technical Specification | Effort | Expected impact |
|---|---|---|---|---|---|
| 4 | Enable agent pipeline in staging | `shared/config.py`, `agent/graph.py`, `agent/thresholds.py` | **Agent activation:**<br>• Set `RAG_USE_AGENT_PIPELINE=true` in staging/benchmark configurations.<br>• Enforce strict thresholds: `CLAIM_GROUNDEDNESS_THRESHOLD` (0.70) and `citation_quality_ok` gate retries prior to `finalize`. | Low | Activates self-correcting retry and verification loops on live query paths. |
| 5 | Strengthen claim verification & fix empty-claim loophole | `verification/scorer.py`, `verification/evidence_verifier.py`, `verification/hallucination_detector.py` | **Verifier hardening:**<br>• Fix `GroundednessScorer`: if `response_text` is non-empty but claims list is empty, score neutral/flagged (0.50) instead of default 1.0.<br>• In `EvidenceVerifier`, enforce exact section number match + prohibition/permission polarity alignment (`_PROHIBITION_RE` vs `_PERMISSION_RE`) and numerical exactness (`_AMOUNT_RE`, `_PERCENT_RE`) instead of relying solely on rapidfuzz $\ge 70$. | Med | Stops false-positive verifications and prevents ungrounded answers from passing. |
| 6 | Replace placeholder targeted retries | `planning/targeted_retry.py`, `agent/nodes/linear.py` (`targeted_retry_node`) | **Actionable retry strategies:**<br>• Replace string appending (`" (identifier)"`) with real query builders:<br>  - `_target_definition`: generate `"{term} means" OR "definition of {term}"`<br>  - `_target_identifier`: generate exact statutory lexical query `"{Act} Section {N}"`<br>  - `_target_hierarchy`/`_target_exception`: query adjacent provisions and exception clauses (`"Section {N} exception proviso notwithstanding"`).<br>• Direct retries to specific retrieval arms (sparse identifier route, definition search, or KG expansion). | Med | Ensures retry passes actually fetch missing evidence classes rather than repeating the same failed query. |
| 7 | Deeper multi-part decomposition | `retrieval/subquery_decomposer.py`, `planning/query_planner.py`, `tasks.py` | **Compound query planning:**<br>• Extend `subquery_decomposer` beyond multiple "Section N" patterns to handle compound factual/legal queries.<br>• Unify `EvidenceTask` planner across linear and agent paths; execute per-task retrieval and merge results through `EvidenceSelector`. | Med | Provides complete coverage for multi-part legal questions without cross-topic context dilution. |

### P2 — Reasoning quality (after evidence is clean)

| # | Improvement | Key files | Action & Technical Specification | Effort | Expected impact |
|---|---|---|---|---|---|
| 8 | Structured reasoner + legal auditor | `generation/structured_reasoner.py`, `agent/nodes/reasoning.py`, `agent/nodes/auditor.py` | **IR reasoning architecture:**<br>• Enable `ENABLE_STRUCTURED_REASONER=true` (and `ENABLE_LEGAL_AUDITOR=true`).<br>• Reasoner generates structured IR: $\text{Issue} \to \text{Rule} \to \text{Exceptions/Conditions} \to \text{Application} \to \text{Conclusion}$.<br>• Auditor checks whether statutory exceptions and conditional provisos were addressed before finalizing the answer. | Med | Prevents jumping directly from raw evidence to answers; systematically handles legal exceptions. |
| 9 | Wire `KGReasoner` into compiled graph | `planning/kg_reasoner.py`, `agent/graph.py`, `agent/nodes/linear.py` | **Structured graph reasoning:**<br>• Add `builder.add_node("kg_reason", nodes.kg_reason_node)` to `StateGraph`.<br>• Route `cross_reference` and `multi_hop` queries through `kg_reason` before retrieval.<br>• Gracefully degrade when Neo4j is offline, falling back to hybrid vector search. | Med | Enables true multi-hop statutory lineage and cross-reference traversal. |
| 10 | Hierarchy-aware context packing & section prefix | `retrieval/legal_identity.py`, `retrieval/reranker.py`, `shared/config.py` | **Hierarchical signal injection:**<br>• Set `RAG_CE_SECTION_PREFIX=true` so Cross-Encoder receives `"[Act Name \| Section N] Chunk Text"`.<br>• Balance prompt context window across primary, definition, and exception chunk tiers. | Low | Reduces Cross-Encoder confusion across similarly numbered sections in different statutes. |

### P3 — Later polish & calibration

| # | Improvement | Key files | Action & Technical Specification | Effort | Expected impact |
|---|---|---|---|---|---|
| 11 | Calibrate advisor confidence | `advisor/confidence.py`, `advisor/selector.py` | **Confidence calibration:**<br>• Collect human approve/reject audit labels from staging runs.<br>• Fit an isotonic regression calibrator over raw heuristic confidence scores. | High | Produces well-calibrated confidence scores for trustworthy automated escalation. |
| 12 | Server-side BM25 | `retrieval/hybrid_retriever.py`, `sparse_retriever.py`, `shared/config.py` | **Lexical index upgrade:**<br>• Enable `RAG_QDRANT_BM25=true` where Qdrant cluster supports native BM25 sparse vectors. | Low | Replaces client-side rapidfuzz fallback with true inverted-index BM25 scoring. |

---

## 7. Suggested rollout

### Phase 1 (Immediate / Week 1) — Close the Evidence Loop (Use What Exists)
1. **ContextBuilder Evidence Integration:** Modify `ContextBuilder.build()` and `generate_node` to filter/order prompt chunks by the selected evidence set. See §6 item 1 for the dict type, the answerability-ordering constraint, and the required characterization tests.
2. **Section Prefix for Cross-Encoder:** Enable `RAG_CE_SECTION_PREFIX=true` in `config.py`.
3. **Empty-Claims Verifier Guard:** Patch `GroundednessScorer` to score empty claims on non-empty responses conservatively.
4. **Staging Agent Pipeline:** Enable `RAG_USE_AGENT_PIPELINE=true` in staging environments.

### Phase 2 (Weeks 2–3) — Recovery & Honest Evaluation Ceiling
1. **Targeted Retry Hardening:** Implement failure-specific query generators in `TargetedRetryPlanner` (`_target_definition`, `_target_identifier`, `_target_exception`).
2. **Corpus Ingestion (deferred, P2):** the measured gap is 1.6% and confined to the Prevention of Cruelty to Animals Rules, 2017 — not the acts originally listed. See §5.2.
3. **Dual Metric Evaluation & 50-Answer Human Audit:** Run benchmark measuring binary correctness alongside soft metrics; classify residual failures.

### Phase 3 (Weeks 4+) — Reasoning Quality & Knowledge Graph
1. **Structured Reasoner Activation:** Enable `ENABLE_STRUCTURED_REASONER` with binary eval tracking.
2. **KGReasoner Graph Integration:** Wire `kg_reason_node` in `app/rag/agent/graph.py` for cross-reference / multi-hop query routing.
3. **Confidence Calibration:** Fit isotonic calibration model for FSO Advisor using collected audit outcomes.

---

## 8. What not to do first

1. **Do not** chase K → 200–500 without evidence selection (more chunks only dilute the prompt without selective packing).
2. **Do not** treat O3 ≈ 37% soft as a universal LLM ceiling (soft Jaccard is heavily distorted by lexical phrasing).
3. **Do not** enable the auditor and judge wins only on soft Jaccard (Exp D proved auditor gains appear as soft drops due to reference drift).
4. **Do not** train contrastive generation models before corpus fill and evidence packing are fixed.
5. **Do not** optimize soft overlap alone — always report **binary + soft + citation + groundedness**, and human labels for high-stakes claims.

---

## 9. Success metrics (how to know it worked)

Track these together, never soft alone:

| Metric | Role | Target Direction |
|---|---|---|
| **Binary answer correctness** | Primary hard signal (LLM Judge / Gold Answer) | Baseline is **~61% adjudicated**, not 9–12% (§5.3). Track both. |
| **Human legal correctness** | Ground truth on audited sample | $\ge 85\%$ on in-corpus queries |
| **Soft token-overlap (Jaccard)** | Secondary signal; monitor for reference drift | Stable or rising ($\ge 45\%$) |
| **Citation precision / recall** | Evidence attribution quality | $\ge 0.85 / \ge 0.85$ |
| **Groundedness / claim_groundedness** | Hallucination prevention | $\ge 0.90$ |
| **Evidence-set P/R/F1** | Packing & selection health | $\ge 0.80$ |
| **Evidence-missing rate** | Corpus completeness | **Already 2.0%** (3/150 questions, 4/248 gold units) — target 0% |
| **Abstention precision** | Correct refusal when evidence is absent | $\ge 90\%$ |

---

## 10. References

- `Legal_RAG_Answer_Correctness_Improvement_Roadmap.md`
- `docs/RAG_IMPLEMENTATION.md`
- `docs/RAG_UPGRADE_RESEARCH.md`
- `docs/RAG_EFFECTIVENESS_EVALUATION.md`
- `docs/rag_entity_intent_architecture_assessment.md`
- `docs/rag_entity_intent_failure_analysis.md`
- ADRs: `docs/adr/0002-*` (retrieval cache), `0003/0006/0007/0008` (advisor), `0009` (provision extraction)

---

## 11. Next implementation candidates & technical details

If implementing immediately after this analysis, execute in this exact sequence:

```
┌─────────────────────────────────────────────────────────────┐
│ Step 1: P0-1 EvidenceSet Context Packing Filter              │
│ files: app/rag/generation/context_builder.py                │
│        app/rag/agent/nodes/linear.py                        │
│        app/rag/tasks.py                                     │
├─────────────────────────────────────────────────────────────┤
│ Step 2: P1-5 Verifier Empty-Claims & Polarity Guard         │
│ files: app/rag/verification/scorer.py                       │
│        app/rag/verification/evidence_verifier.py            │
├─────────────────────────────────────────────────────────────┤
│ Step 3: P1-6 Actionable Targeted Retries                     │
│ files: app/rag/planning/targeted_retry.py                   │
│        app/rag/agent/nodes/linear.py                        │
├─────────────────────────────────────────────────────────────┤
│ Step 4: P1-4 Agent Pipeline Staging Activation              │
│ files: app/shared/config.py                                 │
│        app/rag/agent/graph.py                               │
└─────────────────────────────────────────────────────────────┘
```

---

*Updated from the 2026-10-04 RAG module audit with concrete codebase specifications. Re-evaluate metrics after P0/P1 deliverables land.*

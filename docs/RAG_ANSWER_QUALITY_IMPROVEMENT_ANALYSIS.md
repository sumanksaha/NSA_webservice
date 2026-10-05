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

**Core diagnosis:** As retrieval availability rises (K=1→100: recall ~29%→88%), answer correctness barely moves (~33%→38%). Even oracle gold/full-support context stays near ~37% soft.

**The knowledge graph was inert, and wiring it up the obvious way makes things worse.** `provisions_for_query` returned provisions for only **5 of 30** benchmark questions. Three silent bugs in `kg/queries.py`, each wrapped in `try/except` that only logs a warning, so the pipeline served vector-only results while appearing to have a graph:

1. Concept keys did not match the graph's `LegalConcept.name` values — `FoodBusiness` vs `'Food Business'`, `SolidWaste` vs `'Solid Waste'`, `Pollution` vs `'Environmental Pollution'`. Concept traversal is an equality match, so **7 of 13 keys matched nothing**.
2. The traversal matched only `APPLIES_TO|RELATES_TO|REQUIRES` — **3 of the 15** provision→concept edges that exist. `IMPOSES_DUTY` (2,033 edges), `PRESCRIBES_PENALTY` (645), `PROHIBITS` and `DEFINES` were invisible.
3. The full-text fallback matched the **entire question** as a literal `CONTAINS` substring. No provision can contain a full English question: the whole-question match returns 0 provisions, the single word `punishment` returns 75.

After the fix, **30/30** questions return provisions (~4.9 per query). But enabling fusion then **degrades** the pipeline (`evaluation/ab_kg_fusion.py`, 150 paired questions, live LLM, `poolside/laguna-s-2.1:free`):

| metric | KG off | KG on | delta | paired t |
|---|---|---|---|---|
| binary_correct | 0.1200 | 0.1267 | +0.0067 | +0.45 |
| answer_correctness | 0.3740 | 0.3729 | −0.0011 | −0.33 |
| citation_recall | 0.3099 | 0.1035 | **−0.2064** | **−6.96** |
| groundedness_score | 0.8600 | 0.7867 | −0.0733 | −2.07 |

Gold evidence is lost on **18** questions and gained on only **5**. The cause is in `kg/hybrid.rrf_fuse_chunks`: fusion truncates the candidate pool to `top_k = slot_budget` (~20), while the vector pool holds ~500 chunks. Each KG item enters at rank 1–5 of its own list and therefore outranks every vector chunk beyond rank ~20 by construction — the docstring claims KG "never lets a KG item displace an equally-ranked vector item", but that is true only within the fused top-k, not against the vector list being cut from 500 to 20. Correctness stays flat because the lost gold evidence costs about what the added KG evidence gains.

**Two of the causes were self-inflicted and are now fixed.** The concept-key rewrite made one provision reachable through several of the query's concepts, so `provisions_for_query` emitted it once per concept — **27/40** benchmark questions had duplicate KG chunk_ids, and the `SUPPORTED_BY` fan-out in `search_provisions` emitted one row per supporting chunk edge. Dedupe by `provision_id` plus a `collect`/`head` aggregation in the Cypher took duplicates to **0/40**. `rrf_fuse_chunks` also gained an opt-in `reserve_kg_slots` parameter that confines the KG list to reserved tail slots instead of letting it compete for a truncated 500-chunk pool (default `0` preserves existing behaviour).

**The re-run A/B with those fixes (same 150 questions) still shows fusion subtracting:**

| metric | KG off | KG on | delta |
|---|---|---|---|
| binary_correct | 0.1400 | 0.1333 | −0.0067 |
| answer_correctness | 0.1519 | 0.1467 | −0.0052 |
| citation_recall | 0.1351 | 0.0369 | **−0.0982** |
| groundedness_score | 0.3400 | 0.3333 | −0.0067 |
| n_prompt_chunks | 18.87 | 19.20 | +0.33 |

Gold lost fell 18 → **9** and gold gained rose 5 → **3** at `mean KG provisions/query 4.57`, `KG injected 150/150`, so the dedupe recovered part of the loss but did not change the sign. A reserve-slot sweep over 50 questions at `top_k=20` (vector-only gold@20 = 203) found **no** reserve value that recovers gold: reserve 0 → −26, 1 → −8, 2 → −16, 3 → −22, 5 → −40.

**The ceiling is KG payload quality, not fusion policy.** The text the graph contributes is a *pointer*, not evidence: median injected chunk length is **85 characters**, 108/116 are under 150 characters, and **87/91** chunks only restate their own section reference ("PROVISION 31 — Section 31 Instrument: Food Safety and Standards Act 2006 Authority: Food Safety Officer"). The provision *body* was never ingested — the graph has 3,784 `LegalProvision` nodes whose `text` is a reference line, while the 34,439 `Chunk` nodes hold the real text. KG chunk ids also live in the `KG:*` namespace, so they can never equal a vector gold chunk_id and the gold-loss accounting above is structurally lopsided against the KG.

**So the KG is not a missed lever — it is a correctly-wired lever carrying the wrong payload.** Fixing fusion policy alone cannot help: the graph can point at the right provision but cannot quote it. The work item is ingesting provision text into `LegalProvision.text` (or resolving `SUPPORTED_BY` chunk bodies into the injected chunk), after which the fusion policy question becomes worth revisiting.

> **⚠️ The citation-dependent figures in both tables above are now superseded.** `CitationTracker` matched only `[n]` and silently dropped the `[Source n]` form, even though `ContextBuilder` renders every prompt chunk as `<source>[Source {idx}] …</source>`. That is the form the model is actually shown. Every `citation_recall`, `groundedness_score` and gold-gained/lost number above is therefore **under-reported** and was computed with a known bug. Only `binary_correct`, `answer_correctness`, `hallucination_detected`, `n_invalid_citations` and `n_prompt_chunks` are unaffected. Re-measurement is pending (LLM quota exhausted 2026-10-05); see "Re-measurement" below for how to complete it without new quota.

**Fusion is now off by default.** `.env` had `RAG_KG_FUSION=true` and the `config.py` default was also `true`, so the measured-harmful path was live in production. Both are now `false` (`kg_fusion` and `kg_expansion`), so the default configuration is vector-only. This is safe to revert once the KG carries real provision text.

**Measured split of the residual failures** (`evaluation/failure_attribution.py`, over the 89 `model_wrong` questions, measured by asking `ContextBuilder` what it actually admits):

- **Before the window fix:** 36 (40.4%) were evidence-starved (gold in the pool but not in the prompt), 50 (56.2%) had gold in the prompt and still answered wrong, 3 (3.4%) never retrieved gold at all.
- **After the window fix (shipped):** evidence-starved falls to **26 (29.2%)** and gold-in-prompt rises to **60 (67.4%)**.

So the remaining gap is **not** purely interpretation and application, as originally stated here — a substantial part of it was evidence that was retrievable and never surfaced. That is also why the P0-1 prompt-packing A/B came back flat: reordering a window that is missing the gold provision cannot help.

**Evidence presence did not translate into answer quality.** The live-LLM A/B on the widened window (`evaluation/ab_window_width.py`, 89 paired `model_wrong` questions, `poolside/laguna-s-2.1:free`, run three times) is **flat on correctness in every run**: binary delta +0.011 / −0.011 / +0.023 (paired |t| ≤ 1.42, 0–2 improved, never a regression), soft correctness within ±0.025 of zero. On the **7 questions the fix actually moved** from gold-in-prompt 0→1, binary was **0.0000 → 0.0000 in all three runs**. The model was handed the gold provision and still answered wrong on every one, every time.

What the window *did* buy is **citations**: gold evidence reachable in the prompt +0.026 (deterministic, a property of the window), and gold actually cited by the model +0.295 on the causal subset. Mean prompt chunks rose 14.9 → 18.9 (+27%), not the ~2x the char budget implies.

**Groundedness shows no measurable harm, but is too noisy to show benefit.** Run through the shipped `CitationTracker` + `ResponseSanitizer`: hallucination flag 13.5% → 10.1% in one run and 14.6% → 21.4% in another (paired |t| = 1.4 both times, sign flips), groundedness +0.034 then −0.067. The per-question crosstab (6 narrow-only vs 12 wide-only flips, 7 both) is what run-to-run variance looks like, not a trend. **Invalid citations were 0 in both arms of every run**, so the wider window did not make the model cite things it was never shown.

So the window is kept on the citation gain at modest cost. But the bottleneck it was built for is not the one limiting these answers: **60 of 89 failures have gold in the prompt** and are lost downstream in generation.

The other driver is a **soft Jaccard metric** that is a weak proxy for legal correctness.

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

1. ~~**Do not** chase K → 200–500 blindly.~~ **Acted on; the dilution check is done and came back negative.** Measured: 36 of 89 `model_wrong` questions had gold below the prompt window, so the window was raised (per-type budgets are now caps under `RAG_CONTEXT_MAX_CHUNKS`/`_CHARS` instead of overriding them) and evidence-starved failures dropped to 26. Three live-LLM A/B runs put correctness flat (binary |delta| ≤ 0.023, paired |t| ≤ 1.42; 0/7 on the causal subset every time) and citations up, at +27% prompt chunks. Groundedness is indistinguishable from noise and invalid citations stayed at 0. Kept for the citation gain; the correctness gap is downstream in generation, so **more context is not the lever**.
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

**Do not enable `RAG_KG_FUSION` yet.** The fusion path is now correct but the graph carries no quotable text (§1): re-run `evaluation/ab_kg_fusion.py` only after `LegalProvision.text` holds real provision bodies or the injected chunk resolves `SUPPORTED_BY` chunk text. Note the cost figure quoted in §1 is itself pre-fix and under-reported; the direction (fusion costs citations, gains no correctness) is what matters.

**Re-measurement (no LLM quota required).** `ab_kg_fusion.py` shards now persist `answer`, `gold_chunk_ids` and `pool_chunk_ids`, and gained `--rescore`, which re-derives `citation_recall` / `gold_in_prompt` from the stored answers through the current `CitationTracker` and reprints the report. `python -m evaluation.ab_kg_fusion --rescore <shards…>` therefore re-measures the tracker fix offline. Verified on a synthetic shard: a `[Source 2]` marker that the old tracker dropped moved `citation_recall` 0.0 → 0.5. The 150-question shards written before this change lack the stored answers and cannot be rescored — those rows need a fresh run when quota is available.

---

## 11b. LangGraph agent pipeline: the KG node was dead three times over

`RAG_USE_AGENT_PIPELINE=false`, so the agent graph was not serving traffic — but it was also non-functional, in three independent ways, each of which alone would have made it a no-op. All three are fixed; the flag stays `false` pending an A/B of the agent path against the linear pipeline.

1. **The node was never registered.** `kg_reason_node` was implemented in `app/rag/agent/nodes/linear.py:497` and exported from `nodes/__init__.py`, but `app/rag/agent/graph.py` contained zero references to it. It is now registered between `plan` and retrieval (`plan → kg_reason → {retrieve, multi_hop_retrieve, plan_tasks}`). It no-ops when the active profile sets `kg_reasoning_enabled=false` (the `fast` profile), so the cost is zero where it should be.
2. **Its state keys were undeclared.** LangGraph drops any key a node returns that the state schema does not declare — verified directly: a node returning an undeclared key yields an output dict without it. `kg_paths` / `kg_cypher` were absent from `RAGState` while `targeted_retry` reads `kg_paths`. Both are now declared, and a compiled-graph run confirms 9 paths survive the state round-trip.
3. **The traversal queried a schema the graph does not have.** This is the substantive one. `kg_reasoner.py` was written against `Section` / `Penalty` / `Exception` / `Temporal` nodes and `HAS_AUTHORITY` / `HAS_PENALTY` / `HAS_EXCEPTION` / `HAS_CROSS_REFERENCES` / `TEMPORAL_VALIDITY` edges. The live graph has **none** of those labels or edges. It also minted provision ids as `FSSA::31`, a namespace matching no node.

Measured against the live graph before the fix: `reason_from_query` returned **0 paths for 4/4** section-bearing queries, while recording a clean-looking no-op audit entry. After the fix — real labels/edges, ids resolved by querying `provision_number` with the query's Act as a disambiguating hint — the same queries resolve to exactly the right provision:

| query | resolved provision_id | paths |
|---|---|---|
| punishment under Section 31 of the FSS Act 2006 | `FSS_ACT_2006_SEC_31` | 9 |
| exceptions to section 16 of the FSS Act | `FSS_ACT_2006_SEC_16` | 2 |
| Section 1 of the IPC | `IPC_1860_SEC_1` | 4 |
| Section 7 of the Consumer Protection Act | `CONSUMER_PROTECTION_ACT_2019_SEC_7` | 5 |
| Section 3 of the Air Act | `AIR_ACT_1981_SEC_3` | 5 |

Paths are now backed by real edges (`IMPOSES_DUTY → Obligation`, `GRANTS_PERMISSION → Permission`) rather than by keyword matches on text. Note this is a *reasoning/targeting* improvement, not a payload one: it does not put provision text in the prompt, so it does not by itself move answer quality. `IPC` section 59 and 304 still resolve to nothing — those provisions are genuinely absent from the graph, which is a corpus gap, not a code bug.

Also: there is **no LangChain** in this project. The dependency is `langgraph>=1.0.0`, imported lazily inside `build_graph` so the module still imports without it.

---

*Updated from the 2026-10-04 RAG module audit with concrete codebase specifications. Re-evaluate metrics after P0/P1 deliverables land.*

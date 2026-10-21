# RAG — FSSAI-Primary Multi-Domain Waste-Disposal Analysis

**Date:** 2026-10-10
**Author:** engineering review of `app/rag`, `app/auditor`, `benchmark/`, `docs/`
**Goal:** Determine what is necessary to answer FBO violation questions such as
*"A food business operator is improperly discarding solid waste"* with:
(1) how solid waste **must** be discarded under FSSAI law (primary focus),
(2) how it must be discarded under SWM/municipality law (secondary focus),
(3) the **penalty**, and (4) **how it must be corrected**.

---

## 1. Executive verdict

**The stack is 60-70% ready. The missing piece is a production multi-domain
query router with FSSAI-first priority fusion; the rest exists and can be
assembled:**

| # | Capability | Status | Location |
|---|------------|--------|----------|
| 1 | Per-domain Qdrant collections (FSSAI primary + env/SWM, wb_state/KMC, etc.) | ✅ Done | `app/rag/collections.py`, `other domain/manifest.json` |
| 2 | FSS corpus covering FSS Act §§26, 32, 55, 58, 59 (the FBO-hygiene-penalty spine) | ✅ Done | `fssai_legal_768` |
| 3 | SWM Rules 2026 (FBO segregation duty + no-open-dumping) in env corpus | ✅ Done (1 provision) | `env_legal_768`, `swm_rules:segregation` |
| 4 | KMC Act municipal provisions (nuisance, s172/s196/s313, trade licence) | ✅ Done | `wb_state_legal_768` |
| 5 | Generation with penalty-structure directive + evidence-type context packing | ✅ Done | `prompt_template.py`, `context_builder.py` |
| 6 | §32 Improvement-Notice -> §55 penalty -> prosecution **Act selection** | ✅ Done | `app/rag/advisor/`, `selector.py` |
| 7 | **FBO waste-disposal provision tagged as `primary_provision` in FSS corpus** | ⚠️ Partly — only instrument-level (`fssai:regs/licensing`) | Gap #1 |
| 8 | **Production query router -> fuse FSSAI(primary) + env/KMC(secondary)** | ❌ Missing | **Gap #2** |
| 9 | Structured 4-part answer (obligation->penalty->correction) + extraction | ⚠️ Partial — prompt has penalty directive, no 4-part schema | Gap #3 |
| 10 | **Benchmark corpus of FSSAI-primary / other-law-secondary questions** | ❌ Missing | **Gap #4** |

**Consequence today:** a production query "FBO improperly discarding solid
waste" is routed to the **default single collection** (`fssai_legal_768`). It
retrieves FSS §§26/32/55/58/59 but **misses the SWM Rules 2026 segregation
duty** (`swm_rules:segregation` lives in `env_legal_768`), so the answer is
incomplete: it tells the FSO the penalty and the correction but not *how the
waste should be discarded* under the primary FSS obligation + SWM overlay.
## 2. The legal answer (for the example question)

Grounded on the corpus + existing benchmark Q057 (`acceptable_conclusion`:
*"The FBO must segregate waste at source into biodegradable/recyclable/dry and
hand it over only to the municipal door-to-door collector; open dumping is
prohibited."*):

### 2.1 Primary obligation - FSSAI law
- **FSS Act §26** - "Responsibilities of the food business operator": the FBO
  must ensure food is not unfit for human consumption and not injurious to
  health at every stage (manufacture, storage, distribution, sale). Improper
  solid-waste handling is a breach of this hygiene obligation.
- **FSS (Licensing and Registration of Food Businesses) Regulations** -
  hygiene/sanitation/waste-disposal requirements under Schedule 4 (corpus has
  this at instrument level via `fssai:regs/licensing`, `fssai:food_regulations`;
  needs provision-level expansion - see Gap #1).
- **FSS Act §21** - general provisions for food business hygiene (gap: verify
  presence in corpus).

### 2.2 Secondary obligation - SWM + municipality law
- **Solid Waste Management Rules, 2026** (`env_legal_768`): FBO **segregates
  at source into biodegradable / recyclable / dry domestic waste**, stores in
  covered bins, and hands over **only to the municipal door-to-door collector**.
  Open dumping / littering is prohibited.
- **Kolkata Municipal Corporation Act, 1980** (`wb_state_legal_768`): nuisance
  provisions (s172, s196, s313, s330, s391, s392), street sweeping and
  collection duties, and trade-licence conditions that make waste disposal a
  licensable obligation; fines for non-compliance.
### 2.3 Penalty
- **FSS Act §32** - Food Safety Officer serves an **Improvement Notice**
  directing the FBO to remedy the hygiene breach within a specified period
  (normally 15 days). This is the floor act for non-sample hygiene cases.
- **FSS Act §55** - penalty for failure to comply with the §32 direction: fine
  up to ₹2,00,000.
- **FSS Act §58** - general penalty where no specific provision applies: fine
  up to ₹5,00,000.
- **FSS Act §§59-61** - if the mismanaged waste adulterates/renders food unsafe
  (unsafe/sub-standard/misbranded food): punishment of imprisonment + fine.
- **KMC Act** - nuisance/municipal fines in addition to (not in lieu of) FSS
  penalties.
- **Bharatiya Nyaya Sanhita, 2023** (`criminal_legal_768`) - if public health
  is knowingly endangered, criminal liability attaches.

### 2.4 Correction
- **Step 1 - §32 Improvement Notice:** FSO specifies the breach, the corrective
  act, and the deadline (default 15 days); notice is served and logged.
- **Step 2 - CAPA plan (FBO Auditor Agent, `app/auditor/service.py`):** phased
  remediation - Immediate (0-48 h: stop dumping, provision covered bins,
  engage authorized collector), Corrective (3-8 d: train staff, restructure
  storage, rectify licence conditions), Preventive (9-14 d: monitoring logs,
  periodic self-audit, staff refresher).
- **Step 3 - dossier for FSO re-inspection:** photos, weight-billing receipts
  from the authorized collector, segregation-bin layout, training records.
- **Step 4 - closure:** FSO verifies; on satisfaction the §32 matter closes
  (`Corrective Measures Implemented` terminal state); on non-compliance the
  §55 penalty direction is issued.
## 3. What modifications are necessary

### Gap #1 - Corpus: add FSS waste-disposal provisions at provision level
**Where:** ingestion (`app/rag/ingestion.py`), chunking (`app/rag/chunker.py`),
gold registry (`benchmark/gold_provisions_v1.0.json`).

**Why:** the FSS corpus contains only instrument-level coverage for the
Licensing Regulations (`fssai:regs/licensing`, `fssai:food_regulations`). The
LLM must see the *specific* waste-disposal clauses to cite §26 + the hygiene
regulations as the **primary** obligation, not paraphrase.

**Changes:**
1. Expand `fssai:regs/licensing` into provision-level rows (hygiene Reg 3, waste
   disposal Reg 4, storage/transport) with `section` numbers, `act_name` =
   "FSS (Licensing and Registration of Food Businesses) Regulations, 2011",
   `collection` = `fssai_legal_768`, `domain` = `FOOD_SAFETY`.
2. Add provision IDs `fssai:waste_disposal` and `fssai:hygiene` mapping to those
   rows; update the gold registry and the question corpus.
3. Verify FSS Act §21 is present in `fssai_legal_768` (coverage check).
4. Tag every such chunk with `evidence_type` = `primary_provision` /
   `obligation` / `penalty_provision` so `context_builder.py` packs them first
   (already wired via `_PACK_ORDER`).
5. Verify `env_legal_768` contains the SWM 2026 `swm_rules:segregation` chunk
   (registry entry has `document_id=swm_rules_2026`); if chunk-level text is
   missing, re-ingest `other domain/SWM_2026.pdf`.

### Gap #2 - Production multi-domain router (FSSAI-first fusion)
**Where:** `app/rag/domain_router.py` (new), `app/rag/retrieval/stages.py`,
`app/rag/tasks.py` (`retrieve_task`/`run_retrieval_pipeline`),
`app/rag/agent/state.py` `collection_name` field.

**Why:** the current `retrieve_task(query, top_k, collection_name)` accepts one
collection; the eval harness handles the benchmark's `collections` list by
running per-collection and fusing offline. Production queries never do this.

**Design (mirrors existing seams):**
- `classify_domain(query) -> list[(domain, priority)]`: rule-based domain
  classifier seeded with an FBO-violation keyword taxonomy. Default priority:
  `fssai=1.0`, then environment (`waste`/`solid`/`plastic`/`pollution`),
  municipality (`KMC`/`municipal`/`Kolkata`/`municipality`), animal.
- `run_multi_collection_retrieval(query, collection_priorities, top_k)` -> runs
  `build_hybrid_retriever(collection)` per domain in priority order, fuses via
  **rank-based priority weighting** (FSSAI chunks get a boost to occupy the
  top slots), returns `chunks` with a `domain` tag.
- Add config flag `RAG_MULTI_DOMAIN_ROUTER=true` (Pattern A in `cfg`), default
  on (FSSAI-first is the platform default - `DEFAULT_DOMAIN = "fssai"`).
- The LangGraph `RAGState.collection_name` becomes a list; the agent graph
  calls the multi-collection route.

**Fallback:** if the router is disabled, queries degrade to the single default
collection (no silent cross-domain contamination), per
`MULTIDOMAIN_INTEGRATION.md` §7.5.
### Gap #3 - Structured 4-part answer + extraction
**Where:** `app/rag/generation/prompt_template.py`,
`app/rag/generation/grounded_service.py`, new
`app/rag/generation/structured_answer.py`, response payload.

**Contract (four sections the model must produce, each with [n] citations):**
1. **Primary FSSAI obligation** - "How the waste must be discarded under FSSAI
   law" (FSS Act §26 + Licensing Regs hygiene/waste), labeled **PRIMARY LAW**.
2. **Other laws** - "Solid Waste Management Rules 2026 / municipality law"
   (FBO segregation, no-open-dumping; KMC nuisance/trade-licence duties),
   labeled **SECONDARY LAW**.
3. **Penalty** - full structure per `prompt_template.py` directive: base fine,
   imprisonment, daily continuing fine, escalation on repeat conviction.
4. **Correction** - §32 Improvement-Notice steps + CAPA plan phases (0-48 h /
   3-8 d / 9-14 d) + FSO re-inspection dossier, with the FBO Auditor Agent
   `generate_remediation_plan()` as the structural reference.

**Extraction:** a post-hoc extractor populates the response's `structured_answer`
JSON so the UI/agent can surface "obligation", "penalty", "correction"
independently (mirrors `structured_argument`/`audit_result` state).

**Guardrail:** the primary section must never be answered from SWM/municipal
text when FSS text is retrieved; if FSS retrieval yields nothing, the model
must abstain on the primary section (the corpus-audit ceiling shows
evidence-missing rate should be 0% - see
RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md §9).

### Gap #4 - Benchmark corpus (FSSAI-primary / other-law-secondary)
**Where:** `benchmark/benchmark_fssai_multidomain_v1.0.jsonl` +
`benchmark/gold_provisions_fssai_multidomain_v1.0.json` +
`benchmark/gold_sources_fssai_multidomain_v1.0.json`, `evaluation/config.py`.

**Why:** the existing benchmark (v1.0, 150 questions) has only one FBO-waste
question - Q057 - and it is SWM-*only* (`domains: ENVIRONMENT_POLLUTION,
MUNICIPAL`, no FSSAI). There is no training/eval data for the FSSAI-primary
pattern, so Gap #2 and Gap #3 cannot be validated or tuned.

**Quantity & shape:** ~40 questions, split `dev/validation/holdout` (100/30/20
ratio), each with `question_type` list, `primary_provisions` /
`acceptable_alternatives` / `supporting_provisions` (FSSAI-primary first),
`domains` (FOOD_SAFETY + secondary), `collections`, `common_traps`,
`acceptable_conclusion`, `required_conditions`. Six scenario families: (a) FBO
waste-disposal hygiene, (b) food-waste storage/transport, (c) FSS penalties
(§32/§55/§58/§59/§63-64), (d) correction & §32 closure / CAPA, (e) municipal
overlay, (f) cross-domain priority (FSS vs SWM precedence).

**Validation:** run `evaluation/run_evaluation` arms against the new corpus;
target human-adjudicated correctness >=85% and retrieval recall@20 = 100% for
gold FSS chunks (per §9 of RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md).
## 4. How the "model" should be trained

**Design decision (repo-wide):** this stack does **not** fine-tune the
generative LLM. The generator is an off-the-shelf endpoint; invest training
budget in the retrievable corpus, the reranker, and the retrieval policy. The
existing evidence (RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md, §1): raising
retrieval recall barely moves soft answer correctness (~33%->38% from K=1->100),
so the lever is **corpus completeness + evidence packing + verification**, not
bigger models.

### 4.1 Corpus "training" (ingest + tag)
1. **Provision-level ingestion:** chunk FSS Licensing Regs (hygiene/waste) and
   SWM 2026 into `section`-stamped chunks (the `clause_number` payload index
   already exists per RAG_ENTITY_INTENT_DELIVERABLES §6.3); stamp `act_name`,
   `document_id`, `enactment_date`, `is_current`.
2. **Evidence-type tagging:** deterministic tagger classifies each chunk into
   `primary_provision`, `obligation`, `exception`, `definition`,
   `penalty_provision`, `cross_reference`, `authority` (reuse
   `context_builder.py._PACK_ORDER` + KG semantic edges
   `IMPOSES_DUTY`/`PRESCRIBES_PENALTY`/`PROHIBITS` - see `kg/enrichment.py`).
   This makes selection deterministic and reranker-supervised.
3. **Temporal validity:** stamp effective/sunset dates; `temporal_validity.py`
   gates superseded provisions (SWM 2016 superseded by 2026).

### 4.2 Reranker fine-tuning (the actual ML training)
- The cross-encoder reranker is already fine-tuned on legal query-chunk pairs
  (`evaluation/out/cache/ce_training_pairs.jsonl`). **New training pairs:**
  take the FSSAI-primary benchmark questions (section 3) as queries, mark gold
  FSS chunks (FSS primary) and SWM chunks (secondary) as relevant with
  `role=primary`/`role=secondary` gains (2.0 / 1.0 per the benchmark protocol),
  and hard negatives = chunks from the *wrong* domain that were retrieved
  historically.
- Train the CE on these pairs, then **ablate** (RAG-CE config arms) to confirm
  FSS chunks rank above secondary chunks for FBO-violation queries.
- Per the reranker analysis in `legal_query_classifier.py`: penalty-type queries
  respond well to hierarchy boosting - apply `PENALTY_CONFIG` for the penalty
  family.

### 4.3 Classifier seeding (no-LLM, deterministic)
- **Domain classifier** (`domain_router.py`): seed the FBO-violation keyword
  taxonomy (table below); rule-based, zero latency, mirrors
  `legal_query_classifier.py`'s conservative multi-match pattern.
- **Query-type expansion:** add `FBO_VIOLATION`, `VIOLATION_WITH_CORRECTION`,
  `PENALTY_AND_CORRECTION` to `LEGAL_QUERY_TYPES` and `QueryType`; wire into
  the reranker config `QUERY_TYPE_CONFIGS` and the planner in `planning/query_planner.py`.

| Keyword family | Keywords (compiled IGNORECASE) |
|---|---|
| FBO identity | `food business operator`, `FBO`, `food establishment`, `manufacturer`, `processor`, `seller`, `retailer`, `vendor` |
| Waste | `solid waste`, `waste`, `dumping`, `dump`, `disposal`, `dumped`, `discarding` |
| Hygiene | `hygiene`, `unsanitary`, `sanitation`, `dirty`, `filth`, `vermin`, `rodent`, `pest` |
| SWM | `solid waste management`, `segregation`, `SWM`, `recyclable`, `biodegradable`, `door-to-door` |
| Municipality | `KMC`, `municipal`, `Kolkata`, `municipality`, `municipal commissioner`, `ULB`, `urban local body` |
| Penalty | `penalty`, `fine`, `punishment`, `imprisonment`, `prosecution`, `offence`, `contravention` |
| Correction | `improvement notice`, `correct`, `comply`, `remedy`, `CAPA`, `remediation`, `closure` |
### 4.4 Retrieval policy (RL bandit) - optional enhancement
- `app/rag/rl/controller.py` + `RAGQueryLog` + `rag_rl_policy_log` are already
  in place. The RL layer picks `top_k`/`rrf_k` per query bucket. For the new
  query type, bucket = `FBO_VIOLATION`, `multi-domain`; the action space is
  identical (no code change), only the log accumulates (reward = groundedness +
  citation_recall from verification). **Ablate the RL layer against the new
  corpus before enabling**; the default (`rl_enabled=false`) is a safe,
  deterministic baseline.

### 4.5 Evaluation loop (human-in-the-loop, as the repo does)
1. Human-label the new benchmark on **binary correctness**, **soft overlap**,
   **citation recall/precision**, **groundedness** (target: >=0.90 groundedness,
   >=0.85 / >=0.85 citation P/R, >=85% human-correct on in-corpus queries).
2. Run paired A/B: **router off (single collection)** vs **router on
   (multi-collection FSSAI-first)** per question - the multi-domain ablation
   required by `MULTIDOMAIN_INTEGRATION.md` §7.5.
3. Track **evidence-missing rate** (target 0%) and **abstention precision**
   (>=90%): if FSS retrieval fails, the model must abstain on the primary
   section rather than answer from SWM text.
4. Retrain the CE reranker on failures (ranked-but-missing gold, wrong-domain
   ranking) and re-run. Iterate until targets are met - then freeze.

### 4.6 If an actual generative fine-tune is desired (out of scope, but documented)
Should the team later choose to fine-tune the generator, the corpus provides
the dataset: each benchmark question -> (`acceptable_conclusion` + cited
grounded answer) as instruction pairs, trained with the IRAC-style conditioning
(`PROMPT_CONDITIONING_IRAC_RESEARCH.md`). **Recommendation:** only do this
after gaps #1-#4 are closed and the retrieval + verification targets are met;
corpus/retrieval gains dominate on this dataset.

---

## 5. Files changed / created

| # | File | Change | Owner |
|---|------|--------|-------|
| 1 | `docs/RAG_FSSAI_PRIMARY_MULTI_DOMAIN_ANALYSIS.md` | This analysis, legal content, training plan | - |
| 2 | `app/rag/domain_router.py` (new) | Rule-based FSSAI-first multi-domain classifier + priority-fused retrieval | - |
| 3 | `app/rag/retrieval/stages.py` | New `multi_domain_stage` retrieval stage (feature-gated) | - |
| 4 | `app/rag/tasks.py` | `retrieve_task` accepts `collection_names: list[str] | None`; `run_retrieval_pipeline` supports fusion | - |
| 5 | `app/rag/agent/state.py` | `collection_name` -> list of domain priorities | - |
| 6 | `app/rag/generation/prompt_template.py` | 4-part structured answer directive (primary/secondary/penalty/correction) | - |
| 7 | `app/rag/generation/structured_answer.py` (new) | Answer builder + JSON extraction contract | - |
| 8 | `benchmark/benchmark_fssai_multidomain_v1.0.jsonl` (new) | ~40 FSSAI-primary / other-law-secondary questions | - |
| 9 | `benchmark/gold_provisions_fssai_multidomain_v1.0.json` (new) | Gold provision registry for the new corpus | - |
| 10 | `benchmark/gold_sources_fssai_multidomain_v1.0.json` (new) | Gold source-chunk mapping | - |
| 11 | `evaluation/config.py` | New benchmark config entry + eval arms | - |
| 12 | `tests/test_domain_router.py` (new) | Domain classification + fusion tests (mock Qdrant) | - |
| 13 | `FSSAI_rules documents/` | (if needed) ingest Licensing Regs hygiene/waste provision-level chunks | - |

---

## 6. Open questions / assumptions

1. **FSS Act §21 presence:** assumed present in `fssai_legal_768`; verify with a
   coverage audit before finalizing §2.1.
2. **SWM 2026 chunk text:** registry entry exists (`swm_rules_2026`); confirm the
   live Qdrant payload contains the text layer - re-ingest `other
   domain/SWM_2026.pdf` if a reindex run is required.
3. **LLM endpoint:** assumes the off-the-shelf endpoint (e.g. TEI/remote LLM) is
   configured; the CAPA/structured-answer steps use it. `AUDITOR_AI_ENABLED`
   gates the auditor.
4. **Qdrant availability for tests:** router tests use a mock store (no live
   Qdrant required); the eval harness already has its own live-cluster path.
---

## 7. Success metrics (mirrors §9 of RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md)

| Metric | Target |
|---|---|
| Retrieval recall@20 (gold FSS + SWM chunks) | 100% |
| Primary-section answer correctness (human) | >=85% |
| Soft token overlap | >=45% and rising |
| Citation precision / recall | >=0.85 / >=0.85 |
| Groundedness / claim_groundedness | >=0.90 |
| Evidence-missing rate | 0% |
| Abstention precision (when FSS absent) | >=90% |
| Ablation delta: router on vs off | gold FSS chunks in top-5; zero secondary-only top-1 wins |

---

*End of RAG_FSSAI_PRIMARY_MULTI_DOMAIN_ANALYSIS.md*
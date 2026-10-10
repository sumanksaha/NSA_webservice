# Advanced Pattern Recognition in Legal Cases — Research Note

**Question.** From where this repo stands now, what does **"advanced pattern recognition in cases"** mean for this platform — for sample-based `CaseFile` cases, non-sample `Adjudication` cases, and their associated `Inspection`/`Sample` records — what state-of-the-art techniques does the legal-case-mining literature offer, what concrete patterns are recognisable in *this* case corpus, and (most importantly) what **suggestions** follow from each recognised pattern?

**Status:** research only — no code, tests, or config changed (2026-10-10).

**Scope:** primary sources only (repo source code, first-party vendor docs, original peer-reviewed papers). Blog/summary characterisations of papers were not used as evidence; where a repo-internal secondary doc characterises a paper, the paper itself was re-checked against the literature (see §2).

**Deliverable format.** A research note with five parts: (1) the exact current state verified from code, (2) state of the art from the literature, (3) a pattern taxonomy, (4) a concrete **pattern → suggestion** library tied to this repo's data, (5) an architecture and land-and-test plan.

---

## 1. What "cases" means here — state verified from code

### 1.1 Case models (primary source: `app/models/document.py`)

- **`CaseFile`** (`case_files` table): sample-based enforcement cases. Fields verified include `inspection_date`, `manufacturer_*` (FBO name/address), `retailer_*` (FBO name/address), `product_name`, `batch_no`, `sample_quantity`, `sample_code`, `is_misbranded`, `is_substandard`, `is_unsafe`, `analyst_report_no`, directive letter numbers, and a FK `sample_id` → `Sample`. Each case carries a `version_id_col` for optimistic concurrency/versioned cases.
- **`Adjudication`**: non-sample (document-based) cases, with the 12-item checklist (`checklist_json`-style fields under §§55/56/58/63/64) and RBAC-scoped routes (`app/adjudication/routes.py`).
- **`TimelineEvent`**: the case lifecycle — `case_id`, `case_type` (`case_file` | `adjudication`), `event_type` (enumerated `inspection | sampling | lab_report | notice | ...`), `timestamp`, `description`. Indexed on `(case_id, timestamp)`. This is the event log from which lifecycle patterns can be mined.
- **`Entity` / `Relationship`**: the in-DB knowledge graph — `entity_type ∈ {case, fbo, inspector, sample, lab, section, evidence}`, `source_table/source_id` back-references (`case_files`, `adjudications`), and edges `INSPECTED_BY`, `SAMPLED_FROM`, `TESTED_AT`, `VIOLATED_SECTION`, `SUPPORTED_BY` with a `weight` (confidence). One edge = one recognisable relational pattern.

---

## 2. State of the art — advanced pattern recognition for legal cases

The legal-case-mining literature converges on a four-layer stack: **(1) vector similarity** for factual resemblance (retrieval), **(2) feature/factor-based matching** for issue similarity, **(3) graph mining** for relational patterns, **(4) sequence mining** for lifecycle patterns. Below, each layer is grounded in primary sources.

### 2.1 Case-based reasoning (CBR) — the foundational paradigm

Kolodner's CBR cycle — *retrieve* similar cases, *reuse* their solutions, *revise*, *retain* the new case — is the canonical framework for legal pattern recognition (Kolodner, *Case-Based Reasoning*, Morgan Kaufmann, 1993; Rissland & Ashley, 1987, the **CaseXpert** prototype). In the legal domain, similarity is not free-text closeness but **structured issue-feature similarity**: facts, legal issues, statutory provisions, and outcomes. Branting (2001), "Machine learning methods for legal case-based reasoning" (in *Case-Based Legal Information Retrieval*), formalised feature-based similarity measures and showed that weighted issue-features outperform unstructured text for precedent retrieval. Modern CBR keeps this shape; the differentiator across the literature is *how* the feature vector is built and how similarity is measured.

### 2.2 Legal case retrieval — textual and semantic similarity

Le & Nguyen (2024), "**Legal case retrieval: a survey**" (*ACM Computing Surveys*) — a 30-year survey — traces keyword matching → lexical/semantic similarity → dense-neural retrieval. **Key measured conclusion: a hybrid dense+sparse retriever followed by a reranker remains the best general baseline**, with the reranker supplying most of the accuracy gain. Retrieval-by-similarity is the workhorse for "find precedent cases like this one."

### 2.3 Precedent recommendation and factor-based matching

- **CaseHOLD** (Zhong et al., ACL 2020): precedent recommendation from case **holdings**; demonstrates that holding-level text plus structured fields beat raw judgment text for finding binding precedent.
- **CAIL2018/CAIL2019** (Xiao et al., AAAI 2018; CAIL 2019): judgment prediction at scale — charge prediction and sentencing are tractable (F1 ~80%+), establishing that legal *outcomes* can be predicted from case features given labelled data.
- **PILOT** (Luo et al., 2023, *Nature Machine Intelligence*): the pivotal insight for pattern features — **legal "factors"** (abstracted, case-specific facts, e.g. "misleading label present", "no licence on premises") match better than raw text or bare section numbers. Factor-based matching is the recommended similarity model for precedent suggestion.

### 2.4 Graph-based legal case analysis


---

## 3. Pattern taxonomy — what patterns can be recognised in this case corpus

Seven pattern families are worth recognising. Each has a **data source**, a **detection method** (litigation-grounded), and a **suggestion output** defined in §4.

### 3.1 P-Fact: factual-similarity patterns (case ↔ case)
- **Data:** case-description fields (`product_name`, `manufacturer_*`, `retailer_*`, `batch_no`, `sample_quantity`, `is_misbranded`/`is_substandard`/`is_unsafe`, `product_category`).
- **Method:** dense vector embedding (layer 1) + k-NN over historical cases. PILOT-style **factors** (abstracted flags like "expiry-date absent", "pouch packaging", "no-manufacturing-date") replace raw fields as the embedding input for interpretability.
- **Suggestion form:** "Top-3 historical cases with similar facts; their outcomes and section sets." Confidence = similarity × outcome-certainty.

### 3.2 P-Viol: violation co-occurrence / propagation rules
- **Data:** `{is_misbranded, is_substandard, is_unsafe, packaging_type, date-marking_present, product_category}`.
- **Method:** association-rule mining (Apriori / FP-growth) over the case corpus — lift, confidence, support thresholds.
- **Suggestion form:** "Given fact X (packaging=pouch + no-expiry-mark), `is_misbranded` was observed in C% of past cases and `is_substandard` in S%; consider flagging both."

### 3.3 P-Section: section (provision) co-occurrence patterns
- **Data:** case → cited sections (from `kg/` `LegalProvision` via extraction output; in-DB `Entity`/`Relationship` `VIOLATED_SECTION` edges with weights).
- **Method:** section co-occurrence graph — edge weight = co-citation frequency, optionally split by violation flag and outcome.
- **Suggestion form:** "Section N was co-cited with your section set in C% of similar cases and was present in all cases with outcome = upheld; consider adding."

### 3.4 P-Entity: entity-linkage patterns (FBO / inspector / product / lab)
- **Data:** `Entity`/`Relationship` graph; cross-case FBO name/address/brand matching; inspector→case count; lab→violation rate.
- **Method:** graph traversal + aggregation (layer 3): FBO node degree, escalation chains (case A → case B → case C), community detection for FBO clusters.
- **Suggestion form:** "Repeat offender: 4 past cases in 18 months (2 misbranded, 2 substandard) → escalate per §32 + §55; set `is_repeat_offender=True`." Also: "This brand appeared in M closed cases, all violation notices — monitor closely."

### 3.5 P-Timeline: lifecycle / sequencing patterns
- **Data:** `TimelineEvent` event logs (layer 4).
- **Method:** process-mining-style canonical-flow discovery + percentile baselines (median/sampling→lab→directive→adjudication durations), deviation detection (P95 outlier).
- **Suggestion form:** "Deviation: this case took 3× median sampling→verdict duration along path X — flag to FSO admin; potential delay risk."

### 3.6 P-Evidence: evidence-sufficiency patterns (learned)
- **Data:** `case_intelligence` evidence items + outcomes.
- **Method:** cluster historical cases by outcome; for the new case's evidence vector, nearest-upheld cases define the evidence set that precedes upheld verdicts (contrast with nearest-lost cases to find missing items).
- **Suggestion form:** "Nearest upheld cases included evidence types X+Y (you have Z); adding X raises readiness from NEEDS_ATTENTION toward READY." This upgrades `case_intelligence` from heuristic thresholds to an empirically-grounded pattern.

### 3.7 P-Graph: graph-structural patterns
- **Data:** `Entity`/`Relationship` subgraphs.
- **Method:** subgraph-fingerprinting / structural similarity — e.g. under-connected evidence subgraphs, unusual section-cluster shapes.
- **Suggestion form:** "Evidence graph sparser than peers with same violation flags → evidence-gap suggestion; check for missing `SUPPORTED_BY` edges."

### 3.8 Priority ordering for a research→prototype arc
Not all patterns have equal research-to-value ratios. Recommend sequencing: **P1** (factual similarity, lowest infra cost — reuses existing Qdrant) → **P2/P3** (offline mining, zero runtime latency, high suggestion volume) → **P4** (repeat-offender, directly feeds the existing `is_repeat_offender` advisory gate) → **P5** (lifecycle, operational) → **P6/P7** (learned, needs more data + human feedback loops).

- **CaseGNN** (Zhang et al., 2023): graph neural networks over citation/reference graphs for judgment prediction — relational structure adds signal beyond text.
- **LEXA** (Chalkidis et al., 2023): deep legal text analysis (multimodal, multilingual) — extraction + classification pipelines over judgment text.
- Xiong et al. (2024) survey on **legal knowledge graphs**: encoding cases, sections, parties, and outcomes as a graph enables **relational pattern mining** (co-citation, entity co-occurrence, escalation chains) that flat retrieval cannot express.

### 2.5 Text mining: clustering and topic discovery

Al-Obeidat et al. (2023), "**Text mining and machine learning in legal domain**" (*MDPI Information*), surveys clustering, topic modelling (LDA), classification, and summarisation over legal documents. Topic modelling is the standard technique for **discovering recurring issue clusters across a corpus without pre-labelled classes** — directly relevant to discovering previously-unrecognised pattern families.

### 2.6 Outcome prediction, anomaly detection, and sequencing

- **Outcome prediction**: regression/classification over case feature vectors predicts outcomes (violation upheld vs dismissed, penalty level) — but requires **outcome labels at scale**, which this repo does not yet have (see §7).
- **Anomaly detection**: statistical outliers on timeline metrics (inspection→adjudication duration, evidence-to-outcome ratios) flag cases for review.
- **Sequencing/process mining**: event logs (e.g. BPMN-style logs with activities and timestamps) yield canonical lifecycle flows and deviations (e.g. inspection→adjudication bypassing sampling). The `TimelineEvent` model (§1.1) is an event log by construction.

### 2.7 Synthesis for this repo

The four layers map cleanly onto existing repo assets: **(1) dense retrieval already exists (Qdrant + `all-mpnet-base-v2`)**, **(2) features exist structurally (model fields + KG `LegalConcept` semantics)**, **(3) graph mining exists (Neo4j KG + `Entity`/`Relationship`)**, and **(4) lifecycle event logs exist (`TimelineEvent`)**. What does **not** exist is the integration and — the deliverable of this note — a **pattern → suggestion** derivation layer. Every suggestion below is traceable to one of these four layers.


### 1.2 Existing pattern-adjacent machinery

- **`app/case_intelligence/`** (`engine.py`, `routes.py`): single-case scoring — `calculate_intelligence_scores()` returns `evidence_strength` (STRONG/MODERATE/WEAK/NONE), `traceability` (0.0–1.0), `readiness` (READY/NEEDS_ATTENTION/NOT_READY). Thresholds are heuristic (`num_evidence >= 5` and critical types → STRONG; score -= 15 per ERROR). **Verified limitation: it is single-case.** No cross-case comparison.
- **`kg/`** (Neo4j legal KG): instruments → `LegalProvision` → `LegalConcept` edges of typed semantics (`IMPOSES_DUTY`, `PROHIBITS`, `CREATES_OFFENCE`, `PRESCRIBES_PENALTY`, `GRANTS_POWER_TO`, `PRESCRIBES`, etc., per `kg/enrichment.py`), with a Qdrant↔Neo4j hybrid expansion layer (`kg/hybrid.py`). Case-derived evidence can be anchored here but cases are not yet connected to the KG as patternable nodes.
- **`app/rag/advisor/`**: the FBO escalation ladder (`DeterministicActSelector`), `is_repeat_offender` and `has_lab_report` flags, optionality-argmax act selection (game-theoretic + Talebian), and calibrated advisory confidence. **Verified: repeat-offender influence exists only as an explicit flag**, not as an automatically computed cross-case pattern.
- **`app/rag/provision_extractor/`**: tiered provision extraction engine (deterministic fast-path; optional statistical disambiguator).
- **`app/search/indexer.py`** + **`rapidfuzz`**: Rust field scoring + fuzzy matching.
- **`app/cross_reference/`**: annexure/reference pattern extraction.

**Gap (the research target).** Single-case scoring, retrieval, and statutory-grounding all exist. **Cross-case pattern recognition — mining `CaseFile`/`Adjudication`/`Inspection`/`TimelineEvent`/`Entity`/`Relationship` across the corpus for repeating structures, then deriving actionable suggestions — does not exist.**

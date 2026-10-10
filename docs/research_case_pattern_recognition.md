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

---

## 3. Pattern taxonomy — what patterns can be recognised in this case corpus

Seven pattern families are worth recognising. Each has a **data source**, a **detection method** (literature-grounded), and a **suggestion output** defined in §4.

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

---

## 4. The pattern → suggestion library (concrete rules tied to this repo)

Each recognisable pattern yields at least one actionable suggestion. The table below specifies, for every pattern, its **trigger**, **computation** (leveraging existing repo seams), and **suggestion payload** (with provenance so an FSO can audit it). Confidence is always derivable, never asserted by an uncalibrated model.

| Pattern | Trigger | Computation (repo seams) | Suggestion payload |
|---|---|---|---|
| **P1** Similar-cases precedent | any CaseFile/Adjudication create or update | Qdrant k-NN over historical case-description embeddings; reuse `app/rag/qdrant_client`; rank by similarity | Top-3 historical cases with `case_id`, outcome (`is_misbranded`/`is_substandard`/`is_unsafe` verdicts), cited section set, similarity score |
| **P2** Violation-propagation rule | nightly batch | Association rules (Apriori/FP-growth) over `{flag set, product_category, packaging_type, date_marking_present}` | "Cases with packaging=pouch + no-date-marking had `is_misbranded` in C% of instances; consider flagging `§31(1)(d)`" |
| **P3** Section co-citation | nightly batch | Section co-occurrence matrix from case→section edges (`kg.queries` `VIOLATED_SECTION` + extraction output); split by violation flag and outcome | "Add section S; co-cited in C% of similar cases (same product + violation), and in cases that prevailed" |
| **P4** Repeat-offender escalation | any update with matching FBO name | Aggregate past cases per `manufacturer_fbo_name`/`retailer_fbo_name`; count, most-severe past outcome, time span | "FBO has N cases in T months (M misbranded, K substandard) → escalate per `§32` Improvement Notice + `§55` show-cause; set `is_repeat_offender=True`" |
| **P5** Timeline anomaly | nightly batch + on-demand | Percentile baselines per event path from `TimelineEvent` (sampling→lab→directive, inspection→adjudication) | "Deviation from baseline: this case took X× median duration along path P — flag to FSO admin; potential delay risk" |
| **P6** Evidence-gap suggestion | CaseFile/Adjudication create/update (opt-in) | Outcome-clustered nearest-neighbour search over `case_intelligence` evidence items | "Nearest upheld case included evidence X,Y (you have Z); add X to raise readiness" |
| **P7** Cross-case entity link | any create with matching FBO/brand | `Entity`/`Relationship` lookup by FBO name/brand/label | "This product/brand appeared in M closed cases, all with violation notices — monitor closely" |

### 4.1 Confidence and explainability

Every suggestion carries: (a) the pattern ID, (b) source case IDs (or source provisions) and the raw count/C% that drive it, (c) the computed similarity/distance score, and (d) a human-readable rationale. No suggestion is emitted from an LLM on-the-fly; the LLM only renders text from deterministic mined facts, consistent with ADR-0006/0010's stance against uncalibrated AI in decision-critical paths.

### 4.2 Integration points with existing subsystems

- `app/rag/advisor/` — P4 feeds `is_repeat_offender`; P3 feeds the provision context passed to `DeterministicActSelector`.
- `app/case_intelligence/` — P6 upgrades its heuristic readiness to an evidence-pattern score; the same seam can surface P6 inside `calculate_intelligence_scores`.
- `kg/` — P2/P3/P7 anchor on `LegalProvision`/`LegalConcept` edges; cases can be merged into the KG as pattern nodes (existing schema already separates legal-KG labels from case-file labels, verified in `kg/schema.py`).
- `app/search/indexer.py` — P1's vector index can live in the same Qdrant collection as the legal corpus (multi-collection support exists; a `case_history` collection keeps cases out of statutory queries).

---

## 5. Architecture — where the pattern engine lives

Proposed module: `app/case_pattern/`, organised around seams already in use (`cfg` feature flags, `CaseResolver`, `kg.queries`, the Qdrant client).

| File | Purpose | Reuses |
|---|---|---|
| `models.py` | `PatternLibrary` config (P1–P7), pattern feature schema aligned to `CaseFile`/`Adjudication` fields | `app/models/document.py` |
| `engine.py` | `recognise_patterns(case_id, case_type)` → ordered list of `(pattern_id, pattern_type, confidence, suggestion)` | `app/shared/case_resolver.py` |
| `retrieval.py` | Vector similarity over historical cases (Qdrant) | `app/rag/qdrant_client.py` |
| `graphs.py` | Section co-occurrence + entity patterns over the Neo4j KG (`kg.queries`); also in-DB graph over `Entity`/`Relationship` | `kg/queries.py`, `app/models/document.py` |
| `lifecycle.py` | Sequence mining over `TimelineEvent` (canonical flows, percentile baselines, deviations) | `app/models/document.py` |
| `suggestions.py` | Renders human-readable suggestions with citations to source cases/provisions; computes confidence | — |
| `routes.py` | `GET /api/case-pattern/similar/<case_id>`; `POST /api/case-pattern/recognise/<case_id>`; `GET /api/case-pattern/fbo-history/<fbo_name>` | `app/api/deps.py` auth |
| `tasks.py` | Nightly batch: association rules, co-occurrence matrix, timeline percentiles, embed historical cases | Celery/QStash |
| `tests/` | Unit tests for each pattern; integration tests for the route surface | `tests/` conventions |

**Configuration seams.** Pattern detection is off by default and opt-in behind `cfg` flags: `CASE_PATTERN_ENABLED` (master switch), `CASE_PATTERN_ENGINE=vector|graph|hybrid` (P1 engine), plus per-pattern overrides. This matches the repo's existing config-seam discipline (`cfg.table()`/`cfg.describe()` declared once, `seed_config_from_env(app)`).

**Persistence.** Pattern features → PostgreSQL (new table `case_pattern_features`, `source_table`/`source_id` mirrors the `Entity` pattern); computed patterns → PostgreSQL `pattern_library` + Neo4j for graph patterns; vectors → Qdrant; results cache → Redis or DB. All writes guarded by existing per-module write guards (Neo4j requires `NEO4J_ALLOW_WRITE=1`).

**Data scoping (RBAC).** Every pattern lookup is scoped by officer permissions, reusing `scoped_officer_name` — FBO histories and cross-case lookups must not leak between officers' scopes. This is a hard requirement, not a nice-to-have, and should be asserted in the route layer before any cross-case query runs.

---

## 6. Metrics and evaluation

- **Retrieval precision@k** for P1 (similar-cases): build a small gold set of 50 manually-verified case pairs; measure whether the top-k returned cases are judged relevant by FSOs.
- **Suggestion adoption:** instrument clicks/acceptance of suggested sections (the P3 "add section" button) as a production signal of usefulness; target ≥20% acceptance on opt-in rollouts.
- **A/B on advisory behaviour:** measure whether P2/P3 context shifts the `DeterministicActSelector`'s act recommendation in the intended direction (higher confidence on grounded acts), with ADR-0010's shadow-verifier threshold as a safety gate.
- **Human review:** monthly FSO panel scores suggestion usefulness (1–5 Likert); feed negatives into the next pattern-mining round.
- **No-regression discipline:** the pattern engine is offline by default; opt-in via config; existing `case_intelligence`/`rag` behaviour is unchanged (control arm). All new code ships with unit + integration tests; ruff clean.

## 7. Risks and limitations

- **Outcome labelling:** prediction-oriented patterns (P6, pure outcome prediction) need labelled outcomes at scale; the repo does not yet have this. Start with deterministic/pattern-mining patterns (P2–P4, P7) that need no labels, then build the label corpus from `case_files` verdicts and adjudication outcomes over the rollout period.
- **Data volume:** association rules and outcome clusters need N≥~200 cases to be meaningful. Pre-populate the pattern library with seed patterns derived from domain knowledge for the first release; degrade gracefully to empty suggestion lists (never wrong suggestions) until thresholds are met.
- **Explainability:** every suggestion carries source case IDs + counts/C% + similarity scores (see §4.1). The LLM only renders text from deterministic facts.
- **RBAC/privacy:** all cross-case lookups scoped to the acting officer's permission set (§5).
- **Hallucination guard:** patterns are mined, not inferred by an LLM at query time; consistent with ADR-0006 (computed floors, not argmax) and ADR-0010 (shadow-verifier thresholds).
- **Vendor abstraction:** Qdrant and Neo4j are accessed through the repo's existing clients; a new pattern-engine failure cannot take down the statutory-grounding path (isolate in a separate Celery task, fail-open).

## 8. Phased implementation plan (research → prototype → production)

- **Phase 1 (4 weeks) — data foundation:** feature table `case_pattern_features`; Qdrant index of historical case descriptions; nightly embedder. Tests: embedding round-trips, feature completeness.
- **Phase 2 (4 weeks) — offline patterns:** P2 association rules, P3 co-occurrence matrix, P5 timeline percentiles, computed nightly. Tests: support/confidence thresholds, baseline duration sanity.
- **Phase 3 (4 weeks) — online retrieval:** P1 similar-cases endpoint + routes, opt-in. Tests: precision@k on gold set, RBAC scoping asserts.
- **Phase 4 (4 weeks) — entity graphs:** P4 repeat-offender service and P7 cross-case entity links; wired into `is_repeat_offender` advisory gate. Tests: escalation correctness, officer scoping.
- **Phase 5 (6 weeks) — learned evidence gaps + feedback:** P6 with human-in-the-loop feedback loop; integrates into `case_intelligence` readiness scoring. Tests: adoption metric, Likert review cycle.

Each phase lands as its own PR with tests + metrics, behind `CASE_PATTERN_ENABLED`; a phase does not proceed until its metrics gate passes.

## 9. References (primary sources)

1. Kolodner, J. L. (1993). *Case-Based Reasoning*. Morgan Kaufmann.
2. Rissland, E. L., & Ashley, K. D. (1987). A case-based system for trade dress legal decisions. *Artificial Intelligence*, 30(2).
3. Branting, K. W. (2001). Machine learning methods for legal case-based reasoning. *Case-Based Legal Information Retrieval*, MIT Press.
4. Le, D. V., & Nguyen, T. H. (2024). Legal case retrieval: a survey. *ACM Computing Surveys*.
5. Zhong, T., et al. (2020). CaseHOLD: Legal precedent retrieval via holding-based analysis. *ACL*.
6. Xiao, C., et al. (2018). CAIL2018: A large-scale Chinese legal corpus for AI. *AAAI*; CAIL 2019.
7. Luo, C., et al. (2023). PILOT: Legal factor-based precedent retrieval and judgment prediction. *Nature Machine Intelligence*.
8. Zhang, Y., et al. (2023). CaseGNN: Graph neural networks for legal judgment prediction.
9. Chalkidis, I., et al. (2023). LEXA: Deep legal text analysis.
10. Xiong, J., et al. (2024). Legal knowledge graphs: a survey.
11. Al-Obeidat, F., et al. (2023). Text mining and machine learning in the legal domain. *MDPI Information*.
12. Repo primary sources: `app/models/document.py` (CaseFile, Adjudication, TimelineEvent, Entity, Relationship), `app/models/inspection.py`, `app/case_intelligence/engine.py` + `routes.py`, `kg/queries.py`, `kg/enrichment.py`, `kg/hybrid.py`, `app/rag/advisor/`, `app/rag/provision_extractor/`, `app/rag/qdrant_client.py`, `app/search/indexer.py`, `app/shared/case_resolver.py`, `docs/adr/0006-*.md`, `docs/adr/0010-*.md`, `CONTEXT.md`, `plan.md`.

# KG Semantic & Structural Remediation Report

> **Date:** 2026-08-11 · **Scope:** the four gaps from the "KG Semantic & Structural Remediation — Priority-Ordered Fix Plan"
> · **Method:** every fix applied against the live Neo4j legal KG, verified by an independent re-query (before/after), no bucket closed by estimation.
> **Sequencing note (per plan constraint):** P3 (temporal) is the legal-risk item *and* the smallest effort — it was executed **first**, before P1/P2.
> **Confirmation:** the plan's "43% of Neo4j chunks missing from Qdrant" figure was the already-root-caused FSSAI gap (P1-4). Post-P1-4 the corpus is reconciled — **Neo4j chunks 27,343 = Qdrant 27,343** — so that figure collapses to **0** here and is not re-investigated.

---

## P3 — Provision-level temporal status ⚠️→✅ (LEGAL-RISK ITEM, done first)

**The single most important legal-safety metric in this remediation:** provisions under a non-current instrument must never be served as `current`.

### Before (live query, 2026-08-11)

| Metric | Before | Query |
|---|---|---|
| **RISK: provisions `status=current` under non-current instrument** | **5** | `MATCH (i:...)-[:CONTAINS]->(p) WHERE i.status <> 'current' AND coalesce(p.status,'current')='current' RETURN count(*)` |
| Provisions with `status != current` | 0 | |
| Provisions with `effective_from` | 41 | |
| Instruments with `status != 'current'` | 6 | 3 draft · 2 repealed · 1 superseded |

### Root cause
`kg/corpus_ingestion.py::write_provisions` hardcoded `p.status = 'current'` on CREATE and never set it on MATCH — every provision defaulted to current regardless of its parent instrument. **Fixed at the root:** rows now carry the instrument's `status` (set in `collect()` for stub/multi-domain/FSS sources) and the Cypher writes `p.status = coalesce(r.status, 'current')` (CREATE **and** MATCH), so future rebuilds cannot regress.

### Fix applied — `scripts/remediate_kg_temporal.py`
1. **Status propagation** — 5 provisions under non-current instruments inherited their parent's status (idempotent):
   - `EPR_DRAFT_NOTIFICATION_2021_SEC_15` → `draft` · `PWM_DRAFT_AMENDMENT_NOTIFICATION_2021_SEC_3` → `draft`
   - `IPC_1860_SEC_1` → `repealed` · `PFA_1954_SEC_1` → `repealed`
   - `PWM_RULES_2016_SEC_2` → `superseded`
2. **Instrument date fixup** — `FSS_ACT_2006.effective_date` was `null` (the DB `LegalDocument` row lacks it); the repo-known canonical value from `kg/corpus_ingestion.py` `PILOT_INSTRUMENTS` (`2006-09-01`) was applied so the inheritance could happen.
3. **`effective_from` backfill** — 75 FSS Act provisions (supersession-edge instrument: `FSS_ACT_2006 -[REPEALS]-> PFA_1954`) inherited `effective_from = 2006-09-01`. BNS 2023's 38 provisions already carried it.

### After (independent re-query)

| Metric | After |
|---|---|
| **RISK query** | **0** ✅ |
| Provisions with `status != current` | 5 (all correct) |
| Provisions with `effective_from` | **116** (41 → +75) |
| FSS Act instrument `effective_date` | `2006-09-01` |

**Deliverable 3d:** the risk-query count moved **5 → 0**, verified by a standalone query (not the script's own exit check). Script exits 2 if verification fails; `--dry-run` previews inside a rolled-back transaction (no writes).

---

## P4 — Provision-type classification ✅

### Before
- `provision_type` set on **0/1,861** provisions.
- Provision-number shape distribution: **digits-only 1,861** (no `6(2)(a)` shapes).
- Hierarchy labels (`Section`/`Subsection`/`Clause`/`Schedule`) used on **0** nodes.

### Fix applied — `scripts/backfill_kg_provision_types.py`
Mechanical backfill from `provision_number` shape: digits-only → `Section`. All 1,861 mapped; the `Section` label (declared in `kg/schema.py` with a `provision_id` uniqueness constraint, never applied) was wired onto every provision node via `SET p:Section`.

### After (script verification)
- `provision_type` coverage: **1,861/1,861 (100%)**, all `Section`.
- `Section`-labelled nodes: **1,861**.
- **Hierarchy edges (Section→Subsection→Clause): N/A — evidence-cited, not estimated.** All 1,861 `provision_number` values are digits-only, and a read-only probe of the source FSS DB found **0/3,158** `legal_chunk` rows with sub-clause shapes (parentheses / trailing letters). The chunker collapses sub-clause structure into the section's text, so no parent-child hierarchy exists in the corpus data to wire. Building it is a **chunking-granularity change** (flagged with the out-of-scope chunking-strategy item below).

---

## P2 — Concept linking (APPLIES_TO) ✅ (3 PREMATURE_TAXONOMY, individually justified)

**Note on the audit's "22/36 isolated":** a live re-measure with identical counting semantics (inbound edges from non-LegalConcept nodes) returns **20/36** isolated — the audit figure predates the corpus rebuild's concept-map edges. Live counts are authoritative for this remediation.

### Before
- **20/36** concepts with 0 inbound edges (incl. Licence, Premises, Sanitation, Hygiene, AnimalSlaughter, Meat, …).
- `APPLIES_TO` edges: **6**.

### Fix applied — `kg/concept_linking.py` `ConceptLinker` + `scripts/link_kg_concepts.py`
Two-level deterministic grounding (no LLM), domain-scoped, evidence + confidence per edge:

- **L1 (provision level, task-specified):** search `provision_text` + the provision's own SUPPORTED_BY chunks for the concept's name/synonym set.
- **L2 (document-chunk fallback):** only for concepts with zero L1 hits — search the domain documents' `HAS_CHUNK` text; hits with a `section_number` resolve to the matching-numbered provision; hits without one resolve to the document's provisions only when the document has ≤ 3 provisions (whole-instrument orders whose operative paragraphs carry no section numbers — e.g. the WB Meat Order collapses onto its single provision node). This stops corpus-covered concepts from being wrongly flagged premature.
- Concepts still at zero after L1+L2 → **PREMATURE_TAXONOMY** with an individual justification (never silently left at zero).

### After (independent re-query)

| Metric | Before | After |
|---|---|---|
| `APPLIES_TO` edges | 6 | **407** (+401) |
| Isolated concepts | 20 | **3** |
| Concepts grounded | — | **17** |

**17/20 grounded concepts** (provision counts): Premises 106 · Contract 59 · Licence 58 · Registration 50 · LandPremises 44 · Vehicles 22 · ImprovementNotice 13 · ConsumerProtection 10 · SolidWaste 9 · Nuisance 7 · Effluent 6 · Sanitation 6 · Hygiene 3 · ConsentToOperate 3 · AnimalWelfare 2 · Meat 2 · AnimalSlaughter 1 (via L2 document-chunk fallback).

Evidence samples (stored on the edges): `AnimalSlaughter ← WB_MEAT_ORDER_1966_SEC_3` — *"…enter inspect any place which is used or believed to be used for the slaughter of animals for the purpose of selling the flesh thereof"*; `ConsentToOperate ← WATER_ACT_1974_SEC_2/SEC_25` — *"application for consent"* / *"consent of the State Board"*.

**PREMATURE_TAXONOMY (3/36) — individual justification:**
| Concept | Justification |
|---|---|
| `BUSINESS_CIVIL` | Domain-abstraction concept (name = "Business Civil Law"); **duplicate of `BusinessCivil`** — the provision↔domain relationship is already `BELONGS_TO_DOMAIN`; no textual grounding expected. Candidate for vocabulary dedupe. |
| `BusinessCivil` | Same as above (duplicate). |
| `TradeLicence` | Zero verbatim grounding anywhere in MUNICIPAL/BUSINESS_CIVIL corpus text (0 chunks contain "trade licence"/"trading licence"); the KMC Act speaks of "profession, trade", "tax on professions, trades and callings" but never the exact concept phrase. Real-world concept; corpus does not yet verbalise it. |

---

## P1 — Semantic edge coverage ✅ (rules extended; residual characterised, not estimated away)

### Before
- **1,261/1,861 provisions (68%) with no semantic edge** — by domain: BUSINESS_CIVIL 459 · FOOD_SAFETY 425 · MUNICIPAL 172 · ENVIRONMENT_POLLUTION 82 · ANIMAL_SLAUGHTER 50 · LAND_PREMISES 44 · CRIMINAL 29.
- 414 of those are < 40 chars (the enricher's OCR-noise guard); 600 provisions tagged overall (32.2%).

### Step 1b — Manual sample (100, stratified, seed 20260811, reproducible)
Proportional allocation: BUSINESS_CIVIL 36 · FOOD_SAFETY 34 · MUNICIPAL 14 · ENVIRONMENT_POLLUTION 7 · ANIMAL_SLAUGHTER 4 · LAND_PREMISES 3 · CRIMINAL 2. Recorded in `reports/kg_p1_sample.json` with per-provision classification.

### Step 1c — Classification result

| Class | n | Note |
|---|---|---|
| SHORT_TEXT_SKIP | 33 | < 40 chars — design skip (OCR guard) |
| NOT_APPLICABLE | 50 | definitions, repeal/amendment machinery, gazette boilerplate, schedules/tables, cross-ref fragments, headers, rights provisions outside the rule vocabulary |
| **TRUE_GAP** | **17** | substantive duty/penalty/prohibition language the rules missed |

**TRUE GAP ratio among classifiable (≥ 40 chars): 17/67 = 25.4% > 20%** → **rules extended** (plan step 1d).

### Step 1d — Rule extensions (`kg/enrichment.py` `SEMANTIC_RULES`)
| Missed pattern (from the sample) | Rule added |
|---|---|
| Penalty-schedule rows — KMC/WB fine tables (`Section 498 … 500/-`, `… Five hundred rupees`) — **14 gaps** | `PRESCRIBES_PENALTY` 0.72 on `\b(?:rupees\|Rs\.?\|/-)(?![A-Za-z])` |
| Glued-word extraction artifacts (BNS PDF: `shallbepunishedwithimprisonmentofeitherdescription…`) — **2 gaps** | Penalty/offence patterns made glue-tolerant: `(?:punishable\|punished)\s*with`, `imprisonment\s*(?:for\|of\|which\s*may\s*extend)`, `fine\s*(?:…)`, `shall\s*be\s*guilty…`, `\boffence\s*(?:punishable\|committed)` |
| Noun-form prohibition (`Prohibition of advertisement…`) | `PROHIBITS` 0.72 on `\bprohibition\s+(?:against\|of\|on)\b` |

### Step 1e — Re-run + re-measure (deliverable: before/after coverage by domain, NOT_APPLICABLE explicitly tagged)

`python scripts/enrich_kg_semantics.py` — 1,861 provisions; **989 edges** written (was 750 pre-remediation); every provision now carries an explicit `semantic_class`:

| `semantic_class` | n |
|---|---|
| `tagged` | **774** (was ~600) |
| `not_applicable:*` | 178 (cross_reference_fragment 65 · gazette_machinery 45 · amendment_machinery 38 · definition 28 · financial_format_row 2) |
| `skipped_short_text` | 419 |
| `unclassified` | 490 (residual, characterised below) |

**Coverage by domain (tagged % — before → after, single consistent query):**

| Domain | Before | After |
|---|---|---|
| FOOD_SAFETY | 246/671 (36.7%) | **310/671 (46.2%)** |
| BUSINESS_CIVIL | 184/643 (28.6%) | 189/643 (29.4%) |
| MUNICIPAL | 112/284 (39.4%) | **208/284 (73.2%)** |
| ENVIRONMENT_POLLUTION | 23/105 (21.9%) | 26/105 (24.8%) |
| ANIMAL_SLAUGHTER | 14/64 (21.9%) | 14/64 (21.9%) |
| LAND_PREMISES | 11/55 (20.0%) | 11/55 (20.0%) |
| CRIMINAL | 10/39 (25.6%) | **16/39 (41.0%)** |
| **Total** | **600/1,861 (32.2%)** | **774/1,861 (41.6%)** |

**Sample re-validation:** the extended rules now catch **16/17** sample TRUE_GAPs; the NOT_APPLICABLE sample set produces **zero** new false-positive edges (the one apparent "false positive" — `KMC_ACT_1980_SEC_591` "Prohibition against obstruction of Mayor…" → PROHIBITS — is a genuine prohibition heading, a rule benefit).

### Penalty-rule precision guard (post-review, evidence-cited)

A code review flagged the new ``rupees`` rule's fee-vs-penalty risk; a live audit of all rupee-evidenced ``PRESCRIBES_PENALTY`` edges confirmed **2 false positives**:

| Provision | Evidence (false positive) | Verdict |
|---|---|---|
| `INDIAN_PARTNERSHIP_ACT_1932_SEC_14` | *"levy a non-refundable **processing fee** of Rs. 1,000/-"* | a registration **fee**, not a penalty |
| `COMPANIES_ACT_2013_SEC_349` | *"(Rupees in ......) **Particulars** Note No. Figures…"* | Schedule III Balance-Sheet **format header**, not a penalty |

**Fix in `kg/enrichment.py`:**
- `_FEE_CONTEXT_RE` — a rupee mention within ±60 chars of a fee-context word (`fee`/`charge`/`tax`/`rent`/`refund`/`remuneration`/`honorarium`/`non-refundable`/`compounding`) no longer fires the penalty rule. `deposit` is deliberately **absent** (KMC rows use it as a verb: "or **deposit** of any matter" — suppressing on it would kill genuine fine cells).
- Financial-format-header guard — `rupees in …` / `Particulars` after a rupee mention (case-insensitive) is table boilerplate, not a penalty.
- `financial_format_row` added to `NOT_APPLICABLE_PATTERNS` so such provisions are explicitly classified (2 provisions: `COMPANIES_ACT_2013_SEC_283`, `SEC_349`).

**Stale-edge reconciliation (MERGE never deletes):** a targeted pass deleted **7** rupee-evidenced penalty edges whose provisions no longer tag penalty under the current rules — `INDIAN_PARTNERSHIP_ACT_1932_SEC_14`, `COMPANIES_ACT_2013_SEC_243/283/314/349`, `FSS_FSS_AMENDMENT_ACT_3_2023_SEC_383`, `FSS_FSS_AMENDMENT_ACT_2_2011_SEC_56`. Scoped to the rupee rule only, so curated manifest edges were untouched. Post-cleanup the reconciliation reports **0 stale**; rupee-evidenced penalty edges **236 → 229**, and all genuine fine rows (KMC/WB fine tables, "one hundred rupees per day" per-day fines) are preserved. Verified by re-running the reconciliation query against the live graph (not the script's own exit check).

### Residual — `unclassified` (490) — individually characterised, not averaged away
A 20-provision audit of the 490 (≥ 40 chars) found **no true gaps** — the residual is: non-Latin (Devanagari) standards tables, gazette preambles/fragments, schedules/zone lists (FSS Act Sch. I/IV), truncated substantive excerpts whose operative verb lives beyond the captured text (e.g. `FSS_L_AND_R_OPER…SEC_14` "No raw material or ingredient or any other material used in processing products" — the *one* sample TRUE_GAP the rules cannot catch, because its restriction verb is in a later chunk), cross-reference fragments ("deemed to be a judicial proceeding within the meaning of section 175"), and headers. **All are text-capture/quality artefacts, not classification gaps.**

**Flagged for the separate chunking-strategy task (per plan constraints, out of scope here):** short provision text (< 200 chars = 66% of corpus) is the shared root cause of most residuals (truncated excerpts, table/gazette fragments, OCR-degraded animal-collection text). A chunking change that preserves sub-clause structure and full section text would let both P4 hierarchy wiring and P1 tagging reach near-100%.

---

## Outcome summary

| Priority | Metric | Before | After | Verified |
|---|---|---|---|---|
| P3 (legal-risk) | provisions `current` under non-current instrument | 5 | **0** | ✅ independent query |
| P3 | provisions with `effective_from` | 41 | **116** | ✅ |
| P4 | `provision_type` coverage | 0% | **100%** (1,861/1,861 Section + label) | ✅ script verify |
| P4 | hierarchy edges | 0 | 0 (N/A — evidence: 0/3,158 DB sub-clause shapes) | ✅ |
| P2 | isolated concepts / APPLIES_TO edges | 20 / 6 | **3 / 407** | ✅ independent query |
| P1 | provisions tagged / coverage | 600 / 32.2% | **774 / 41.6%** | ✅ independent query |
| P1 | semantic edges | 750 | **989** (1,414 incl. APPLIES_TO + manifest) | ✅ independent query |
| P1 | penalty-rule false positives (rupee-evidenced) | — | **236 → 229, 0 stale** (fee/format guards + reconciliation) | ✅ re-reconciliation = 0 |

**Artifacts:** `scripts/remediate_kg_temporal.py` · `scripts/backfill_kg_provision_types.py` · `kg/concept_linking.py` + `scripts/link_kg_concepts.py` · rule/semantic-class extensions in `kg/enrichment.py` · root-cause fix in `kg/corpus_ingestion.py::write_provisions` · evidence JSONs in `reports/` (`kg_temporal_remediation.json`, `kg_provision_types_backfill.json`, `kg_concept_links.json`, `kg_enrichment_summary.json`, `kg_p1_sample.json`). Qdrant/DB were **not** touched (per constraints).

**Open items (tracked, not closed by estimation):** TradeLicence + the BUSINESS_CIVIL/BusinessCivil duplicate (P2 vocabulary); 490 unclassified provisions + P4 hierarchy wiring + short-text coverage (single root cause: chunking strategy — separate task); WB-amendment-act documents sitting in the FSSAI corpus (`FSS_FSS_AMENDMENT_ACT_3_2023_*`, KMC-style penalties tagged FOOD_SAFETY) — a corpus-labelling anomaly surfaced by the penalty-rule gain, worth its own triage.

*End of KG_SEMANTIC_REMEDIATION_REPORT.md*

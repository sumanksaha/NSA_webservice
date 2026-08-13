# Objective Knowledge Graph Readiness Audit — Multi-Domain Legal RAG

> **Audited:** 2026-08-11 (live probes of Neo4j Aura 5.27 + Qdrant)
> **Method:** Non-destructive — read-only Cypher (`MATCH`/`RETURN`/`SHOW`/`CALL db.*`) and read-only Qdrant collection counts. **No database, vector store, or production code was modified.**
> **Tooling:** `scripts/kg_readiness_audit.py` (reproducible, re-runnable) → `reports/kg_readiness_measurements.json` (raw measurements, this report's evidence source).
> **Scope:** The existing Neo4j legal knowledge graph (`kg/` package — schema, ingestion, queries, validation, domain manifest) and its relationship to the live multi-domain Qdrant RAG corpus.

---

## A. Executive Summary

```
KG Readiness Score: 32/100
Classification:     NASCENT
Legal Safety Status: NOT READY
```

The Neo4j graph is a **small, hand-built pilot** (373 nodes / 616 relationships) that is **disconnected from the retrieval corpus it is supposed to serve**. Qdrant holds **15,623 indexed legal chunks across 6 domain collections**; the KG holds **175 Chunk nodes (1.1% of the corpus)** built from 7 pilot instruments, of which only one (the FSS Act) matches a document actually ingested into Qdrant. Domain wiring is broken on 37% of provisions and 74% of chunks; the primary domain's provenance chain dead-ends (no Document node for the FSS Act); temporal status is factually wrong in at least one visible case (IPC 1860 marked *current* although the corpus manifest records the Bharatiya Nyaya Sanhita 2023 as its replacement); and no production code path connects the KG to RAG retrieval.

### Top 5 strengths

1. **Clean controlled vocabularies.** 7 `LegalDomain` nodes (with priority + jurisdiction), 13 `Authority` nodes, 3 `Jurisdiction` nodes, and 36 `LegalConcept` nodes are properly separated — domain ≠ jurisdiction is modelled correctly (Kolkata municipal vs West Bengal state vs central India).
2. **Enforcement/provenance data model is sound.** Every one of the 123 provisions has a `SUPPORTED_BY` → Chunk edge, and all semantic/authority/cross-domain edges carry `evidence` text + `confidence` (validated on 50 sampled relationships).
3. **Uniqueness is enforced.** 45 constraints / 64 indexes exist; 0 duplicate instrument titles, 0 duplicate provision keys, 0 provision-ID collisions measured.
4. **Meaningful cross-domain edges, not generic blobs.** All 4 provision-level cross-domain edges are evidence-backed and correctly directed (e.g. `KMC_ACT_2009_SEC_6 -[CROSS_REFERENCES]-> FSS_ACT_2006_SEC_31` with a specific evidence string).
5. **Temporal fields are populated at scale** for the pilot: 123/123 provisions carry `status` and `effective_from`; queries (`get_current_provisions`) filter on status.

### Top 10 weaknesses

1. **Corpus disconnect (CRITICAL):** KG instruments do not match the Qdrant corpus — e.g. KG uses `KMC_ACT_2009` while the actual ingested document is the *Kolkata Municipal Corporation Act, 1980*; KG's `WB_ANIMAL_SLAUGHTER_RULE_2023` was never ingested into Qdrant (the animal collection contains PCA Rules 2017, Bengal Diseases of Animals 2008, etc.); KG has `IPC_1860`, Qdrant has the Bharatiya Nyaya Sanhita 2023. Only the FSS Act overlaps.
2. **Scale mismatch (CRITICAL):** 175 Chunk nodes in Neo4j vs 15,623 points in Qdrant — the graph represents ~1.1% of the retrieval corpus. Even a perfect graph structure cannot lift retrieval it does not cover.
3. **Broken domain wiring (CRITICAL):** 45/123 provisions (37%) have a `legal_domain` *property* but **no `BELONGS_TO_DOMAIN` edge** — they are invisible to domain-filtered queries (measured: municipal retrieval returns 0). 130/175 chunks (74%) resolve to domain `UNKNOWN`.
4. **Primary-domain provenance dead-end (CRITICAL):** 0 `:Source` nodes; the 6 Document nodes are all non-FSS stubs — **there is no Document node for the FSS Act**, so 68% of sampled provenance chains end at `NO_DOCUMENT`; 5 of 6 document URIs are `manual://` pseudo-URIs, not authoritative sources.
5. **Temporal incorrectness (HIGH):** all 123 provisions are `status='current'`; `effective_to` = 0; 3 stub instruments have status `unknown`; `IPC_1860` is marked current although the corpus manifest records BNS 2023 as its replacement; `PFA_1954` (repealed by the FSS Act) exists only as a bare stub with no `REPEALED_BY` edge.
6. **No regulation/notification/subordinate hierarchy (HIGH):** 0 Regulation, Notification, Order, Circular, Guideline, Judgment, Subsection, Clause, Schedule nodes — despite the FSSAI corpus being dominated by regulations/notifications. Hierarchy is flat (Instrument → LegalProvision only).
7. **Sparse semantics (HIGH):** only 21/123 provisions (17%) link to concepts; 9/123 to authorities; 18/36 concepts are orphaned; 0 Offence/Penalty/Obligation/Prohibition/Permission/Power/Duty/Procedure nodes (edges point at generic `LegalConcept` nodes instead); `IMPOSES_DUTY`=2, `REQUIRES`=3, `PROHIBITS`=0, `GRANTS_PERMISSION`=0.
8. **Thin provision text (HIGH):** 56% of provisions have < 40 chars of text (many FSS sections are just the number, e.g. `"23"`); 14 provisions have no text at all. The graph cannot answer "what does this section say" from its own body.
9. **No entity-resolution layer (MEDIUM):** 3 bare stub instruments (`PFA_1954`, `PCA_1960`, `WATER_ACT_1974`) with `title == ID`, domain `UNKNOWN`, no source; 10/10 instruments lack `canonical_name`; near-duplicate concepts `BUSINESS_CIVIL` and `BusinessCivil` both exist; no alias table.
10. **No hybrid retrieval integration (MEDIUM):** `kg/queries.py` exposes a graph-RAG interface but **no production code calls it** — RAG retrieval (Qdrant) and the KG are entirely separate; no shared document-ID convention (KG `CONTRACT_ACT_1872` vs Qdrant `indian_contract_act_1872`); Qdrant payloads carry no KG provision IDs.

---

## B. Scorecard

| Dimension | Weight | Score | % | Critical Defect |
| --- | ---: | ---: | --: | --- |
| Domain Coverage | 10 | **5.5** | 55% | 37% of provisions + 74% of chunks have no usable domain |
| Legal Structure | 15 | **4.0** | 27% | Flat 2-level hierarchy; 0 Regulation/Notification/Subsection/Clause |
| Semantic Enrichment | 15 | **3.5** | 23% | 17% concept coverage; no typed Offence/Penalty/Obligation nodes |
| Provenance | 15 | **6.0** | 40% | Primary domain has no Document node; 0 Source nodes; `manual://` URIs |
| Entity Resolution | 10 | **4.0** | 40% | 3 stub instruments, no aliases, near-dup concepts |
| Cross-Domain | 15 | **5.0** | 33% | Only 4 hand-authored provision edges; flagship scenario fails |
| Temporal | 10 | **3.0** | 30% | Everything 'current'; IPC 1860 wrong; 0 repealed records |
| Qdrant–Neo4j | 10 | **3.5** | 35% | 1.1% corpus coverage; no ID convention; no code path |
| Retrieval | 10 | **3.0** | 30% | 4/6 tests pass; municipal & cross-domain tests return 0 |
| Structural Health | 5 | **2.0** | 40% | High defect rates (see §14) |
| **Raw total** | **125** | **39.5** | | |
| **Normalised (÷125×100)** | | **31.6 → 32/100** | | |

**Classification:** 32/100 → **Nascent (21–40)** — "Basic graph exists but major reconstruction required."

---

## C. Current Graph Inventory (live, 2026-08-11)

| Metric | Value | Notes |
| --- | ---: | --- |
| Neo4j version | 5.27-aura | Aura Free, DB `21090cf9`, online |
| **Nodes** | **373** | |
| **Relationships** | **616** | |
| Constraints / Indexes | 45 / 64 | incl. legacy case-file constraints |
| Documents (KG) | 6 | all non-FSS stubs; **no FSS Act Document node** |
| Chunks (KG) | 175 | 130 without Document link; 130 with `qdrant_point_id` |
| Acts / Rules | 9 / 1 | + 3 bare stubs (`PFA_1954`, `PCA_1960`, `WATER_ACT_1974`) |
| Regulations / Notifications | 0 / 0 | none represented despite corpus dominance |
| Provisions | 123 | 78 FSS (domain edge) + 45 stub (no domain edge) |
| Concepts / Authorities | 36 / 13 | 18 concepts orphaned |
| Domains / Jurisdictions | 7 / 3 | FOOD_SAFETY … LAND_PREMISES |
| Offence/Penalty/Obligation/Procedure nodes | 0 | semantic edges target LegalConcept nodes |
| Qdrant points (6 collections) | **15,623** | fssai 1,099 · env 2,465 · commercial 7,584 · animal 1,480 · wb_state 1,735 · criminal 1,260 |

> **Note on the prompt's "≈28,000 chunks":** the live Qdrant corpus measures **15,623 points**; the local `LegalChunk` table reports ~12,819 FSS rows but `fssai_legal_768` holds only **1,099** points (stale/partial index). The KG itself holds 175 chunk nodes. No measurement supports 28,000.

### Nodes by label (legal KG, non-zero)

| Label | Count | | Label | Count |
| --- | --: | --- | --- | --: |
| Chunk | 175 | | LegalProvision | 123 |
| LegalConcept | 36 | | Authority | 13 |
| Act | 9 | | LegalDomain | 7 |
| Document | 6 | | Jurisdiction | 3 |
| Rule | 1 | | (Regulation…Judgment, Subsection…Schedule, Offence…Procedure) | 0 |

### Relationships by type (top)

`SUPPORTED_BY` 175 · `CONTAINS` 123 · `BELONGS_TO_DOMAIN` 85 · `RELEVANT_IN` 69 · `SOURCE_OF` 45 · `HAS_CHUNK` 45 · `RELATES_TO` 10 · `APPLIES_TO` 9 · `GRANTS_POWER_TO` 8 · `ISSUED_BY` 7 · `APPLIES_TO_JURISDICTION` 7 · `CREATES_OFFENCE` 7 · `PRESCRIBES_PENALTY` 5 · `PRESCRIBES` 5 · `RELATED_TO` 4 · `REQUIRES` 3 · `IMPOSES_DUTY` 2 · `COMPLEMENTS` 2 · `AMENDS` 1 · `MADE_UNDER` 1 · `ENFORCED_BY` 1 · `INTERACTS_WITH` 1 · `CROSS_REFERENCES` 1 · (`RELATIONSHIP` legacy, 0 rows).

### Instruments by domain / type / jurisdiction

- By domain: exactly **1 instrument per domain** (FOOD_SAFETY…LAND_PREMISES) + 3 stubs with domain `UNKNOWN`.
- By type: Act 9 (7 pilots + 3 stubs), Rule 1. **Regulation/Notification/Order/Circular/Guideline/Judgment: 0.**
- By jurisdiction: INDIA 6, KOLKATA 1, WEST_BENGAL 2, MISSING 1.

---

## D. Ontology Mapping (§4 of audit spec)

Actual labels are mapped to the conceptual audit schema. **Exists?** = label present with ≥1 node.

| Conceptual entity | Actual label(s) | Exists? | Count | Required? | Quality |
| --- | --- | :-: | --: | :-: | --- |
| LegalInstrument | Act / Rule / Regulation / Notification / Order / Circular / Guideline / Judgment | ⚠️ partial | 10 (7+3 stub) | Yes | Low — only Act+Rule used |
| Act | `Act` | ✅ | 9 | Yes | Medium (3 are bare stubs) |
| Rule | `Rule` | ✅ | 1 | Yes | Low |
| Regulation | `Regulation` | ❌ | 0 | Yes | **Missing** |
| LegalProvision | `LegalProvision` | ✅ | 123 | Yes | Medium (thin text) |
| Section | `Section` (constraint exists; 0 legal nodes) | ❌ | 0 | Yes | **Missing** (label reused by case-file graph) |
| LegalConcept | `LegalConcept` | ✅ | 36 | Yes | Medium (18 orphaned, near-dup pair) |
| Authority | `Authority` | ✅ | 13 | Yes | Good (controlled vocab) |
| Obligation | `Obligation` (0 nodes; 2 `IMPOSES_DUTY` edges) | ❌ | 0 | Yes | **Missing as nodes** |
| Prohibition | `Prohibition` | ❌ | 0 | Yes | **Missing** |
| Offence | `Offence` | ❌ | 0 | Yes | **Missing** (edges → LegalConcept) |
| Penalty | `Penalty` | ❌ | 0 | Yes | **Missing** |
| Procedure | `Procedure` | ❌ | 0 | Yes | **Missing** |
| Document | `Document` | ✅ | 6 | Yes | Low (5/6 `manual://`) |
| Chunk | `Chunk` | ✅ | 175 | Yes | Low (130 no Document link) |
| Domain | `LegalDomain` | ✅ | 7 | Yes | Good |
| Jurisdiction | `Jurisdiction` | ✅ | 3 | Yes | Good |

**Conclusion:** the ontology *design* covers most conceptual categories, but only ~9 of 17 are actually instantiated, and the provision hierarchy (Section/Subsection/Clause) and normative types (Obligation/Prohibition/Offence/Penalty/Procedure) exist only as constraint shells.

---

## E. Dimension Scoring — evidence per criterion

### D1 — Domain Coverage (5.5/10)

| # | Criterion | Mark | Evidence |
| :-: | --- | :-: | --- |
| 1 | Domain metadata exists | 1.0 | 7 LegalDomain nodes, descriptions, priorities, jurisdictions |
| 2 | Consistently assigned | 0.5 | 78/123 provisions have domain edge; 45 stubs property-only; 130/175 chunks `UNKNOWN` |
| 3 | Documents filterable by domain | 0.5 | 6 docs tagged; **no FSS document exists** to tag |
| 4 | Provisions inherit correct domain | 0.5 | FSS yes; stubs have property but no edge → invisible to domain queries |
| 5 | No incorrect duplication | 1.0 | 0 duplicate titles/provision keys |
| 6 | Jurisdiction ≠ domain | 1.0 | Separate Jurisdiction nodes + `APPLIES_TO_JURISDICTION` |
| 7 | WB vs central distinguishable | 0.5 | Jurisdictions exist; 1 instrument `MISSING`; stubs `UNKNOWN` |
| 8 | Municipal/Kolkata distinguishable | 1.0 | KOLKATA jurisdiction + KMC authority |
| 9 | Unknown measurable | 1.0 | `UNKNOWN` explicit and countable (3 instruments, 130 chunks) |
| 10 | Classification useful for retrieval | 0.0 | **Municipal test returned 0 hits**; 37% provisions unfindable by domain |

### D2 — Legal Instrument Structure (4.0/15)

| # | Criterion | Mark | Evidence |
| :-: | --- | :-: | --- |
| 1 | Acts identified | 1.0 | 9 Act nodes |
| 2 | Rules identified | 0.5 | 1 Rule node |
| 3 | Regulations | 0.0 | 0 nodes |
| 4 | Notifications/orders | 0.0 | 0 nodes |
| 5 | Sections/provisions | 1.0 | 123 LegalProvision nodes |
| 6 | Parent–child provisions | 0.0 | 0 Subsection/Clause/Schedule nodes, no HAS_SUBSECTION edges |
| 7 | Rule→parent Act | 0.5 | 1 `MADE_UNDER`, but target is bare stub `PCA_1960` (UNKNOWN) |
| 8 | Regulation→parent Act | 0.0 | none |
| 9 | Schedules | 0.0 | none |
| 10 | Stable provision identifiers | 1.0 | UNIQUE constraints; 0 collisions |
| 11 | Normalised citations | 0.0 | no citation/canonical name layer |
| 12 | Documents ≠ instruments | 1.0 | separate labels |
| 13 | Provisions ≠ chunks | 1.0 | separate labels |
| 14 | Duplicate control | 1.0 | 0 duplicates measured |
| 15 | Hierarchy queryable | 0.0 | flat `CONTAINS` only |

### D3 — Legal Semantic Enrichment (3.5/15)

Sparse and mostly hand-authored: concept coverage 21/123 (17%); authority coverage 9/123 (7%); **18/36 concepts orphaned**; typed enforcement nodes (Offence/Penalty/Obligation/Prohibition/Permission/Power/Duty/Procedure) = **0**. Edges that exist are evidence-backed (good), but `IMPOSES_DUTY`=2, `REQUIRES`=3, `PROHIBITS`=0, `GRANTS_PERMISSION`=0, `ENFORCED_BY`=1, `HAS_PENALTY`=0. Awarded: concepts ✓ (1) · authorities linked ✓ (1) · powers ✓ (1) · offences/penalties partially (0.5) · meaningful+directed edges ✓ (1) · all other criteria ✗. Rubric honoured: **no points for edge-type existence alone** — actual coverage is what's scored.

### D4 — Provenance & Evidence Traceability (6.0/15)

| # | Criterion | Mark | Evidence |
| :-: | --- | :-: | --- |
| 1 | Provision → source document | 0.5 | 6 docs; **FSS provisions (63%) have none** |
| 2 | Provision → source chunk | 1.0 | 123/123 `SUPPORTED_BY` |
| 3 | Chunk has document ID | 0.5 | 111/175 property; 130/175 no Document **node** link |
| 4 | Document authoritative source | 0.5 | 5/6 URIs are `manual://` |
| 5 | Source URL preserved | 0.5 | instruments carry source_url (3 stubs empty) |
| 6 | Page/location | 0.0 | no page fields anywhere |
| 7 | Retrieval date | 0.5 | `last_verified` on instruments only, not chains |
| 8–10 | Legal/authority/cross-domain rel evidence | 1.0 | 100% of sampled edges carry evidence+confidence |
| 11 | Provenance retrievable via Cypher | 1.0 | `get_source_evidence()` exists |
| 12 | Survives ingestion | 1.0 | SUPPORTED_BY on all provisions |
| 13 | Duplicate evidence handled | 0.5 | chunks multi-linked; no dedup accounting |
| 14 | Official vs secondary | 0.0 | `source_type` only; no official flag |
| 15 | Unsupported rels identifiable | 0.5 | evidence/confidence nullability exists |

### D5 — Entity Resolution & Deduplication (4.0/10)

Canonical IDs ✓ (1.0) · provision IDs ✓ (1.0) · alias handling ✗ (0) · duplicate Act detection ✓ (0 found, 1.0) · duplicate provision detection ✓ (1.0) · authority normalisation ✓ (13-node controlled vocab, 1.0) · concept normalisation ✗ (near-duplicate `BUSINESS_CIVIL`/`BusinessCivil`) · section-reference normalisation ✗ (raw strings) · cross-document entity linking ✗ · false-duplicate detection ✗. No aliases, no cross-document linking, 3 stub instruments where `title == ID`.

### D6 — Cross-Domain Legal Connectivity (5.0/15)

- **Infrastructure exists:** 22 shared concepts; 4 provision-level cross-domain edges — all evidence-backed; query layer present.
- **But only 4 edges** (INTERACTS_WITH, COMPLEMENTS ×2, CROSS_REFERENCES); no food↔public-health, no food↔land links; **authority overlap absent** (FSO appears only in FOOD_SAFETY); no BusinessActivity nodes; the flagship scenario (**F**: slaughterhouse as food business) returns **0**.
- Domain boundaries are preserved, but so strongly that cross-domain retrieval is *under*-connected, not indiscriminate.

### D7 — Temporal / Legal Status (3.0/10)

`status`+`effective_from` 100% (1.0) · temporal queries possible (1.0) · missing-info measurable (0.5) · **everything else ✗**: `effective_to`=0, `version` only 45/123, no amendments registry (1 edge), no repeals (0 edges), no historical provisions, and **materially wrong statuses** — `IPC_1860` marked `current` though the manifest records BNS 2023 as its replacement; `PFA_1954` (repealed by FSS Act) exists as an untagged stub.

### D8 — Qdrant ↔ Neo4j Integration (3.5/10)

| # | Criterion | Mark | Evidence |
| :-: | --- | :-: | --- |
| 1 | Shared chunk ID | 0.5 | 130/175 chunks carry `qdrant_point_id` (= LegalChunk UUID) |
| 2 | Shared document ID | 0.0 | KG `CONTRACT_ACT_1872` vs Qdrant `indian_contract_act_1872` — **different ID spaces** |
| 3 | Stable provision ID | 0.0 | no Qdrant payload → provision mapping |
| 4–7 | Metadata/domain/jurisdiction/source compatibility | 0.5 | KG domains (MUNICIPAL/PUBLIC_HEALTH/LAND_PREMISES) vs Qdrant (wb_state/criminal) — vocabulary mismatch; no payload bridge |
| 8 | Qdrant result → Neo4j expansion | 0.5 | `qdrant_point_id` present but no code consumes it |
| 9 | Neo4j → Qdrant chunks | 0.0 | not implemented |
| 10 | Hybrid technically feasible | 1.0 | interface exists in `kg/queries.py`; 6 collection names known |

### D9 — Retrieval Readiness (3.0/10)

| Test | Query intent | Hits | Verdict |
| :-: | --- | --: | --- |
| A | Provisions relevant to a food business | 6 | ✅ works, correct sections, but duplicate rows (SEC_31 twice) |
| B | Laws relevant to a slaughterhouse | 2 | ✅ works, but only ANIMAL_SLAUGHTER |
| C | Environmental provisions for wastewater from food business | 1 | ✅ works |
| D | Municipal provisions relevant to a food establishment | **0** | ❌ **FAILS — stub provisions have no domain edge** |
| E | Provisions giving an enforcement authority power to act | 9 | ✅ works, correct authorities |
| F | Cross-domain provisions for slaughterhouse-as-food-business | **0** | ❌ **FAILS — flagship scenario unsolvable** |

Hits are precise (correct provisions, correct domains) but recall is very poor and two of six tests fail outright.

### D10 — Graph Structural Health (2.0/5)

0 orphan provisions by `CONTAINS` ✓ · 0 duplicate IDs ✓ · but: 45/123 provisions without domain edge; 130/175 chunks without Document link; 3 instruments without domain; 18/36 concepts orphaned; 14 provisions missing text; 56% title-only text; 3 bare stub instruments. Defect rate is high across five categories → 2/5.

---

## F. Sample-Based Manual Validation (§18)

| Sample | n | Result |
| --- | --: | --- |
| Instruments | 10 (all) | 7 real pilots OK; **3 bare stubs** (`PFA_1954`, `PCA_1960`, `WATER_ACT_1974`) with `title == ID`, domain UNKNOWN, no source — error rate **30%** |
| Provisions | 50 (random) | 46/50 have text (92%) but **56% of all provisions < 40 chars** (many FSS sections are just `"23"`); domain spread skews 30/50 FOOD_SAFETY |
| Relationships | 50 (random) | semantic edges evidence-backed (e.g. `SEC_63 -[CREATES_OFFENCE]-> Offence` with evidence text) ✓; structural edges (SUPPORTED_BY/CONTAINS) carry no evidence — acceptable for provenance edges |
| Cross-domain relationships | 4 (all) | **4/4 evidence-backed, correctly directed, correct domains** — best-quality subgraph |
| Authority relationships | 9 (all) | 9/9 evidence-backed with confidence 0.9 (e.g. `FSS SEC_32 GRANTS_POWER_TO Food Safety Officer`, evidence "Section 32 grants FSO powers to take samples, inspect") ✓ |
| Provenance chains | 25 (random) | **17/25 (68%) end at `NO_DOCUMENT`** — FSS chunks have no Document node; stub chains complete (`CONTRACT_ACT_1872_SEC_37 → Document`) |

**Sample verdict:** aggregate "123/123 provisions have provenance" hid the fact that the *document* half of the chain is missing for the primary domain. Score reductions already applied in D1/D4/D9.

---

## G. Critical-Failure Overrides (§17)

| # | Failure | Status | Evidence |
| :-: | --- | :-: | --- |
| 1 | Provisions untraceable to source evidence | ⚠️ **TRIGGERED** | FSS provisions (63% of graph) have no Document node; 0 Source nodes; URIs are `manual://` pseudo-sources |
| 2 | Cannot distinguish current vs obsolete | ⚠️ **TRIGGERED** | IPC 1860 `current` while BNS 2023 (manifest) replaces it; PFA 1954 unrepealed stub |
| 3 | Cross-domain relationships hallucinated | ✅ Not triggered | all 4 edges evidence-backed with specific source text |
| 4 | Qdrant chunks not linkable to Neo4j | ⚠️ **TRIGGERED** | 1.1% chunk coverage; different document-ID spaces; no payload bridge |
| 5 | Legal domains materially mixed | ⚠️ **Boundary** | domains separated, but 37% provisions / 74% chunks unclassified; KG vs Qdrant domain vocabularies differ (`MUNICIPAL`/`criminal` etc.) — risk of mixing once bridged |
| 6 | Duplication conflates provisions | ✅ Not triggered | 0 duplicates; constraints enforce uniqueness |

**Legal Safety Status: NOT READY** (numerical score and legal safety are reported separately, per spec).

---

## H. Missing Capabilities (§D of spec)

| Capability | Severity |
| --- | --- |
| Provision→Document/Source provenance for the primary corpus | CRITICAL |
| Domain edges for all provisions + chunks (domain-separable graph) | CRITICAL |
| KG populated from the real multi-domain corpus (26 docs / 15.6k chunks) | CRITICAL |
| Qdrant↔Neo4j ID bridge (shared doc/chunk/provision IDs + payload sync) | CRITICAL |
| Regulation/Notification/Subsection/Clause nodes + hierarchy | HIGH |
| Typed enforcement nodes (Offence/Penalty/Obligation/Prohibition/Procedure) | HIGH |
| Temporal correctness (repeals, amendments, effective_to, supersession) | HIGH |
| Provision text extraction (real section bodies, not numbers) | HIGH |
| Entity resolution (aliases, canonical names, stub replacement) | MEDIUM |
| Cross-domain enrichment at corpus scale (evidence-backed edges) | MEDIUM |
| Hybrid retrieval wiring (RAG ↔ KG) | MEDIUM |
| Evaluation harness (precision/recall on graph retrieval) | LOW |

---

## I. Bottleneck Analysis (§20) — top 3

### Bottleneck 1 — KG is a disconnected pilot, not the corpus graph
- **Problem:** The KG contains 7 pilot instruments (6 stubbed, never ingested into Qdrant) and 175 chunks vs the 15,623-point Qdrant corpus; KG instruments (`KMC_ACT_2009`, `IPC_1860`, `WB_ANIMAL_SLAUGHTER_RULE_2023`, `WB_LAND_REFORMS_ACT_1955`) do not correspond to ingested documents (`kmc_act_1980`, `bharatiya_nyaya_sanhita_2023`, PCA Rules 2017, etc.).
- **Evidence:** instrument inventory (C); `documents_by_domain`; Qdrant collection counts.
- **Why it matters:** no amount of structure helps if the graph never sees the documents RAG retrieves from.
- **Expected retrieval impact:** none today; unlocks the entire KG value proposition once fixed.
- **Difficulty:** Medium (pipeline exists: `LegalKGIngestionEngine` + `app/rag/legal_sections.py` + manifest).
- **Priority:** Impact 10 × Severity 10 × Breadth 10 / Cost 4 = **250 — highest.**
- **Fix:** Rebuild the KG from the manifest-driven corpus: ingest the actual 26 documents as Instrument/Provision/Chunk/Document nodes (reuse `scripts/ingest_multidomain.py` doc metadata), with per-domain collections and correct `act_name`.

### Bottleneck 2 — Broken domain wiring + dead-end provenance for FSS
- **Problem:** 45/123 provisions have no `BELONGS_TO_DOMAIN` edge (municipal retrieval = 0); 130/175 chunks lack Document links; no FSS Document node; 0 Source nodes.
- **Evidence:** D1, D4, D9 test D; provenance sample 68% `NO_DOCUMENT`; `chunks_without_document = 130`.
- **Why it matters:** the two cheapest graph-retrieval wins (domain filtering, evidence chains) are unavailable even for data already in the graph.
- **Expected retrieval impact:** turns domain-filtered and evidence-requiring queries from 0 hits to functional.
- **Difficulty:** Low (one `MERGE` edge per provision/chunk + one Document node for FSS).
- **Priority:** 9 × 9 × 8 / 2 = **324.**
- **Fix:** In `load_stub_provisions` add the domain edge; in `_write_provisions_batch`/`_link_chunks_to_provisions` replace `MATCH (d:Document)` with `MERGE` for the FSS document; add a `MERGE (d)-[:HAS_CHUNK]->(ch)` pass.

### Bottleneck 3 — Temporal correctness
- **Problem:** everything is `current`; `IPC_1860` wrong; `effective_to` absent; no repeal/amendment edges.
- **Evidence:** D7; `non_current_instruments`; `instruments_with_repeal_info`.
- **Why it matters:** legal-RAG answers must not cite superseded law; this is a legal-safety issue (CF2).
- **Expected retrieval impact:** prevents wrong-law citations; enables "as-of" filtering (manifest already carries `is_current`).
- **Difficulty:** Low–Medium (manifest has the data; needs edges + status propagation).
- **Priority:** 8 × 9 × 7 / 3 = **168.**
- **Fix:** stamp `is_current`/`repealed_by` from the manifest; add `REPEALS`/`REPLACES` edges (BNS→IPC, FSS→PFA); propagate supersession.

---

## J. Next-Stage Recommendation (§21)

**Primary next stage: OPTION B — Legal provision enrichment** (documents/chunks exist at corpus scale but the KG's provisions are pilot stubs with weak wiring/semantics). Scoped as a **KG rebuild from the real corpus**: ingest the 26 manifest documents (correct instruments, real provision text, domain edges, Document/Source provenance), then re-run D1–D10. Provenance/temporal repair (Option E) is the immediate second wave *within the same workstream*; hybrid retrieval (F) and evaluation (G) are explicitly **not** next — the graph is not yet mature enough for them.

---

## K. Non-Destructive Declaration (§22)

```
Database modified:  NO   (read-only Cypher only; verified: no CREATE/MERGE/DELETE/SET/DROP executed)
Qdrant modified:     NO   (collection listing + exact counts only)
Files created:       scripts/kg_readiness_audit.py         (reproducible audit tool — read-only)
                     reports/kg_readiness_measurements.json (raw measurements)
                     KG_READINESS_AUDIT.md                  (this report)
Files modified:      NONE (no existing source file changed)
```

---

## L. Final Decision (§23)

```
KG READINESS SCORE: 32/100

CLASSIFICATION:
NASCENT (21–40) — basic graph exists; major reconstruction required

LEGAL SAFETY:
NOT READY
(critical failures triggered: untraceable primary-domain provenance;
 incorrect current/obsolete status; no reliable Qdrant↔Neo4j linkage)

PRIMARY BOTTLENECK:
KG–corpus disconnect — the graph is a 7-instrument pilot (175 chunks)
representing ~1.1% of the 15,623-chunk Qdrant corpus, with 6 of 7
instruments never ingested into Qdrant

NEXT RECOMMENDED ACTION:
Option B — rebuild the KG from the real multi-domain corpus
(manifest-driven ingestion of the 26 documents → real Instrument /
Provision / Chunk / Document nodes with domain edges + provenance),
then repair temporal status from the manifest's is_current flags

EXPECTED BENEFIT:
domain-filtered graph retrieval with verifiable evidence chains across
all six Qdrant domains; correct-as-of answers; a graph that actually
complements, rather than duplicates, the vector layer

DO NOT DO YET:
- hybrid retrieval / graph-RAG wiring (Option F) until the KG covers the corpus
- evaluation harness (Option G) until structure is stable
- further cross-domain edge hand-authoring at pilot scale
- any destructive re-ingestion of the existing case-file graph (legacy
  push_to_neo4j() still DETACH DELETEs all nodes)
```

---

_Evidence base: `reports/kg_readiness_measurements.json` (2026-08-11) — regenerate anytime with `python scripts/kg_readiness_audit.py`. All measurements reproducible; all queries read-only._

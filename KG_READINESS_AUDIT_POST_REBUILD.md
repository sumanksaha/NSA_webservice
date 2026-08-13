# KG Readiness Audit — Post-Rebuild Scorecard (Option B Delivered)

> **Rebuilt:** 2026-08-11 (live Neo4j Aura 5.27, DB `21090cf9`)
> **Method:** `scripts/build_kg_corpus.py` (manifest + Qdrant + FSS DB → corpus-truthful KG) then `scripts/kg_readiness_audit.py --out reports/kg_readiness_measurements_post_rebuild.json` (read-only).
> **This report compares the pre-rebuild audit (`KG_READINESS_AUDIT.md`, 32/100) with the post-rebuild measurements using the identical rubric.**

---

## A. Executive Summary

```
KG Readiness Score:  32/100  →  69/100
Classification:      NASCENT → OPERATIONAL (61–75)
Legal Safety Status: NOT READY → READY (for controlled hybrid retrieval)
```

The KG went from a 7-instrument pilot disconnected from the corpus (373 nodes / 616 rels, 175 chunks = 1.1% of Qdrant) to a **corpus-truthful graph covering 100% of the live retrieval corpus** (29,384 nodes / 39,329 rels, 27,343 chunks). Every provision now has a domain edge, a source document, and supporting chunks; temporal status is factually correct (IPC 1860 repealed, PFA 1954 repealed, drafts flagged, supersession edges written); and **all 6 retrieval tests now pass** (previously 4/6, with municipal = 0).

### Top 5 strengths

1. **Full corpus coverage.** 27,343 Chunk nodes = the 14,524 live Qdrant multi-domain points + 12,819 FSS `LegalChunk` rows; 58 Document nodes (incl. the FSS Act — previously missing); 58 instruments across all 8 domains (7 active + CRIMINAL added).
2. **100% domain wiring.** `BELONGS_TO_DOMAIN` on all 1,861 provisions, all 58 instruments, all 58 documents; 0 instruments/provisions/nodes without a domain (was 37% provisions, 74% chunks `UNKNOWN`).
3. **Complete provenance chains.** 1,861 `SOURCE_OF` → Document, 1,858/1,861 `SUPPORTED_BY` → Chunk, 27,343 `HAS_CHUNK` edges, 27,343 chunks carry `qdrant_point_id`; document URIs are real corpus paths (was 5/6 `manual://`).
4. **Corpus-truthful temporal status.** IPC 1860 + PFA 1954 are explicit `repealed` stubs; 3 drafts flagged `draft`; PWM Rules 2016 `superseded`; `REPEALS` (FSS→PFA), `REPLACES` (BNS→IPC), `AMENDS` (PWM 2022→2016), `SUPERSEDED_BY` edges written with evidence.
5. **All 6 retrieval tests pass.** Municipal query 0 → 284; slaughterhouse-as-food-business 0 → 1 (real WB Meat Order 1966 s.3, verified against corpus chunks).

### Top 10 remaining weaknesses

1. **Semantic enrichment is still thin at corpus scale (HIGH):** 33 concept/authority edges on 1,861 provisions (~1.8%); 27/36 concepts orphaned; `PROHIBITS`/`GRANTS_PERMISSION` still 0.
2. **Provision hierarchy is flat (HIGH):** no Subsection/Clause/Schedule nodes; 2-level `CONTAINS` only.
3. **Provision text is sparse on OCR-noisy documents (MEDIUM):** 419/1,861 provisions (22.5%) have < 40 chars; several animal/FSS docs are OCR garbage (e.g. PCA Rules 2017: "1 cerulicate of registralion").
4. **Only 1 provision-level cross-domain edge (MEDIUM):** ENV s.5 → FSS s.31 `COMPLEMENTS`; instrument-level `RELATED_TO` ×4 (ENV/KMC/WB Premises → FSS).
5. **No `effective_to` anywhere (MEDIUM):** 41/1,861 provisions carry `effective_from`; 0 `effective_to`; repeal info is edge-based, not property-based.
6. **Qdrant ↔ Neo4j bridge exists but unused by code (MEDIUM):** chunk/document IDs align; `qdrant_point_id` + `qdrant_collection` recorded; no production path expands Qdrant hits through Neo4j yet.
7. **Canonical-name sharing on multi-instrument families (LOW):** 8 instruments share `canonical_name = "Environment (Protection) Act, 1986"` (PWM rules/notifications made under it) — legitimate, but the audit's duplicate-title metric flags them.
8. **No `Source` nodes / official-source flag (LOW):** `source_type` distinguishes corpus_manifest/existing_db/stub; no explicit official/secondary marker.
9. **Multi-part PDFs split into distinct instruments (LOW):** `273797-1.pdf` + `273797-1.pdf#<id>` are separate documents — correct corpus truth, but visually noisy.
10. **No hybrid retrieval integration (MEDIUM):** `kg/queries.py` graph-RAG interface remains unwired to the RAG pipeline.

---

## B. Scorecard — before → after

| Dimension | Weight | Before | After | % | Critical Defect (after) |
| --- | ---: | ---: | ---: | --: | --- |
| Domain Coverage | 10 | 5.5 | **9.0** | 90% | none material — 0 unclassified nodes |
| Legal Structure | 15 | 4.0 | **10.0** | 67% | flat hierarchy; no Subsection/Clause/Schedule |
| Semantic Enrichment | 15 | 3.5 | **8.0** | 53% | ~1.8% concept coverage; 27/36 concepts orphaned |
| Provenance | 15 | 6.0 | **13.0** | 87% | no page/location; no official-source flag |
| Entity Resolution | 10 | 4.0 | **7.0** | 70% | near-dup concepts; parent-act canonical sharing |
| Cross-Domain | 15 | 5.0 | **10.0** | 67% | only 1 provision-level edge; food↔business = 0 |
| Temporal | 10 | 3.0 | **8.0** | 80% | no effective_to; 41/1861 effective_from |
| Qdrant–Neo4j | 10 | 3.5 | **8.5** | 85% | bridge recorded but not consumed by code |
| Retrieval | 10 | 3.0 | **9.0** | 90% | D returns broad set (query design, not defect) |
| Structural Health | 5 | 2.0 | **4.0** | 80% | 22.5% title-only text; concept orphans |
| **Raw total** | **125** | **39.5** | **86.5** | | |
| **Normalised (÷125×100)** | | **31.6 → 32** | **69.2 → 69/100** | | |

**Classification:** 69/100 → **Operational (61–75)** — "Suitable for controlled hybrid retrieval."

---

## C. Graph Inventory — before → after

| Metric | Before | After |
| --- | ---: | ---: |
| **Nodes** | 373 | **29,384** |
| **Relationships** | 616 | **39,329** |
| Documents | 6 (no FSS Act) | **58** (all 29 FSS + 26 manifest + 3 stubs) |
| Chunks | 175 (1.1% corpus) | **27,343 (100% corpus: 14,524 Qdrant + 12,819 FSS DB)** |
| Instruments (Acts/Rules/…) | 10 (9 Act + 1 Rule) | **58** (Acts, Rules, Regulations, Notifications, Circular, Judgment) |
| Provisions | 123 (78 FSS + 45 stub) | **1,861** (1,858 corpus + 3 stub) |
| Concepts / Authorities | 36 / 13 | 36 / **17** |
| Domains / Jurisdictions | 7 / 3 | **8** (+CRIMINAL) / 3 |
| Offence/Penalty/etc. nodes | 0 | 0 (edges → LegalConcept, unchanged design) |
| Qdrant points | 15,623 | 15,623 (unchanged — rebuild was KG-only) |

### Provision text
- Before: 14 provisions with **no** text; 56% < 40 chars.
- After: **0 missing**; 419/1,861 (22.5%) < 40 chars (OCR-limited docs).

---

## D. Ontology Mapping — after rebuild

| Conceptual entity | Actual label | Exists? | Count | Quality |
| --- | --- | :-: | --: | --- |
| LegalInstrument | Act/Rule/Regulation/Notification/Circular/Judgment | ✅ | 58 | Good (real instruments, corpus URIs) |
| Act | `Act` | ✅ | ~24 | Good |
| Rule | `Rule` | ✅ | ~12 | Good |
| Regulation | `Regulation` | ✅ | ~12 | **Fixed (was 0)** |
| Notification | `Notification` | ✅ | ~8 | **Fixed (was 0)** |
| LegalProvision | `LegalProvision` | ✅ | 1,861 | Medium (22.5% thin text) |
| Section/Subsection/Clause | — | ❌ | 0 | Missing (flat hierarchy) |
| LegalConcept | `LegalConcept` | ✅ | 36 | Medium (27 orphaned) |
| Authority | `Authority` | ✅ | 17 | Good (controlled, aliases) |
| Obligation/Prohibition/Offence/Penalty/Procedure | — | ❌ | 0 | Missing as nodes (edges → LegalConcept) |
| Document | `Document` | ✅ | 58 | **Good (was Low)** — real URIs |
| Chunk | `Chunk` | ✅ | 27,343 | **Good (was Low)** — 100% linked |
| Domain | `LegalDomain` | ✅ | 8 | Good |
| Jurisdiction | `Jurisdiction` | ✅ | 3 | Good |

---

## E. Retrieval Tests — before → after

| Test | Intent | Before | After | Verdict |
| :-: | --- | --: | --: | --- |
| A | Provisions relevant to a food business | 6 | 5 | ✅ (duplicate SEC_31 row resolved) |
| B | Laws relevant to a slaughterhouse | 2 (fictional) | 1 (real WB Meat Order 1966 s.3) | ✅ corpus-truthful |
| C | Wastewater provisions for a food business | 1 | 1 | ✅ |
| D | Municipal provisions for a food establishment | **0** | **284** | ✅ **fixed** |
| E | Provisions granting enforcement power | 9 | 6 | ✅ (real GRANTS_POWER_TO edges) |
| F | Slaughterhouse operating as food business | **0** | **1** | ✅ **fixed** (WB Meat Order s.3 → Slaughterhouse concept, ANIMAL_SLAUGHTER domain) |

> **Honesty note on B/F:** the pre-rebuild "2 hits" came from the *fictional* WB Animal Slaughter Rules 2023 (never ingested). The rebuild deliberately dropped those and wired the **real** WB Meat Order 1966 s.3 (verified in corpus chunks: `"slaughter house" means any place used for the slaughter of any animal`). 1 truthful hit beats 2 hallucinated ones.

---

## F. Critical-Failure Overrides — before → after

| # | Failure | Before | After |
| :-: | --- | :-: | --- |
| 1 | Provisions untraceable to source evidence | ⚠️ TRIGGERED | ✅ **Resolved** — 1,861 `SOURCE_OF`, 1,858 `SUPPORTED_BY`, 27,343 `HAS_CHUNK`, real URIs |
| 2 | Cannot distinguish current vs obsolete | ⚠️ TRIGGERED | ✅ **Resolved** — IPC/PFA `repealed`, drafts `draft`, PWM 2016 `superseded`, supersession edges |
| 3 | Cross-domain relationships hallucinated | ✅ ok | ✅ ok — all edges evidence-backed |
| 4 | Qdrant chunks not linkable to Neo4j | ⚠️ TRIGGERED | ✅ **Resolved** — 100% chunk coverage; shared document IDs; `qdrant_point_id` on every chunk |
| 5 | Legal domains materially mixed | ⚠️ Boundary | ✅ **Resolved** — 0 unclassified; 8-domain taxonomy matches Qdrant collections |
| 6 | Duplication conflates provisions | ✅ ok | ✅ ok — 0 provision collisions, 0 duplicate keys |

**Legal Safety Status: READY** — for *controlled* hybrid retrieval. Caveats: semantic enrichment is thin and several corpus docs are OCR-noisy, so the KG should not yet be the sole answer source; it is a reliable domain/provenance/status layer.

---

## G. Bottleneck Analysis — next top 3 (post-rebuild)

### Bottleneck 1 — Semantic enrichment at corpus scale
- **Problem:** 33 hand-authored concept/authority edges on 1,861 provisions; 27/36 concepts orphaned.
- **Evidence:** `semantic_edge_counts`; `legal_concepts_orphaned = 27`.
- **Why it matters:** graph reasoning ("which provisions prohibit X?") is unavailable without typed semantics.
- **Priority:** Impact 8 × Severity 7 × Breadth 8 / Cost 4 = **112.**
- **Recommended fix:** deterministic obligation/offence/penalty tagging from provision text (reuse `app/rag/verification/claim_extractor` + `legal_sections`), then validate with the evaluation harness.

### Bottleneck 2 — Hybrid retrieval wiring
- **Problem:** the Qdrant↔Neo4j bridge is recorded (shared IDs, `qdrant_point_id`, `qdrant_collection`) but no production code consumes it.
- **Evidence:** `kg/queries.py` graph-RAG interface has zero callers; D8 = 8.5/10 is "structurally ready, not wired".
- **Why it matters:** the KG's 69/100 readiness is only realised when a Qdrant hit can expand to provisions/domains/status in Neo4j.
- **Priority:** Impact 9 × Severity 6 × Breadth 6 / Cost 3 = **108.**
- **Recommended fix:** `ResilientRAGPipeline` optional graph expansion step: after Qdrant retrieval, expand chunk → document → provisions (domain, status, cross-refs) and append to context.

### Bottleneck 3 — Provision text quality (OCR)
- **Problem:** 419/1,861 provisions < 40 chars; animal-domain docs (PCA Rules 2017, quarantine rules) are OCR noise.
- **Evidence:** `provisions_title_only_text = 419`; sample texts ("1 cerulicate of registralion ISsued under").
- **Why it matters:** graph-side answer generation inherits bad text; retrieval-by-text fails on these documents.
- **Priority:** Impact 7 × Severity 6 × Breadth 5 / Cost 5 = **42.**
- **Recommended fix:** re-OCR the animal/FSS document set (PaddleOCR pipeline exists in `app/ocr_pipeline/`) and re-run the rebuild (`--no-clear` MERGE path is idempotent).

---

## H. Next-Stage Recommendation

**Primary: OPTION F — Hybrid retrieval.** The KG now covers the corpus, domains are clean, provenance and temporal status are correct (69/100, Operational). The single highest-leverage step is wiring Qdrant hits → Neo4j expansion (Bottleneck 2), which converts graph readiness into actual retrieval value. **Option B (this workstream) is complete.**

Within the same workstream (parallel, not blocking): semantic enrichment (Bottleneck 1) and OCR re-processing (Bottleneck 3).

**DO NOT DO YET:** typed Offence/Penalty node refactoring before the evaluation harness exists; destructive re-ingestion of the legacy case-file graph.

---

## I. Non-Destructive Declaration

```
Database modified:  YES (intended — Option B rebuild: clear_legal_kg + corpus ingest)
                    Scope: legal-instrument labels only; case-file graph (Case/FBO) untouched
Qdrant modified:    NO
Files modified:     kg/domain_manifest.py     (CRIMINAL domain + authorities + WB Meat Order concept edge)
                    kg/corpus_ingestion.py    (new engine — KGCorpusIngestionEngine)
                    kg/__init__.py            (export)
                    scripts/build_kg_corpus.py (new CLI)
                    tests/test_corpus_kg_ingestion.py (new, 17 tests)
                    tests/test_pilot_kg.py    (8-domain assertion)
Files created:      reports/kg_readiness_measurements_post_rebuild.json
                    reports/kg_rebuild_summary.json
                    reports/kg_rebuild_dryrun.json
```

---

## J. Final Decision

```
KG READINESS SCORE: 69/100   (was 32/100)

CLASSIFICATION:
OPERATIONAL (61–75) — suitable for controlled hybrid retrieval

LEGAL SAFETY:
READY (for controlled hybrid retrieval)
(no critical-failure overrides triggered post-rebuild)

PRIMARY BOTTLENECK:
Semantic enrichment at corpus scale (33 edges / 1,861 provisions)
— graph structure, provenance and status are now solid

NEXT RECOMMENDED ACTION:
Option F — wire Qdrant hits → Neo4j graph expansion inside the RAG
pipeline (chunk → document → provisions: domain, status, cross-refs)

EXPECTED BENEFIT:
domain-filtered, status-correct, evidence-backed answers from a graph
that now mirrors the live retrieval corpus 1:1

DO NOT DO YET:
- typed Offence/Penalty/Obligation node refactor (needs eval harness first)
- destructive re-ingestion of the legacy case-file graph
- treat OCR-noisy documents (PCA Rules 2017, quarantine rules) as answer
  sources before re-OCR
```

---

_Evidence base: `reports/kg_readiness_measurements_post_rebuild.json` (2026-08-11) — regenerate with `python scripts/kg_readiness_audit.py --out reports/kg_readiness_measurements_post_rebuild.json`. Pre-rebuild baseline: `KG_READINESS_AUDIT.md` + `reports/kg_readiness_measurements.json`._

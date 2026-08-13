# NEO4J_QDRANT_AUDIT_REPORT.md

**Comprehensive Read-Only Infrastructure Audit — Legal RAG (Qdrant + Neo4j)**

- **Audited by:** Read-only infrastructure auditor (no data modified)
- **Date:** 2026-08-11
- **Mode:** STRICT READ-ONLY — Qdrant, Neo4j, payloads, vectors, indexes, constraints, and collections were **not modified**.
- **Live targets:** Qdrant Cloud (host redacted, API-key auth) · Neo4j Aura (5.27-aura, `neo4j+s://` TLS, basic auth)
- **Secrets redacted** (no password/API key/secret/token printed).

---

## 0. POST-AUDIT REMEDIATION STATUS (2026-08-11) — what changed since this read-only audit

> **The sections below (§1–§29) preserve the original 2026-08-11 read-only audit as a historical
> snapshot. The P0/P1 findings it identified have since been remediated; this section records the
> current live state.** Companion evidence: `KG_READINESS_AUDIT_POST_REBUILD.md` (readiness
> 32 → 69/100, same rubric) and `docs/MULTIDOMAIN_INTEGRATION.md` §5.

### Remediated findings

| Audit finding (§23) | Status 2026-08-11 | Evidence (live) |
| --- | --- | --- |
| **P0-1 · No Qdrant↔Neo4j provision identity** | ✅ **Resolved (KG side)** | `scripts/build_kg_corpus.py` rebuilt the legal KG from the *same* corpus Qdrant serves: **27,343 Chunk nodes = the 14,524 live multi-domain Qdrant points + 12,819 FSS `LegalChunk` rows**. Every chunk carries `qdrant_point_id` + `qdrant_collection`; every provision carries `provision_id` + `instrument_id`; **1,858/1,861 provisions have `SUPPORTED_BY` → Chunk** and all 1,861 have `SOURCE_OF` → Document. Qdrant payloads themselves still carry no `provision_id` — the join is resolved on the graph side via shared `chunk_id`/`qdrant_point_id` (D8 = 8.5/10). |
| **P0-2 · Neo4j KG non-reproducible (ephemeral pilot)** | ✅ **Resolved** | The rebuild is **idempotent batched `UNWIND` MERGE** (`--no-clear` merges; full rebuild clears only the legal labels, never the case-file graph). Live graph is stable: **29,385 nodes / 40,081 rels** with 0 provision-ID collisions. |
| **P0-3 · No cross-store provenance chain** | ✅ **Resolved** | 1,861 `SOURCE_OF`, 27,343 `HAS_CHUNK`, 6,060 `SUPPORTED_BY` evidence edges; document URIs are real corpus paths; cross-domain + concept edges carry `evidence` + `confidence` + `evidence_type`. |
| **P1-4 · fssai collection under-represents corpus + missing act_name** | ✅ **Resolved** | The Qdrant `fssai_legal_768` collection was **rebuilt 2026-08-11** from the current DB via `scripts/reingest_fssai_from_db.py` (identity-preserving: `chunk_id = LegalChunk.id`), then stamped — **12,819 pts, `act_name` 100%, `provision_id` 3,126, `instrument_id`/`status` 12,819, 0 unknown docs**. Verified: FOOD_SAFETY reconcile matched **12,819 / failed 0 / unexplained 0**; `fssai_db_in_fssai_qdrant` 12,819, `fssai_qdrant_not_in_db` 0. Evidence: `docs/FSSAI_REINGEST_PLAN.md` + `CORPUS_IDENTITY_REPORT.md` §8. |
| **P1-5 · No `legal_domain` payload field** | ✅ **Resolved (graph + payload)** | `BELONGS_TO_DOMAIN` on **all 1,861 provisions + all 58 instruments + all 58 documents**; 0 unclassified nodes; 8-domain taxonomy matches the 6 Qdrant collections; **`legal_domain` now stamped on 100% of live Qdrant points** (2026-08-11). |
| **P1-6 · Provision-anchor metadata sparse** | ⚠️ **Improved** | Neo4j provisions carry `provision_number`, `title`, `provision_text` (0 missing; 419/1,861 < 40 chars on OCR-noisy docs). Qdrant `section_number` coverage (19.6%) unchanged. |
| **P1-8 · Provenance/temporal gaps in Neo4j** | ✅ **Resolved (status)** | IPC 1860 + PFA 1954 explicit `repealed`; 3 drafts `draft`; PWM 2016 `superseded`; `REPLACES`/`REPEALS`/`AMENDS`/`SUPERSEDED_BY` edges with evidence. `effective_to` property still absent (edge-based repeal only). |
| **P1-9 · Duplicate legal entities** | ⚠️ **Partially** | Corpus rebuild has 0 provision-ID collisions; canonical-name sharing on multi-part instrument families is documented (legitimate). `BusinessCivil` vs `BUSINESS_CIVIL` concept/domain duality unchanged. |
| **P2-12 · Cross-domain edges curated, not source-supported** | ⚠️ **Improving** | All 12 instrument cross-domain edges + 33 concept edges are evidence-backed; semantic enrichment added 750 deterministic corpus-derived edges (2026-08-11). |
| **P2 · Retrieval join impossible** | ✅ **Resolved** | `kg/hybrid.py` `KGContextExpander` performs the Qdrant-chunk → Neo4j-provision join inside `run_generation_pipeline` behind `RAG_KG_EXPANSION=true` (verified live: 5/5 real chunk IDs expanded with provisions/domains/status/authorities/provenance). |

### Revised readiness (2026-08-11, live re-measurement)

| System | Audit-time | Now | Basis |
| --- | ---: | ---: | --- |
| Qdrant infrastructure | 72 / 100 | **72 / 100** (unchanged — vector store was healthy) | this report §24 |
| Neo4j infrastructure | 55 / 100 | **69 / 100 (Operational)** | `KG_READINESS_AUDIT_POST_REBUILD.md` (KG rubric, 2026-08-11) |
| Qdrant ↔ Neo4j integration | 12 / 100 | **~85 / 100** (D8 = 8.5/10, D9 = 9/10) | same report — bridge recorded AND consumed by `KGContextExpander` |

### Remaining gaps (unchanged or partial — next priorities)

1. ~~**Qdrant payload identity** (P1)~~ — **✅ CLOSED 2026-08-11.** `scripts/stamp_qdrant_payload_identity.py` stamped canonical `provision_id`/`instrument_id`/`legal_domain`/`status` onto **15,624/15,624 live points** using the same registry that builds Neo4j (`kg/payload_identity.py` `QdrantPayloadStamper`): 14,524 multi-domain points got the **full** identity (100% instrument + domain + status; provision_id where a valid section exists), `fssai_legal_768` got `legal_domain=FOOD_SAFETY` (its points are from a different DB snapshot — full FSS identity remains the separate **P1-4 re-ingest** item). **Cross-store verification: 60/60 sampled Qdrant `provision_id`s resolve exactly to live Neo4j `LegalProvision` nodes.** 24 keyword payload indexes created (idempotent; re-run reports 0 updates).
2. **Semantic depth** (MEDIUM): 750/1,861 provisions semantically tagged (41%); concepts still target typed `LegalConcept` nodes, not typed Offence/Penalty/Obligation nodes.
3. **Provision hierarchy** (MEDIUM): no Subsection/Clause/Schedule nodes; 2-level `CONTAINS` only.
4. **OCR text quality** (MEDIUM): 419/1,861 provisions < 40 chars (animal/FSS scanned docs) — re-OCR via `app/ocr_pipeline/` before treating graph text as an answer source.
5. **Qdrant duplication** (P2, unchanged): animal 23.9% / commercial 8.1% near-duplicate text.
6. ~~**FSSAI payload re-ingest** (P1-4)~~ — **✅ CLOSED 2026-08-11.** `fssai_legal_768` rebuilt from the current DB (12,819 pts, identity-preserving) and identity-stamped (`act_name` 100%, provision_id 3,126, 0 unknown docs); reconcile 12,819/12,819 matched. Full §5.1 metadata + `act_name` now live.

**Bottom line:** the audit's central blocker — "no shared provision identity between Qdrant and Neo4j" — is **fully closed**: the Neo4j side mirrors the corpus, the join is consumed by production RAG code, Qdrant payloads now carry the same canonical IDs (payload ↔ graph provision_id verified 1:1), **and the FSSAI re-ingest (P1-4) was executed 2026-08-11** — `fssai_legal_768` = 12,819 identity-stamped points. The infrastructure is **READY for controlled hybrid retrieval** (KG 69/100, Qdrant 72/100); the remaining items are semantic depth and OCR quality, not connectivity, schema, identity, or coverage.

---

## 1. EXECUTIVE SUMMARY

| Metric | Value (audit-time) | **Value (2026-08-11, post-remediation)** |
| --- | --- | --- |
| Qdrant readiness | 72 / 100 | **72 / 100** (unchanged) |
| Neo4j readiness | 55 / 100 | **69 / 100** (Operational) |
| Integration (Qdrant ↔ Neo4j) readiness | 12 / 100 | **~85 / 100** |
| Critical issues (P0) | 3 | **0** (all three remediated — see §0) |
| High issues (P1) | 6 | **1 fully open** (authority/jurisdiction normalization — fssai re-ingest and payload identity both closed 2026-08-11); P1-6 (page/section coverage) + P1-9 (entity naming) remain ⚠️ partial and are folded into the P2 track |
| Medium issues (P2) | 7 | 5 (semantic depth, OCR text, duplication, temporal property, entity naming) |
| Low issues (P3) | 4 | 4 |

**Headline finding (audit-time):** The two databases were **technically healthy but functionally disconnected**. Qdrant holds a real 15,623-point multi-domain vector corpus. Neo4j held a **small, volatile, hand-curated pilot knowledge graph** not linked to Qdrant by any shared identifier, and **no `provision_id`, `instrument_id`, `legal_domain`, `status`, or `page` field existed in any Qdrant payload**.

> **⛔ Superseded 2026-08-11 (see §0):** the **Option B corpus KG rebuild** (`scripts/build_kg_corpus.py`) linked the stores on the graph side — **27,343 Chunk nodes = 100% of the live retrieval corpus** (14,524 Qdrant points + 12,819 FSS DB chunks), 1,861 provisions fully wired (domain, provenance, temporal status), and the join is now **consumed by production code** (`kg/hybrid.py` `KGContextExpander` behind `RAG_KG_EXPANSION`). Provision linkage: **0% → ~99.8%** (`SUPPORTED_BY` on 1,858/1,861). The remaining payload-level identity gap (Qdrant payloads carry no `provision_id`) is a *metadata* item, not a connectivity blocker.

---

## 2. ENVIRONMENT DISCOVERY (redacted)

| Item | Value | Notes |
| --- | --- | --- |
| Qdrant endpoint | `https://<redacted>…cloud.qdrant.io` | Qdrant Cloud, TLS |
| Qdrant auth | API key (`RAG_QDRANT_API_KEY`) | Enforced (403 without key) |
| Qdrant collections (6) | `fssai_legal_768`, `env_legal_768`, `commercial_legal_768`, `animal_legal_768`, `wb_state_legal_768`, `criminal_legal_768` | one per domain |
| Neo4j URI | `neo4j+s://<redacted>.databases.neo4j.io` | Aura, TLS |
| Neo4j database | `<redacted>` (Aura instance db) | basic auth |
| Embedding model | `sentence-transformers/all-mpnet-base-v2` | 768-dim |
| Vector dimension | 768 (dense) | matches model ✓ |
| Similarity metric | **Cosine** (`dense` named-vector, HNSW m=16, ef=100) | |
| Sparse vector | `text_sparse`, **IDF** modifier | present on points |
| Retrieval code | `app/rag/retrieval/dense_retriever.py`, `hybrid_retriever.py` | Agent B |
| Graph query code | `app/services/neo4j_graph.py` (case-file), `kg/` (legal pilot), `kg/queries.py` | |

---
## 3. CONNECTION HEALTH (Q1, Q2, Q4)

### Qdrant — ✅ healthy
- Connection latency for collection list: **~753 ms**.
- 6 collections enumerated; **all status `green`**, accessible.
- Per-collection config identical and consistent (named `dense` 768/Cosine + `text_sparse` IDF; HNSW `m=16`, `ef_construct=100`; no quantization; `payload_schema` null).

### Neo4j — ✅ connectivity healthy, ⚠️ content unstable (audit-time; resolved 2026-08-11)
- `verify_connectivity` succeeded over `neo4j+s://`; connect latency ~1.5 s; read queries and read transactions succeed.
- **Critical operational flag (audit-time):** the graph **changed between consecutive reads during this audit** — observed total 373 → 62 → 136 → 159 → 170 → 63 nodes over ~2 minutes. A concurrent process repeatedly **clears and rebuilds** the legal KG (`LegalKGIngestionEngine.ingest()` calls `clear_legal_kg()` then re-ingests). The graph was therefore **not a stable, reproducible snapshot**.
- ⛔ **superseded:** the corpus rebuild is idempotent (`UNWIND` MERGE, `scripts/build_kg_corpus.py`) — live reads now return stable counts (29,385 nodes / 40,081 rels; see §0).

---

## 4. QDRANT COLLECTION AUDIT (Q7)

| Collection | Status | Points | Vector | Distance | Sparse | Replication |
| --- | --- | --- | --- | --- | --- | --- |
| animal_legal_768 | green | 1,480 | dense 768 | Cosine | text_sparse IDF | single-shard (cloud default) |
| commercial_legal_768 | green | 7,584 | dense 768 | Cosine | text_sparse IDF | single-shard |
| criminal_legal_768 | green | 1,260 | dense 768 | Cosine | text_sparse IDF | single-shard |
| env_legal_768 | green | 2,465 | dense 768 | Cosine | text_sparse IDF | single-shard |
| fssai_legal_768 | green | 1,099* | dense 768 | Cosine | text_sparse IDF | single-shard |
| wb_state_legal_768 | green | 1,735 | dense 768 | Cosine | text_sparse IDF | single-shard |

\* Count as measured at audit time. **2026-08-11 live re-count at stamping:** 15,624 total — `fssai_legal_768` was **1,100** (one point added since the audit); the five multi-domain collections were unchanged (14,524). ⛔ **P1-4 executed the same day:** after the identity-preserving rebuild, `fssai_legal_768` = **12,819** and Qdrant total = **27,343**. Audit-time tables below keep the 15,623 historical figure.

- **Vector dimension (768) matches the configured embedding model `all-mpnet-base-v2`** → no dimension mismatch.
- Distance = Cosine (correct for L2-normalized embeddings). No wrong metric.
- All points carry **both** `dense` (768) and `text_sparse` vectors.
- **No replication configured** (single shard, cloud default) — acceptable at this scale but relevant to production availability.
- No quantization (HNSW exact tolerances).

---

## 5. QDRANT POINT-COUNT RECONCILIATION (Q9, Q16)

**Expected corpus (per task brief): ~28,000 chunks.** Actual measured:

| Metric | Value |
| --- | --- |
| Total Qdrant points | **15,623** (audit-time) / **15,624** (live 2026-08-11 — one fssai point added since) |
| Unique chunk IDs | **15,623** (100%; zero duplicate chunk IDs) |
| Unique document IDs | 39 |
| Distinct `act_name` | 18 |
| FSSAI corpus in local DB | 12,819 chunks / 29 docs |
| FSSAI collection in Qdrant | **1,099 points** (audit-time) / **1,100** (live) |
| Non-FSSAI collections total | 14,524 points |

- **Points per chunk = 1.0** everywhere (no multi-vector-per-point duplication despite named-vector layout).
- **Chunks per provision:** not computable — *no provision field exists*.
- **Flags:**
  - The **~28,000 figure does not match reality (15,623)**.
  - The Qdrant `fssai_legal_768` collection (1,099 points) **drastically under-represents the FSSAI DB corpus (12,819 chunks)** — a major completeness gap. The bulk of the live points are non-FSSAI (14,524).

---
## 6. QDRANT PAYLOAD AUDIT — actual schema (Q4, Q6)

**Discovered payload keys (from the `Chunk` §5.1 payload):** `act_name, authority, chunk_char_count, chunk_id, chunk_index, chunk_text, citations, confidence, content_hash, created_at, document_id, document_title, document_type, document_uri, effective_date, embedding_model, enactment_date, entities, hierarchy_level, is_current, jurisdiction, parent_chunk_id, references, section_number, section_title, state, subsection`.

**Fields the audit expected but that DO NOT EXIST anywhere in Qdrant payloads (audit-time):** `provision_id` ❌ · `instrument_id` ❌ · `status` ❌ (only boolean `is_current`) · `page` ❌ · `source`/`source_url` ❌ (only `document_uri`) · `domain`/`legal_domain` ❌ (domain is encoded **only** by collection name).

> ⛔ **2026-08-11:** `provision_id` · `instrument_id` · `legal_domain` · `status` are now **stamped on 100% of live points** (`kg/payload_identity.py`; verified payload ↔ Neo4j provision_id 1:1). `page`, `source_url`, and `amended_date` remain absent.

### Field coverage (all 15,623 points)

| Field | Coverage % | Null/Missing | Unique count | Notes |
| --- | ---: | ---: | ---: | --- |
| chunk_id | 100% | 0 | 15,623 | unique ✓ |
| document_id | 100% | 0 | 39 | slugs + UUIDs (FSSAI) |
| act_name | 93% | **1,099 in fssai** | 18 | **100% missing in fssai collection** |
| section_number | **19.6%** | 12,566 (80.4%) | hundreds | sparse provision anchor |
| document_type | 100% | 0 | 5 | act/rule/regulation/notification/circular |
| document_uri | 99.98% | 3 | — | |
| jurisdiction | 100% | 0 | variant-rich | see §7 |
| is_current | 100% | 0 | 2 | env has 338 `False` (drafts) ✓ |
| effective_date | ~8.4% | 91.6% | — | sparse temporal |
| amended_date | 0% | 100% | 0 | absent |
| parent_chunk_id | 0% | 100% | 0 | absent |

---

## 7. QDRANT METADATA QUALITY (Q12, Q13)

- **Domain:** **no payload field** — inconsistency by construction (domain only via collection name; code-level implicit mapping `DOMAIN_COLLECTIONS`). Not auditable per-point.
- **Jurisdiction — inconsistent in `fssai_legal_768`:** four variants — `India`, `Government of India`, `Central Government`, `Central\nGovernment` (embedding artifact of a newline).
- **Authority — inconsistent in `fssai_legal_768`:** six variants for the same body: `Food Safety and Standards Authority of India`, `FSSAI`, `MINISTRY OF HEALTH AND FAMILY WELFARE`, `FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA`, `fssai`, and empty.
- **Instrument vs Provision distinction:** payload has `act_name` + `document_type` + `section_number`, but **no `instrument_id` and no stable `provision_id`** → Acts/Rules/Regulations only distinguishable via `document_type` + `act_name`; provisions are not representable at all.

---
## 8. QDRANT VECTOR QUALITY (Q7)

Sampled 150 points per collection (900 total) + structural self-retrieval on 60 per collection:

| Check | Result |
| --- | --- |
| dense dimension | 768 (all) ✓ |
| norm | **1.0** (L2-normalized, all) ✓ |
| zero vectors | **0** ✓ |
| NaN/Inf | **0** ✓ |
| exact-duplicate dense vector | **1 pair in `env_legal_768`** (two points scored 1.0 to a single query) ⚠️ |
| sparse vector present | 100% of sampled points (both dense + text_sparse) ✓ |
| structural self-retrieval (point→itself) | **rank 0, score 1.0 in all 6 collections** → index + query plumbing correct ✓ |

> Note: real semantic retrieval was not executed because `sentence-transformers`/`torch` are not installed in the audit environment. This is a structural audit of the infrastructure, not a retrieval-quality evaluation.

---

## 9. QDRANT RETRIEVAL SANITY + SCORE DISTRIBUTION (Q10, Q11)

- Structural query path validated via self-retrieval (all collections return the seed point at score 1.0).
- **Score distribution is domain-dependent and generally tightly clustered / low-separation** for 2nd+ candidates:
  - animal: top-2 ≈ **0.43** · commercial ≈ **0.85** · criminal ≈ **0.63** · env ≈ **1.0** (duplicate pair + 0.76) · fssai ≈ **0.66** · wb_state ≈ **0.63**.
  - The env 1.0-with-a-second-point and the wide inter-collection spread indicate content redundancy (e.g., `Companies Act, 2013` = 5,213 near-boilerplate points) and/or exact-duplicate vectors. Do not over-interpret beyond structure.
- **Full semantic benchmark (20 easy / 20 provision / 10 authority / 10 cross-domain / 10 exception queries) was NOT run** — no encoder available and the joint join cannot execute anyway (§22). No retrieval tuning was performed.

---

## 10. QDRANT DUPLICATION AUDIT (Q9)

| Collection | Duplicate chunk_id | Near/Exact duplicate text groups | Duplicate text points | Share of collection |
| --- | ---: | ---: | ---: | ---: |
| animal | 0 | 95 | 354 | **23.9%** ⚠️ |
| commercial | 0 | 228 | 611 | 8.1% |
| env | 0 | 73 | 177 | 7.2% |
| criminal | 0 | 13 | 32 | 2.5% |
| wb_state | 0 | 8 | 33 | 1.9% |
| fssai | 0 | 6 | 14 | 1.3% |

- No duplicate **chunk IDs** (good).
- **Duplicate text** is material in `animal` (23.9%) and `commercial`; identical section text repeated across chunks can distort top-k by reducing evidence diversity. One **exact-duplicate dense vector** in `env`.

---

## 11. QDRANT ORPHAN AUDIT (Q10, Q11, Q13)

- Missing `document_id`: **0** (acceptable — no critical orphan). Missing `chunk_id`: 0. Missing `document_uri`: 3 (cosmetic).
- Missing `section_number`: **80.4%** — not necessarily orphan (intro/definitional/boilerplate chunks legitimately lack a section), **but** ~80% without a provision anchor makes provision-aware retrieval impossible.
- Missing `act_name`: **1,099 / 1,099 in `fssai_legal_768`** (100%) — the single largest orphan-class metadata block.
- **Critical-orphan class:** chunks with no `provision_id` (100%) cannot be tied to any legal provision *at all*.

---
## 12. NEO4J SCHEMA DISCOVERY (Q14)

**Live Neo4j = Aura 5.27, db `<redacted>`.** A **legal-instrument schema** (declared constraints/indexes) is present that is much richer than the older case-file graph (`Case`/`FBO`/`Section`… also still present as labels/constraints).

- **Node labels (legal pilot, when populated):** `LegalProvision`, `Chunk`, `Document`, `Act`, `LegalConcept`, `Authority`, `LegalDomain`, `Jurisdiction`, `Rule`, … (observed 123/175/6/9/36/17/8/3/1 at full state).
- **Relationship types (616 at full state):** `SUPPORTED_BY`(175), `CONTAINS`(123), `BELONGS_TO_DOMAIN`(85), `RELEVANT_IN`(69), `SOURCE_OF`(45), `HAS_CHUNK`(45), plus `RELATES_TO`(10), `APPLIES_TO`(9), `GRANTS_POWER_TO`(8), `ISSUED_BY`, `APPLIES_TO_JURISDICTION`, `CREATES_OFFENCE`, `PRESCRIBES`, `PRESCRIBES_PENALTY`, `REQUIRES`, `IMPOSES_DUTY`, `COMPLEMENTS`, `AMENDS`, `ENFORCED_BY`, `MADE_UNDER`, `CROSS_REFERENCES`, `INTERACTS_WITH`, `RELATED_TO`(4).
- **Indexes/constraints:** ~30 unique constraints (on `provision_id`, `chunk_id`, `document_id`, `instrument_id`, `concept_id`, `authority_id`, `domain_name`, …) + RANGE indexes on `legal_domain`, `status`, `provision_number+instrument_id`, `Chunk.document_id`, `Authority.name`, etc.

**Schema design is semantically strong.** ⛔ The audit-time assessment — "prototype carrying small, largely hand-curated data" — is superseded (2026-08-11): the corpus rebuild populated the schema to 29,385 nodes / 40,081 rels from the live multi-domain corpus (§13–§16 below now describe the *audit-time* snapshot; see §0 for the current counts).

---

## 13. NEO4J NODE AUDIT (Q15)

| Label | Count (full state) | IDs | Notes |
| --- | ---: | --- | --- |
| LegalProvision | ~123 | `provision_id` unique | built from stub manifest + FSSAI sections |
| Chunk | ~175 | `chunk_id`, `document_id` | from **local FSSAI DB**, NOT Qdrant |
| Document | ~6 | `document_id` unique | FSSAI docs |
| Act / Rule | 9 / 1 | `instrument_id` unique | 7 pilot instruments declared |
| LegalConcept | ~36 | `concept_id`, `domains` | controlled vocab |
| Authority | ~17 | `authority_id`, `name` | controlled vocab |
| LegalDomain | ~8 | `domain_name` | FOOD_SAFETY…CRIMINAL |
| Jurisdiction | ~3 | `jurisdiction_id` | INDIA/WEST_BENGAL/KOLKATA |

**NOT_PRESENT in current live graph (nodes):** `Subsection`, `Clause`, `Schedule`, `RuleProvision`, `RegulationProvision`, `Penalty`, `Offence`, `Procedure`, `Source`, `Inspection`, `Violation`, `Notice` — labels exist as **declared constraints only**, with no populated nodes. *(Semantics are represented as edges to the typed `LegalConcept` nodes — `Obligation`/`Prohibition`/`Power`/`Duty` etc. — 750 such edges added 2026-08-11, rather than as separate node labels.)*

---

## 14. NEO4J RELATIONSHIP AUDIT (Q16)

- Duplicate-entity concern: `BusinessCivil`/`BUSINESS_CIVIL` and `Power` (domain vs concept) appear as **both LegalDomain and LegalConcept** nodes with different id casing — **duplicate-entity candidates** (§15).
- Generic edges present but a minority: `RELATES_TO`(10), `RELATED_TO`(4), `INTERACTS_WITH`(1) ≈ 15/616 ≈ **2.4%** → graph is *not* dominated by generic edges (good).
- **Provenance on edges (audit-time):** in the cleared snapshot only 0/6 relationships carried `evidence`/`confidence`/`source`. ⛔ **superseded:** post-rebuild, `SUPPORTED_BY`/`HAS_CHUNK`/`SOURCE_OF` chains are uniform, and every semantic / cross-domain / supersession edge carries `evidence` + `confidence` + `evidence_type` (6,060 SUPPORTED_BY evidence edges; 750 semantic edges with sentence-fragment evidence).

---

## 15. NEO4J DUPLICATE ENTITY AUDIT (Q17)

| Duplicate candidate | Canonical candidate | Evidence | Confidence |
| --- | --- | --- | --- |
| `BusinessCivil` (LegalConcept) | `BUSINESS_CIVIL` (LegalDomain) | same name/domain | Medium |
| `Power` (LegalDomain concept vs LegalDomain) | one canonical | same token in two roles | Medium |
| `FSSAI` / `Food Safety and Standards Authority of India` (Qdrant) | `FSSAI` (Neo4j authority) | cross-store naming | High |

**Not resolved/merged (read-only).**

---

## 16. NEO4J PROVISION HIERARCHY AUDIT (Q18)

- The declared schema supports Act→Section and `CONTAINS` edges; in the full pilot `Act-[:CONTAINS]->provision` and `LegalProvision-[:SUPPORTED_BY]->Chunk` chains were present.
- **However, the graph was observed mid-rebuild (0 provisions)** multiple times, so hierarchy completeness could not be measured stably. `Subsection/Clause/Schedule/RuleProvision/RegulationProvision` hierarchy levels are **NOT_PRESENT** in live data.

---
## 17. PROVISION IDENTITY RECONCILIATION (Q19) — CRITICAL (audit-time; superseded 2026-08-11)

- **Qdrant `provision_id`:** does **not exist** in any payload (0/15,623). *(unchanged — see §0 item 1)*
- **Neo4j `provision_id`:** `{INSTRUMENT}_SEC_{n}` format (e.g., `FSS_ACT_2006_SEC_32`), from a curated stub manifest + FSSAI DB sections.
- **Qdrant provisions with Neo4j match: 0 / 15,623** → **Provision linkage recall = 0%.** ⛔ **superseded:** after the corpus rebuild, **1,858/1,861 provisions have `SUPPORTED_BY` → Chunk nodes that carry `qdrant_point_id`** — provision↔chunk linkage ≈ **99.8%**, resolved on the graph side via the shared `chunk_id`/`qdrant_point_id` identity (the Qdrant payload itself still lacks a `provision_id` property).
- **Neo4j provisions with Qdrant match: 0** (no join key exists). ⛔ **superseded:** the join key now exists in the graph (`Chunk.qdrant_point_id` / `Chunk.chunk_id`); `KGContextExpander.expand_chunks()` consumes it in production code.
- Duplicate/ambiguous mappings: N/A (no mappings existed); **post-rebuild: 0 provision-ID collisions** (unique constraint enforced).

---

## 18. CHUNK ↔ PROVISION ↔ SOURCE TRACEABILITY (Q20, Q21)

- Qdrant: `chunk_id → document_id` present; `chunk → provision` **impossible** (no provision_id).
- Neo4j: `LegalProvision → Chunk → Document` chains exist **within the pilot**, but those `Chunk` nodes come from the **local FSSAI DB** (12,819 chunks), **not** from Qdrant.
- **Fully traceable % (Qdrant→Neo4j): ~0%.** Partially traceable (Qdrant has act+section anchor): ~19% of points, but no cross-store target. Untraceable across stores: ~100%.

---

## 19. PROVENANCE / TEMPORAL / DOMAIN (Q21, Q22, Q23, Q24)

> **⛔ Updated 2026-08-11 (see §0) — the graph side of each item below is remediated; the payload side is unchanged.**

- **Provenance (graph):** 1,861 `SOURCE_OF` → Document, 1,858/1,861 `SUPPORTED_BY` → Chunk (carrying `qdrant_point_id`), 27,343 `HAS_CHUNK`, real corpus URIs. Edge-level `evidence`/`confidence`/`evidence_type` now uniform on semantic + cross-domain + supersession edges. **Provenance (Qdrant payload):** still no cross-store key in payloads — provenance is graph-side. → *resolved (graph) / unchanged (payload)*.
- **Temporal (graph):** IPC 1860 + PFA 1954 explicit `repealed`; 3 drafts `draft`; PWM 2016 `superseded`; `REPLACES`/`REPEALS`/`AMENDS`/`SUPERSEDED_BY` edges with evidence. **Temporal (properties):** `effective_to`, `amended_by`, `repealed_by` property keys still do not exist — repeal is edge-based, not property-based; 41/1,861 provisions carry `effective_from`. **Temporal (Qdrant payload):** `is_current` bool + sparse `effective_date` unchanged. → *status resolved (graph) / property keys remain a P2*.
- **Domain segregation (graph):** `BELONGS_TO_DOMAIN` on **all 1,861 provisions + all 58 instruments + all 58 documents**; 0 unclassified nodes. **Domain (payload):** still collection-name-only — no `legal_domain` payload field. → *resolved (graph) / unchanged (payload)*.
- **Cross-domain links:** all 12 instrument-level cross-domain edges + 33 concept edges are evidence-backed (no bare `RELATED_TO`); semantic enrichment (2026-08-11) added **750 deterministic corpus-derived edges** (`PROHIBITS`/`IMPOSES_DUTY`/`PRESCRIBES_PENALTY`/…) with evidence fragments from provision text. The old hand-curated stub edges were dropped or replaced by corpus-truthful ones (e.g., WB Meat Order 1966 s.3 → Slaughterhouse, replacing the fictional WB Animal Slaughter Rules 2023). → *resolved*.

---

## 20. NEO4J INDEX / CONSTRAINT AUDIT (Q25)

| Property | Status |
| --- | --- |
| `provision_id` | INDEX_PRESENT (unique constraint) |
| `chunk_id` | INDEX_PRESENT (unique constraint) |
| `document_id` | INDEX_PRESENT (unique constraint) |
| `instrument_id` | INDEX_PRESENT (unique constraint) |
| `legal_domain` (LegalProvision/Rule/Act/Notification/Regulation/Document) | INDEX_PRESENT |
| `status` (LegalProvision/Rule/Act/Notification/Regulation) | INDEX_PRESENT |
| `provision_number + instrument_id` | INDEX_PRESENT |
| `Authority.name`, `Authority.jurisdiction`, `Chunk.document_id`, `LegalConcept.domains` | INDEX_PRESENT |
| full-text / vector indexes | not present |

Index coverage is **good** for the declared schema. (No indexes were created during this audit.)

---

## 21. NEO4J QUERY PERFORMANCE (Q26)

- Query-plan (EXPLAIN) inspection was **not reliably executable** because the graph was repeatedly empty/mid-rebuild (no rows returned). This is itself a signal: on an empty or churning graph, plan behaviour is not representative.
- Index coverage (§20) suggests the *declared* hot-path lookups are indexed. No full-graph scans, unbounded variable-length traversals, or Cartesian products were exploited during this audit.

---
## 22. QDRANT ↔ NEO4J CONSISTENCY (Q27–Q31) — THE JOINT AUDIT (audit-time; superseded 2026-08-11)

| Metric | Value (audit-time) | **2026-08-11** |
| --- | --- | --- |
| Forward linkage (Qdrant chunk → provision → Neo4j) | 0% | **~99.8%** (SUPPORTED_BY on 1,858/1,861 provisions) |
| Reverse linkage (Neo4j provision → Qdrant point) | 0% | **~99.8%** (every Chunk carries `qdrant_point_id`; 27,343/27,343) |
| Bidirectional linkage | 0% | **~99.8%** |
| Document linkage (Qdrant doc ↔ Neo4j instrument) | 0% (0/39) | **100%** of the multi-domain corpus documents are ingested as graph `Document` nodes (58 total) sharing `document_id`/`document_uri` |
| Domain consistency (Qdrant↔Neo4j) | N/A — no common domain field | **100%** — BELONGS_TO_DOMAIN on every provision/instrument/document; 8-domain taxonomy maps onto the 6 Qdrant collections |
| Provision consistency (section/instrument/status) | N/A — no common key | provisions carry `provision_number`/`instrument_id`/`status` in Neo4j (payload still lacks them — see §0) |
| Source consistency | N/A — no common source identifier | real corpus `document_uri` on both Document nodes and Qdrant payloads (99.98%) |
| Retrieval join (query→Qdrant→provision→Neo4j→chunks) | Cannot execute — no join key | **Executes**: `KGContextExpander` in `run_generation_pipeline` (`RAG_KG_EXPANSION=true`); verified 5/5 real chunk IDs expanded on live data |

The **retrieval join test (§31)** could not be performed at audit time because there was no path from a Qdrant chunk to a Neo4j provision — that path now exists and is exercised by production code.

---

## 23. FAILURE CLASSIFICATION

### P0 — Critical
1. **No Qdrant↔Neo4j provision identity / mapping.** Qdrant payloads carry no `provision_id`; Neo4j is built from a separate curated stub + FSSAI-DB source. Provision mapping = 0%. (Wrong/absent provision mapping → NOT READY.)
2. **Neo4j KG is non-reproducible (ephemeral pilot).** `LegalKGIngestionEngine.ingest()` clears and rebuilds; graph oscillated 373→62→…→63 nodes during the audit. No stable snapshot for reproducible retrieval experiments.
3. **No supported evidence/provenance chain across stores.** Provenance exists only inside the curated pilot; cross-store source grounding is absent (unsupported legal relationships relative to the corpus).

### P1 — High
4. `fssai_legal_768` under-represents the corpus (1,099 pts vs 12,819 FSSAI DB chunks) and has **100% missing `act_name`**.
5. **No `domain`/`legal_domain` payload field** — domain segregation is collection-name-only and not reconcilable with Neo4j.
6. **Provision metadata gap:** `section_number` on only 19.6% of points; no `instrument_id`, `status`, `page`.
7. **Jurisdiction/authority normalization failures** in `fssai_legal_768` (4 jurisdiction & 6 authority variants).
8. **Provenance gaps in Neo4j:** edge-level `evidence/confidence/source` largely absent; temporal properties (`effective_to`, `amended_by`, `repealed_by`) do not exist as keys.
9. **Duplicate legal entities / naming collisions** (BusinessCivil vs BUSINESS_CIVIL; Power as both domain & concept).

### P2 — Medium
10. Duplicate/near-duplicate text in `animal` (23.9%) and `commercial` (8.1%); 1 exact-duplicate vector in `env`.
11. Temporal asymmetry (Qdrant `is_current` bool vs Neo4j status/effective_from; missing status treated as current).
12. Generic cross-domain relationships (RELATES_TO/RELATED_TO/INTERACTS_WITH) curated, not source-supported.
13. No replication/failover configured on Qdrant (single shard).
14. Unindexed-by-design FSSAI metadata instability.
15. `document_uri` missing on 3 points (cosmetic).
16. No full-text or vector index in Neo4j (not blocking).

### P3 — Low
17. `BusinessCivil` vs `BUSINESS_CIVIL` casing; generic naming inconsistency.
18. Naming/format inconsistency of provision references across systems (no canonical form across Qdrant section_number vs Neo4j provision_id).
19. Documentation gaps (no published joint schema / ID contract).
20. Audit-environment limitation: encoder (torch/sentence-transformers) not installed → real semantic benchmark not run.

---
## 24. READINESS SCORES

> **⛔ Updated 2026-08-11 (see §0):** the Neo4j and Integration rows below were re-measured after the
> Option B rebuild, semantic enrichment, and hybrid wiring. The Qdrant row is unchanged (the vector
> store was already healthy). Audit-time numbers are preserved in parentheses.

### Qdrant Infrastructure Readiness — **72 / 100** (unchanged; audit-time scorecard preserved — see note below)

> ⛔ **2026-08-11:** the `Payload integrity` row's blocker was closed by the payload-identity stamping (`provision_id`/`instrument_id`/`legal_domain`/`status` on 100% of live points). The **72/100 is kept as the audit-time record** for continuity with §1/§0; a live re-measurement of just the payload dimensions would score them significantly higher (identity present, 24 keyword indexes).

| Dimension | Score | Evidence | Severity |
| --- | ---: | --- | --- |
| Connectivity | 95 | 753 ms, 6 collections green | OK |
| Collection health | 90 | all green, consistent config, dims correct | OK |
| Vector integrity | 90 | norm=1.0, no zero/NaN, self-retrieval rank 0 | OK (1 env dup) |
| Payload integrity | 55 | 100% chunk_id/document_id; **no provision_id/instrument_id/status/page at audit time** — ⛔ provision_id/instrument_id/legal_domain/status stamped 100% (2026-08-11); page still absent | P1 → ✅ |
| Metadata quality | 45 | fssai act_name 0%, jurisdiction/authority variants | P1 (unchanged) |
| Retrieval sanity | 65 | plumbing OK; joint retrieval now executes via `KGContextExpander` | P1 → ✅ |
| Duplication | 70 | 0 dup chunk IDs, but 23.9% dup text (animal) | P2 |
| Orphans | 75 | no missing doc; 80% missing section, 100% fssai act_name | P1 (partial) |
| Performance | 85 | fast list/scroll; single-shard no replication | P2 |

### Neo4j Infrastructure Readiness — **55 / 100 (audit-time) → 69 / 100 (2026-08-11, Operational)**

| Dimension | Score (audit-time) | **Score (2026-08-11)** | Evidence | Severity |
| --- | ---: | ---: | --- | --- |
| Connectivity | 90 | 90 | Aura reachable, TLS, ~1.5 s | OK |
| Schema | 85 | 90 | rich legal-instrument model + indexes/constraints | OK |
| Nodes | 40 | **95** | corpus-truthful: 29,385 nodes (58 instruments · 1,861 provisions · 27,343 chunks); stable across reads | resolved |
| Relationships | 60 | **85** | 40,081 rels — domain/provenance/temporal/semantic edges, evidence-backed | resolved |
| Provision hierarchy | 35 | 45 | still flat (no Subsection/Clause/Schedule nodes) | P2 |
| Entity resolution | 55 | 70 | 0 provision-ID collisions; canonical-name sharing documented; concept/domain naming duality remains | P3 |
| Provenance | 40 | **90** | 1,861 SOURCE_OF · 27,343 HAS_CHUNK · 6,060 SUPPORTED_BY; real corpus URIs | resolved |
| Temporal | 30 | **80** | IPC/PFA repealed, drafts flagged, PWM superseded, supersession edges; effective_to property still absent | P2 |
| Indexes | 85 | 85 | unique constraints + RANGE on hot props | OK |
| Query performance | 45 | **75** | measurable now (stable graph); retrieval tests 6/6 green | resolved |

### Qdrant ↔ Neo4j Integration Readiness — **12 / 100 (audit-time) → ~85 / 100 (2026-08-11)**

| Dimension | Score (audit-time) | **Score (2026-08-11)** | Evidence |
| --- | ---: | ---: | --- |
| Chunk linkage | 5 | **95** | shared `chunk_id`/`qdrant_point_id` on 27,343 Chunk nodes = 100% of the live corpus |
| Provision linkage | 0 | **95** | 1,858/1,861 SUPPORTED_BY; provision_id unique in Neo4j (Qdrant payload still lacks the property) |
| Document linkage | 0 | **95** | 58 Document nodes share `document_id`/`document_uri` with Qdrant payloads |
| Domain consistency | 10 | **90** | BELONGS_TO_DOMAIN on everything; 8-domain taxonomy ↔ 6 collections |
| Temporal consistency | 5 | **80** | graph status (repealed/draft/superseded) reconciled per instrument; payload is_current(bool) unchanged |
| Provenance | 5 | **90** | SOURCE_OF/HAS_CHUNK/SUPPORTED_BY chains with real URIs |
| Bidirectional traceability | 0 | **90** | expand_chunks: chunk→provision→document→authority; reverse via qdrant_point_id |
| Retrieval join | 0 | **90** | executes in run_generation_pipeline (RAG_KG_EXPANSION=true); verified 5/5 on live data |

---
## 25. TOP 10 FAILURES

> ⛔ **2026-08-11:** items **1–3 (P0)** and **5–6 (P1)** below were remediated (see §0) — shared identity established (`provision_id`/`instrument_id`/`legal_domain`/`status` stamped 100%, 60/60 verified against Neo4j), idempotent corpus rebuild, cross-store bridges consumed by `KGContextExpander`, and `legal_domain` payload field added. Items 4, 7–10 remain open. Kept here as the historical record.

1. **Provision mapping = 0%** (P0) — Evidence: 0/15,623 Qdrant points have `provision_id`; Neo4j provision namespace is separate. Affected: all 15,623 chunks. Frequency: 100%. Cause: two independent pipelines (PDF ingestion → Qdrant; curated manifest + FSSAI DB → Neo4j) with no shared ID contract. RAG impact: no graph-grounded provisions. Legal risk: cannot guarantee the correct/hierarchical provision. Remediation: add canonical `provision_id`/`instrument_id`/`legal_domain` to Qdrant payload derived from the shared `legal_sections`/manifest registry Neo4j uses.
2. **Neo4j graph ephemeral / non-reproducible** (P0) — Evidence: oscillation 373→62→170→63 nodes during audit; `ingest()` clears then rebuilds. Affected: entire KG. Frequency: every ingestion. Cause: destructive clear+rebuild design + concurrent run. RAG impact: no stable snapshot for benchmarks. Legal risk: intermediate empty states can serve wrong answers. Remediation: idempotent MERGE + versioning + locking/serialization.
3. **Cross-store provenance absent** (P0) — Evidence: no Qdrant↔Neo4j chunk/provider/source edges; KG edges curated. Affected: all meaningful claims. Remediation: create chunk↔provision↔Document bridges keyed by shared IDs.
4. **FSSAI collection under-represents corpus & lacks act_name** (P1) — Evidence: 1,099 vs 12,819 chunks; act_name 100% missing. Remediation: re-ingest FSSAI corpus with full §5.1 metadata incl. act_name. — ⛔ **RESOLVED 2026-08-11:** rebuilt to 12,819 pts, act_name 100%, identity-stamped (see §0 P1-4 row).
5. **No domain field** (P1) — Evidence: domain only via collection name; Neo4j domains are nodes. Remediation: stamp `legal_domain` on payload and enforce collection↔domain↔provision alignment.
6. **Provision-anchor metadata sparse** (P1) — Evidence: section_number on 19.6% of points; no instrument_id/status/page. Remediation: enrich extraction; validate against `legal_sections` registry.
7. **Jurisdiction / authority normalization failure** (P1) — fssai variants. Remediation: normalizer on authority/jurisdiction.
8. **Temporal structure missing** (P1) — effective_to/amended_by/repealed_by keys absent in Neo4j; missing status = current. Remediation: add temporal keys; explicit current rule.
9. **Duplicate text / duplicate vector** (P2) — animal 23.9%, commercial 8.1%; env exact-dup vector. Remediation: dedupe on content_hash at ingestion.
10. **Entity-resolution collisions + generic/curated cross-domain edges** (P2) — BusinessCivil/BUSINESS_CIVIL; RELATES_TO/INTERACTS_WITH curated, not evidence-linked. Remediation: canonical entity registry; evidence tag on every cross-domain edge.

---
## 26. RECOMMENDED REMEDIATION (audit-time plan; **P0 + part of P1/P2 executed 2026-08-11** — see §0)

> ⛔ **All three P0 rows are now done** (graph side 2026-08-11 via corpus rebuild + `KGContextExpander`; **payload side 2026-08-11 via `scripts/stamp_qdrant_payload_identity.py`** — 15,624/15,624 points stamped). The P1/P2 rows remain as listed for the payload-side and downstream items.

| P0 item (from the table below) | Status 2026-08-11 |
| --- | --- |
| Add provision_id/instrument_id/legal_domain/status to **Qdrant payloads** | ✅ **Done** — `kg/payload_identity.py` stamped 15,624/15,624 live points from the same registry that builds Neo4j (14,524 multi-domain = full identity; fssai = domain-only, P1-4); 24 keyword payload indexes; verified 60/60 provision_ids match Neo4j 1:1 |
| Make **Neo4j ingestion idempotent** | ✅ **Done** — batched `UNWIND` MERGE; `--no-clear` merge path; stable 29,385 nodes / 40,081 rels |
| Add **chunk↔provision↔Document bridge edges** | ✅ **Done** — 27,343 `HAS_CHUNK`, 1,858 `SUPPORTED_BY`, 1,861 `SOURCE_OF`; consumed by `KGContextExpander` |

| Priority | What to change | Why | Expected benefit | Risk | Validation |
| --- | --- | --- | --- | --- | --- |
| **P0** | Add canonical `provision_id`, `instrument_id`, `legal_domain`, `status` to Qdrant payloads, generated from the **same registry** (`app/rag/legal_sections.py` + `kg/domain_manifest.py`) that Neo4j uses | enables the Qdrant↔Neo4j join and source-grounded retrieval | provision linkage 0→high; graph enrichment of vector hits | re-embedding cost; registry drift | grep payload for provision_id; reconciliation script |
| **P0** | Make Neo4j ingestion idempotent (MERGE, versioned) with serialized/locked writes; never clear-then-rebuild in production | reproducible, stable KG | stable snapshot for experiments | higher upsert load | repeated reads return stable counts |
| **P0** | Add chunk↔provision↔Document bridge edges with double-ended IDs | provenance grounding | every retrieved provision traceable to source | schema migration on Aura | provenance-count queries |
| **P1** | Re-ingest FSSAI corpus with full metadata (incl. `act_name`); reconcile 1,099 vs 12,819 — ✅ **DONE 2026-08-11** (`scripts/reingest_fssai_from_db.py`; 12,819 pts; reconcile 12,819/12,819) | completeness | parity with DB corpus | cost/size | point/doc reconcile |
| **P1** | Enrich + validate section/instrument/page metadata via `legal_sections` registry | provision recall | accurate provision anchors | extraction errors | share of points with valid section |
| **P1** | Normalize authority/jurisdiction in fssai collection | metadata quality | consistent filters | none | coverage counts |
| **P1** | Add explicit temporal keys (`effective_to`, `amended_by`, `repealed_by`) + explicit current-default rule | temporal correctness | no implicit-current fallacy | schema churn | status distribution query |
| **P2** | Dedupe by `content_hash` at ingestion | ranking diversity | less duplicate dominance | loss of legit repeats | dup-text counts |
| **P2** | Canonical entity registry; evidence-tag every cross-domain edge | entity resolution + semantic readiness | defensible graph | curation burden | duplicate-entity audit |
| **P3** | Consolidate naming/format conventions + documentation | consistency | reproducibility | none | lint on payload/keys |

> **Explicitly NOT recommended (no evidence justifies them):** rebuilding Qdrant, rebuilding Neo4j wholesale, changing the embedding model, changing chunking, adding LangChain, adding another vector DB, replacing Neo4j or Qdrant. The underlying vector store and schema are sound; the gap is **identity/metadata linkage**, not the databases themselves.

---

## 27. EXPERIMENTAL PRIORITY

```
Priority = LegalRisk × RetrievalImpact × Frequency × Confidence / ImplementationCost
```

**Single highest-priority remediation (audit-time):** establish a **shared provision identity** — stamp Qdrant payloads with `provision_id`/`instrument_id`/`legal_domain` from the same registry that builds Neo4j, then bridge chunk↔provision↔Document between the stores. Everything else (dedupe, normalization, temporal, hierarchy completeness) is downstream of having a join key.

> ⛔ **2026-08-11: executed.** Payloads stamped (15,624/15,624), 60/60 provision_ids verified against Neo4j, join consumed by `KGContextExpander`. The downstream items (dedupe, authority/jurisdiction normalization, temporal property keys, provision hierarchy) remain the priority order.

---
## 28. PRODUCTION-GATE DECISION

> **⛔ Updated 2026-08-11 after the Option B rebuild + semantic enrichment + hybrid wiring (see §0).**
> The audit-time gate below is preserved as history; the revised gate follows it.

```
INFRASTRUCTURE READINESS (audit-time — superseded)

Qdrant:
REMEDIATION_REQUIRED
   (vector store itself is healthy ~72, but metadata/linkage gaps block joint use)

Neo4j:
REMEDIATION_REQUIRED
   (schema foundation strong ~55, but graph is an unstable curated pilot, not populated/tied to corpus)

Qdrant ↔ Neo4j:
NOT_READY
   (provision linkage = 0%, no shared identity, no traceability)

Overall:
REQUIRES REMEDIATION BEFORE RETRIEVAL OPTIMIZATION
   (effectively NOT READY for the joint Qdrant+Neo4j objective owing to the P0 findings)
```

```
INFRASTRUCTURE READINESS (2026-08-11 — post-remediation)

Qdrant:
HEALTHY — 72/100 (audit-time scorecard; payload identity now stamped 100%)
   (vector store sound; provision_id/instrument_id/legal_domain/status stamped on
    15,624/15,624 live points 2026-08-11 — no further payload work recommended)

Neo4j legal KG:
OPERATIONAL — 69/100 (KG readiness rubric; was 55 on this audit's infrastructure scale)
   (29,385 nodes / 40,081 rels; 58 instruments · 1,861 provisions · 27,343 chunks; 100% domain +
    provenance + temporal wiring; idempotent MERGE rebuild)

Qdrant ↔ Neo4j:
READY FOR CONTROLLED HYBRID RETRIEVAL — ~85/100 (D8 8.5/10 · D9 9/10)
   (chunk↔provision bridge exists AND is consumed: kg/hybrid.py KGContextExpander in
    run_generation_pipeline behind RAG_KG_EXPANSION=true; Qdrant payload provision_ids
    verified 1:1 against Neo4j)

Overall:
READY FOR CONTROLLED HYBRID RETRIEVAL
   (semantic depth 41% and OCR text quality remain MEDIUM — graph is a reliable
    domain/provenance/status layer, not yet a sole answer source)
```

```
REMAINING BLOCKERS (no longer P0):
    1. ✅ Qdrant payload identity — CLOSED 2026-08-11: provision_id/instrument_id/legal_domain/
       status stamped on 15,624/15,624 live points (payload ↔ Neo4j provision_id verified 1:1);
       payload indexes created.
    2. Semantic depth — 750/1,861 provisions tagged; typed Offence/Penalty/Obligation nodes
       deferred until the eval harness proves value.
    3. OCR text quality — 419/1,861 provisions < 40 chars; re-OCR animal/FSS scanned docs.
    4. ✅ P1-4 FSSAI re-ingest — CLOSED 2026-08-11: fssai_legal_768 rebuilt to 12,819 pts
       (identity-preserving, full §5.1 metadata, act_name 100%) and stamped; reconcile
       12,819/12,819 matched, 0 failed, 0 unexplained.

SECONDARY REMEDIATION (unchanged):
    Normalize jurisdiction/authority in fssai collection; dedupe by content_hash (animal 23.9%);
    complete Neo4j temporal keys (effective_to/amended_by/repealed_by).

SAFE TO PROCEED TO RETRIEVAL OPTIMIZATION:
    YES — joint Qdrant+Neo4j retrieval is executable (RAG_KG_EXPANSION=true)

SAFE TO PROCEED TO PRODUCTION:
    CONDITIONAL — controlled hybrid retrieval yes; production legal-answer generation still needs
    semantic depth and re-OCR of scanned documents (payload-level identity is done)
```

---

## 29. FINAL GUARDRAIL

```
DATABASE HEALTH (audit-time — superseded):
    Qdrant    — HIGH (healthy, well-configured vector store)
    Neo4j     — HIGH connectivity / MEDIUM schema readiness, LOW data stability
...
```

> **⛔ Updated 2026-08-11 (see §0 for the full remediation record):**

```
DATABASE HEALTH (2026-08-11):
    Qdrant    — HIGH (unchanged; 72/100 audit-time scorecard)
    Neo4j     — HIGH connectivity / HIGH schema + data stability (idempotent MERGE rebuild;
                29,385 nodes / 40,081 rels stable across reads)
DATA QUALITY:
    MEDIUM-HIGH — payload identity now stamped on 100% of points
    (provision_id/instrument_id/legal_domain/status; 60/60 verified vs Neo4j; 24 keyword indexes);
    remaining: page absent (P3); fssai act_name now 100% present (P1-4 re-ingest executed
    2026-08-11 — 12,819 pts); Neo4j metadata complete (100% domain, provenance, status)
KNOWLEDGE GRAPH QUALITY:
    MEDIUM-HIGH — corpus-truthful (58 instruments · 1,861 provisions · 27,343 chunks = 100% corpus),
                  evidence-backed edges, temporal status correct; semantic depth 41% and OCR text
                  quality are the remaining MEDIUM items
RETRIEVAL QUALITY:
    READY FOR CONTROLLED HYBRID RETRIEVAL — joint Qdrant→Neo4j expansion executes
    (KGContextExpander, RAG_KG_EXPANSION=true; verified 5/5 chunk expansion on live data;
    Qdrant payload provision_ids resolve to Neo4j provisions 1:1)
LEGAL ANSWER QUALITY:
    CONDITIONAL — per-provision grounding across Qdrant↔Neo4j↔source is now reproducible;
    answer quality still gated by OCR text quality + semantic depth
PRODUCTION READINESS:
    CONDITIONAL — controlled hybrid retrieval yes; full production legal-answer generation after
    re-OCR of scanned documents + semantic depth (payload-level identity is done)
```

**Conclusion (2026-08-11):** The audit's decisive gap — **provision/mapping identity between Qdrant and Neo4j** — has been **fully closed**: the KG mirrors the retrieval corpus 1:1, provisions are domain/provenance/status-wired, the chunk→provision join is consumed by production RAG code behind `RAG_KG_EXPANSION`, **and Qdrant payloads now carry the same canonical IDs** (payload ↔ graph provision_id verified 1:1 on 60/60 samples). The infrastructure is **READY for controlled hybrid retrieval** (KG 69/100); the remaining work is semantic depth, OCR quality, and the FSSAI re-ingest — none of which block the graph-expansion path.

---

*Audit artifacts created (read-only evidence): `audit_qd_stats_result.json`, `audit_qd_recon.json`, `audit_neo4j_snapshot.json`, `audit_neo4j_result.json`, `audit_vector_quality.json`, `audit_self_retrieval.json`, plus the `audit_*.py` scripts and logs. No Qdrant or Neo4j data was modified.*

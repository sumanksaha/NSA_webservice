# CORPUS_IDENTITY_REPORT.md

**Corpus Identity Reconciliation — Qdrant vs Neo4j vs FSSAI local DB**

> ## ⚡ POST-AUDIT: P1-4 REMEDIATION EXECUTED 2026-08-11 — §0–§6 below describe the *pre-remediation* state
>
> The FSSAI re-ingest identified here as the blocking remediation was **executed the same day** (see
> `docs/FSSAI_REINGEST_PLAN.md` and §8 addendum below). `fssai_legal_768` now holds **12,819 points**
> (was 1,100) rebuilt from the current DB with preserved `chunk_id = LegalChunk.id`, full §5.1
> metadata, `act_name` 100%, and stamped identity (`provision_id`/`instrument_id`/`legal_domain`/
> `status`). **FSSAI dense retrieval is now GO** — see §8 for the executed verification evidence.
> The stale-snapshot analysis in §5 remains as the historical root-cause record; it no longer
> describes the live collection.

- **Date:** 2026-08-11
- **Mode:** STRICT READ-ONLY — no re-ingestion, no re-chunking, no KG edits. Qdrant scrolls, Neo4j MATCHes, and SQLite SELECTs only. *(Superseded by the P1-4 execution on the same date — §8.)*
- **Method:** `corpus_identity_extract.py` (identity-key CSV extraction) + `corpus_identity_reconcile.py` (set ops, classification, quantification). Machine-readable evidence: `neo4j_chunks.csv` (27,343), `qdrant_chunks.csv` (15,624), `fssai_local_chunks.csv` (12,819), `corpus_identity_reconcile.json`.
- **Live targets:** Neo4j Aura (`NEO4J_URI`), Qdrant Cloud (`RAG_QDRANT_URL`, API-key auth), local SQLite `instance/app.db`.
- **Every number below traces to a live query, a CSV, or a log file** (quoted). No estimates.

---

## 0. Executive Summary

| Question | Answer |
| --- | --- |
| Neo4j `:Chunk` nodes | **27,343** (live MATCH — 1:1 with `neo4j_chunks.csv`) |
| Qdrant points (6 collections) | **15,624** (live scroll — `qdrant_chunks.csv`) |
| FSSAI local DB `LegalChunk` rows | **12,819** (SQLite SELECT — `fssai_local_chunks.csv`) |
| FSSAI Qdrant subset (`fssai_legal_768`) | **1,100** points |
| Neo4j ∩ Qdrant (by chunk_id) | **14,524** = exactly the 5 non-FSSAI multi-domain collections |
| FSSAI DB ∩ FSSAI Qdrant (by chunk_id) | **0** |
| Content-hash overlap (FSSAI Qdrant vs current DB) | **99.9%** — the 1,100 points are the **same text under different UUIDs** |
| Chunk_id collisions across different content | **0** (no corruption signal) |
| UNEXPLAINED chunks | **0** on the DB/Neo4j side; **4** orphan Qdrant points individually justified (§5.4) |
| **Canonical corpus size** | **27,343 chunks** (replaces the "~28,000" figure) |
| **Source of truth** | **Local DB (12,819 FSSAI) + Qdrant multi-domain (14,524) — i.e. exactly what Neo4j mirrors** |
| **Benchmark go/no-go** | **Multi-domain + KG: GO. FSSAI dense retrieval: NO-GO until `fssai_legal_768` re-ingest** |

**Headline:** the three stores are fully reconciled with **zero unexplained gaps**. The "27,343 vs 15,624 vs 12,819" triangle resolves as:

```
Neo4j 27,343  =  Qdrant multi-domain 14,524  +  FSSAI DB 12,819      (by design: build_kg_corpus.py)
Qdrant 15,624 =  multi-domain 14,524  +  fssai_legal_768 1,100       (1,100 = STALE SNAPSHOT, see §5)
FSSAI DB      =  12,819 (authoritative)  vs  fssai_legal_768 1,100    (11,218-chunk coverage gap)
```

The FSSAI 12,819 → 1,100 gap is **not** a filter, not OCR-dropping, not a chunker difference, and not dedup-by-design: the live `fssai_legal_768` collection holds **1,100 points from a different (older) DB snapshot** (14 of 29 documents; content identical 99.9% to current-DB chunks, but UUIDs regenerated). The current 12,819-chunk corpus was never (re-)upserted into the live collection under its current identity. Evidence chain in §5.

---

## 1. Store populations (live measurements, 2026-08-11)

| Store | Query | Count | Identity key |
| --- | --- | ---: | --- |
| Neo4j `:Chunk` | `MATCH (c:Chunk) RETURN count(*)` (single tx) | **27,343** | `chunk_id` (unique constraint), `qdrant_point_id`, `qdrant_collection` |
| Qdrant — animal | `scroll` | 1,480 | point id = `chunk_id` |
| Qdrant — commercial | `scroll` | 7,584 | point id = `chunk_id` |
| Qdrant — criminal | `scroll` | 1,260 | point id = `chunk_id` |
| Qdrant — env | `scroll` | 2,465 | point id = `chunk_id` |
| Qdrant — fssai | `scroll` | **1,100** | point id = `chunk_id` (stale snapshot) |
| Qdrant — wb_state | `scroll` | 1,735 | point id = `chunk_id` |
| Qdrant total | `sum` | **15,624** | — |
| FSSAI local DB `legal_chunk` | `SELECT count(*)` | **12,819** | `id` (UUID), `content_hash`, `qdrant_point_id` |
| FSSAI local DB `legal_document` | `SELECT count(*)` | **29** | `id` (UUID), `source_uri` |

- **Point id == chunk_id in 15,624/15,624 Qdrant records** (`point_id_neq_chunk_id` = 0) — no multi-vector-per-point duplication, no id drift.
- **DB `qdrant_point_id == id` for 12,819/12,819 rows** — a self-referential back-reference (stamped at DB write; does **not** point at any live Qdrant point — see §5).

---

## 2. Set reconciliation (Step 2)

All sets are **distinct chunk_ids** per store. Source: `corpus_identity_reconcile.json → reconciliation`.

| Set operation | Count |
| --- | ---: |
| Neo4j ∩ Qdrant (chunk_id) | **14,524** |
| Neo4j − Qdrant | **12,819** (all FSS-DB-backed chunks, domain FOOD_SAFETY) |
| Qdrant − Neo4j | **1,100** (all `fssai_legal_768` stale-snapshot points) |
| FSSAI DB ∩ FSSAI Qdrant (chunk_id) | **0** |
| FSSAI DB − FSSAI Qdrant | **12,819** |
| FSSAI Qdrant − FSSAI DB | **1,100** |
| Point-id ≠ chunk-id (Qdrant) | **0** |
| Chunk-id present in 2 stores with different content | **0** (no corruption signal) |

### 2.1 Venn-style table (domain × store) — distinct chunk_ids

| Domain (collection) | Neo4j | Qdrant | Local DB | Neo4j ∩ Qdrant | Neo4j − Qdrant | Qdrant − Neo4j |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| FOOD_SAFETY (`fssai_legal_768`) | 12,819 | 1,100 | 12,819 | **0** | 12,819 | 1,100 |
| ENVIRONMENT_POLLUTION (`env_legal_768`) | 2,465 | 2,465 | — | 2,465 | 0 | 0 |
| BUSINESS_CIVIL (`commercial_legal_768`) | 7,584 | 7,584 | — | 7,584 | 0 | 0 |
| ANIMAL_SLAUGHTER (`animal_legal_768`) | 1,480 | 1,480 | — | 1,480 | 0 | 0 |
| WB_STATE (`wb_state_legal_768`) | 1,735 | 1,735 | — | 1,735 | 0 | 0 |
| CRIMINAL (`criminal_legal_768`) | 1,260 | 1,260 | — | 1,260 | 0 | 0 |
| **TOTAL** | **27,343** | **15,624** | **12,819** | **14,524** | **12,819** | **1,100** |

> **All five non-FSSAI domains are perfectly reconciled** — Neo4j and Qdrant agree 1:1 on every chunk_id. The ONLY discrepancy in the entire corpus is the FSSAI pair, and it is fully explained in §5.

### 2.2 content_hash duplicates within each store

| Store | Hash source | Distinct hashes | Duplicate groups | Excess rows |
| --- | --- | ---: | ---: | ---: |
| Neo4j | SHA-256 of stored `chunk_text` (KG text itself truncated to 500 chars by `build_kg_corpus.py`) | 22,927 | 1,043 | 4,416 |
| Qdrant | payload `content_hash` (real SHA-256) | 15,265 | 359 | 733 |
| FSSAI DB | `content_hash` column (real SHA-256) | 9,242 | 643 | 3,577 |

Notes:
- The DB's 12,819 rows carry only 9,242 distinct content hashes (3,577 duplicate rows). These are **within-corpus repeats** (same section text re-chunked across documents — e.g. `Food_Additives_Regulations-4.pdf` 5,913 rows), **not** identity collisions: every `(document_id, chunk_index)` is unique (`uq_chunk_doc_index`), so each duplicate row is a legitimate repeated passage with its own UUID.
- Qdrant's 733 duplicate rows are the same class (repeated legal text across chunks), consistent with the earlier NEO4J_QDRANT_AUDIT duplication table (animal 23.9% / commercial 8.1% near-dup text).
- No chunk_id in any store maps to two different content hashes → **no corruption signal**.

---

## 3. Classification of discrepancies (Step 3)

Bucket definitions applied (from the task spec). Every unmatched chunk was tagged by the reconcile script; cause counts below are from `corpus_identity_reconcile.json → classification`.

### 3.1 Neo4j − Qdrant (12,819 chunks, all FOOD_SAFETY)

| Cause | Count | Evidence |
| --- | ---: | --- |
| **FAILED_INGESTION** | 12,819 | Each chunk_id exists in the **local DB** (`LegalChunk.id`) but has **no point in the live `fssai_legal_768` collection** (0/1,100 overlap). The KG ingested them from the DB (authoritative); the Qdrant collection was never updated with them. No length/quality/OCR filter exists in the DB → all present, none filtered. |
| INTENTIONAL_DEDUP | 0 | (Dedup-by-design would mean the DB itself dropped them; it did not.) |
| FILTERED_BY_DESIGN | 0 | No config/rule cited — none applies (DB holds the full 12,819). |
| OCR_NOISE_DROPPED | 0 | No OCR/length threshold evidence; 4,791 chunks have char_count < 20 and are still in the DB (and in Neo4j). |
| UNEXPLAINED | 0 | — |

### 3.2 Qdrant − Neo4j (1,100 points, all `fssai_legal_768`)

| Cause | Count | Evidence |
| --- | ---: | --- |
| **FAILED_INGESTION** | 1,100 | Points carry document/chunk UUIDs that match **no** current-DB row (0/1,100 chunk-id overlap; 0/14 doc-id overlap) — they were upserted from a **different DB snapshot** (§5). |
| UNEXPLAINED | 0 | — |

### 3.3 FSSAI DB − FSSAI Qdrant (12,819 DB chunks vs 1,100 live points)

| Cause | Count | Evidence |
| --- | ---: | --- |
| **FAILED_INGESTION** | **11,218** | Chunks exist in the DB (and Neo4j) but their content_hash does **not** appear anywhere in the live collection → never upserted under any identity. |
| INTENTIONAL_DEDUP (content-represented) | 1,601 | Chunk content IS present in the live collection **under a different UUID** (content_hash match) — the collection holds an older snapshot of the same text (§5). Identity absent, content present. |
| FILTERED_BY_DESIGN / OCR_NOISE_DROPPED | 0 | No rule or threshold exists that removes DB chunks (all 12,819 survived to DB + KG). |
| UNEXPLAINED | 0 | — |

### 3.4 FSSAI Qdrant − FSSAI DB (1,100 live points vs 12,819 DB chunks)

| Cause | Count | Evidence |
| --- | ---: | --- |
| INTENTIONAL_DEDUP (stale snapshot) | 1,096 | Point content_hash exists in the current DB → same text, old UUID (99.6%). |
| **FAILED_INGESTION** (orphan) | **4** | Four points whose content is **not** in the current DB — individually identified in §5.4. |

---

## 4. Quantified summary (Step 4)

| Domain | Neo4j | Qdrant | Local DB | Matched | Dedup | Failed | Filtered | OCR | Unexplained |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **FOOD_SAFETY (FSSAI)** | 12,819 | 1,100 | 12,819 | 0 | 1,601* | 11,218 | 0 | 0 | 0 |
| ENVIRONMENT_POLLUTION | 2,465 | 2,465 | — | 2,465 | 0 | 0 | 0 | 0 | 0 |
| BUSINESS_CIVIL | 7,584 | 7,584 | — | 7,584 | 0 | 0 | 0 | 0 | 0 |
| ANIMAL_SLAUGHTER | 1,480 | 1,480 | — | 1,480 | 0 | 0 | 0 | 0 | 0 |
| WB_STATE | 1,735 | 1,735 | — | 1,735 | 0 | 0 | 0 | 0 | 0 |
| CRIMINAL | 1,260 | 1,260 | — | 1,260 | 0 | 0 | 0 | 0 | 0 |
| **TOTAL** | **27,343** | **15,624** | **12,819** | **14,524** | **1,601** | **11,222** | **0** | **0** | **0** |

\* `Dedup` = content-represented in the live FSSAI collection under a different UUID (older snapshot), not intentional de-duplication. `Failed` = 11,218 DB chunks with no representation in `fssai_legal_768` + 4 orphan collection points. **`Unexplained = 0` across the entire corpus.**

The "~1,700-point gap" referenced in the task brief does not exist as a standalone bucket: the FSSAI delta decomposes into **1,601 content-duplicate** + **11,218 failed** (+ 1,096 stale + 4 orphan on the collection side). The **11,218 failed** figure is the true FSSAI coverage gap (87.5% of the DB corpus absent from `fssai_legal_768`).

---

## 5. FSSAI root cause — 12,819 → 1,100 (Step 5)

### 5.1 The question

Was the 12,819 → 1,100 drop a failed batch job, a filter, a different chunking strategy, or partial/incomplete ingestion?

### 5.2 Verdict — with evidence, not inference

**The live `fssai_legal_768` collection was built from a DIFFERENT (older) DB snapshot and never rebuilt to match the current 12,819-chunk corpus. The current corpus was ingested into a Qdrant target that no longer holds those points (dropped/recreated), and the current DB's upserts were never (re)run against the live collection.** This is **partial/incomplete ingestion across DB-snapshot boundaries** — a FAILED_INGESTION class, with a stale-snapshot duplicate tail.

Evidence chain (each item independently verified 2026-08-11):

1. **Zero identity overlap (live queries):**
   - `fssai_legal_768` point chunk_ids ∩ current `LegalChunk.id` = **0 / 1,100**.
   - `fssai_legal_768` point document_ids ∩ current `LegalDocument.id` = **0 / 14**.
   - The collection's 14 documents' per-doc chunk counts *exactly* match 14 current-DB documents (e.g. 606/606 Compendium, 285/285 Alcoholic Beverages, 76/76 Contaminants, 32/32 notification) — **same documents, same chunking, different UUIDs**.

2. **Content is identical — it is an old snapshot, not different text (live query):**
   - 1,092 of 1,093 unique content hashes in `fssai_legal_768` (99.9%) exist in the current DB → the points are the current corpus's own text with regenerated UUIDs.

3. **The DB's back-references are self-referential, not live (SQLite query):**
   - `legal_chunk.qdrant_point_id == legal_chunk.id` for **12,819 / 12,819** rows. No live Qdrant point carries these ids (see item 1). The stamp is a write-time self-reference, **not** proof of a live point.

4. **The last logged FSSAI ingestion contradicts the live collection (log evidence):**
   - `ingest_run.log` (2026-08-09): **24 docs, 21 indexed, 3 failed**, with 21 docs reporting `points_upserted > 0` (e.g. Compendium 606, FSS Act 722, FSS Amendment 2-2011 784) using the **current DB's document ids**.
   - The 3 failures: `Food_Additives_Regulations-4.pdf` (5,486 chunks; `Qdrant upsert failed after retry: [Errno 10054] An existing connection was forcibly closed by the remote host`), `FSS_Amendment_Act_3-2023.pdf` (2,317 chunks; same error), `Nutraceuticals_Regulations.pdf` (644 chunks; `The write operation timed out`).
   - `resume_a3.log` / `resume_fa.log` / `resume_nu.log` (same evening) show the 3 failures re-ingested **successfully** (2,523 / 5,913 / 648 points) — again with current-DB ids.
   - **Yet NONE of those ~12,819 points exist in the live collection today** → the collection that received them no longer exists, or the upserts targeted another collection that was dropped. Either way the live `fssai_legal_768` is not the artifact of the current corpus build.

5. **No persisted in-DB job history contradicts this (SQLite query):**
   - `audit_log` = **0 rows**, `record_audit` = 0 rows → the `IngestionLogger` audit trail (`entity_type="rag_ingestion"`) was never persisted (best-effort logger, empty DB).

6. **Timestamps corroborate the snapshot theory (payload evidence):**
   - `fssai_legal_768` point `created_at`: **1,096 on 2026-08-09**, 3 on 2026-08-10, 1 on 2026-08-11 (stray partial upserts, §5.4).
   - Current DB `LegalChunk.created_at`: **2026-08-10 01:14–01:15 UTC** (fresh UUID generation); DB `chunk_enrichment` rows 01:17–01:19 UTC (12,819 rows = full enrichment on the current corpus).

7. **Chunking is NOT the difference (measured):** the chunker is the same `Chunker`/legal-engine pipeline for both DB and Qdrant writes; per-doc chunk counts match exactly between the live collection's 14 docs and their current-DB twins (item 1). No alternate chunking strategy was applied to FSSAI only.

### 5.3 What this is NOT

| Ruled-out cause | Why |
| --- | --- |
| Min-length / quality filter | 4,791 DB chunks have char_count < 20 and survived to DB + KG; no threshold removed them. |
| OCR noise drop | Same as above; OCR only adds text, never removes DB rows. |
| Intentional dedup | Dedup would collapse rows in the DB; all 12,819 rows (incl. 3,577 hash-duplicates) are present. |
| Different chunking for FSSAI | Chunk counts match exactly across snapshot boundaries. |
| Single failed batch job | Logs show 21/24 + 3/3 resumed OK; the failure mode was *lost collection state*, not a single job abort. |

### 5.4 Individually justified orphan points (the only 4 non-DB-matching collection points)

| Qdrant point id | document_id | created_at | Justification |
| --- | --- | --- | --- |
| `fc93b2aa-…` | `3c4664e3-…` | 2026-08-10T02:24:24Z | Stray partial upsert (1 point) after the snapshot; content not in current DB. |
| `79103cca-…` | `8c6b228f-…` | 2026-08-10T14:05:40Z | Stray partial upsert (1 point). |
| `40965143-…` | `1335c4e2-…` | 2026-08-10T15:05:30Z | Stray partial upsert (1 point). |
| `eb0119bb-…` | `ee4338d9-…` | 2026-08-11T02:57:58Z | Stray partial upsert (1 point) — the "one point added since the audit" noted in NEO4J_QDRANT_AUDIT_REPORT §4. |

These are aborted/partial upserts from other DB states; each is a single point with a non-DB document UUID. **Individually explained, not averaged away.**

---

## 6. Canonical decision & correct corpus size (Step 6)

### 6.1 Source of truth for chunk count

| Store | Role | Basis |
| --- | --- | --- |
| **Local DB (`legal_chunk`)** | **Source of truth for FSSAI** | Holds the full 12,819-chunk corpus with content hashes, enrichment rows, and unique `(document_id, chunk_index)`; the KG's FSS chunks and the 08-09/08-10 ingestion logs were built from it. |
| **Qdrant multi-domain (5 collections)** | Source of truth for non-FSSAI | 14,524 points, 1:1 with Neo4j chunk_ids, no orphans, no collisions. |
| **Qdrant `fssai_legal_768`** | **NOT a source of truth** | Stale snapshot (14/29 docs, 1,100/12,819 chunks); flagged P1-4. |
| **Neo4j** | **Aggregate mirror** | 27,343 = 12,819 DB + 14,524 Qdrant; 1,858/1,861 provisions linked; used for graph expansion, not as an independent source. |

**Canonical total corpus size = 27,343 chunks** (12,819 FSSAI DB + 14,524 multi-domain Qdrant), replacing the "~28,000" figure. Effective *vector-searchable* corpus = **15,624** points (14,524 healthy + 1,100 stale FSSAI).

### 6.2 Go / no-go *(as of the audit — FSSAI verdict superseded by §8 execution)*

```
BENCHMARK READINESS:

Multi-domain retrieval (env / commercial / animal / wb_state / criminal):
    ✅ GO — 14,524/14,524 points reconciled 1:1 with the KG; zero orphans, zero collisions.

KG hybrid expansion (RAG_KG_EXPANSION / KGContextExpander):
    ✅ GO — Neo4j's 27,343-chunk inventory is the canonical corpus union; provision links 99.8%.

FSSAI dense retrieval against fssai_legal_768:
    ❌ NO-GO (blocking) at audit time — 1,100/12,819 chunks (8.6%) from a stale DB snapshot.
       ➜ REMEDIATED 2026-08-11: collection rebuilt to 12,819 points (identity-preserving),
       verified 12,819/12,819 matched / 0 failed / 0 unexplained. **Now GO — see §8.**

Any benchmark that mixes fssai_legal_768 into a "full-corpus" number:
    ✅ GO after §8 — Qdrant now stores 27,343 points = the full 27,343-chunk corpus.
```

**Bottom line:** the corpus identity question is **resolved and documented** — there are **0 unexplained chunks** on the corpus side. The reconciliation is complete; the one open remediation identified (FSSAI re-ingest, P1-4) was **executed on the same date (§8)**. Multi-domain, KG-hybrid, and now **FSSAI dense retrieval** may proceed.

---

## 8. Post-audit addendum — P1-4 FSSAI re-ingest EXECUTED (2026-08-11)

Executed per `docs/FSSAI_REINGEST_PLAN.md` (this report's flagged remediation). Not part of the
read-only audit; this section records the executed remediation + verification.

### 8.1 Execution steps (evidence)

| Step | Command / artifact | Outcome |
| --- | --- | --- |
| STEP 1 — backup | `scripts/export_fssai_backup.py` | `reports/fssai_legal_768_pre_reingest_backup.json`: **1,100 points with vectors** (dense + sparse, JSON-safe) |
| STEP 2 — rebuild | `python scripts/reingest_fssai_from_db.py --delete-collection` (dry-run first; backup-guard passed) | `reports/fssai_reingest_run.log`: **29/29 docs OK, 12,819/12,819 points upserted, `finished in 1961s ok=True`** |
| STEP 3 — stamp | `python scripts/stamp_qdrant_payload_identity.py --collection fssai_legal_768` (dry-run first) | **12,819 updated**; `provision_id` on 3,126 section-bearing chunks, `instrument_id` 12,819, `legal_domain=FOOD_SAFETY` 12,819, `status` 12,819; **0 unknown documents**; 4 payload indexes created |

### 8.2 Post-rebuild verification (all read-only, live)

Re-ran `corpus_identity_extract.py` + `corpus_identity_reconcile.py` against the rebuilt collection:

| Check | Expected | Actual |
| --- | --- | --- |
| `fssai_legal_768` exact count | 12,819 | **12,819** ✅ |
| FOOD_SAFETY reconcile `matched` / `failed` / `unexplained` | 12,819 / 0 / 0 | **12,819 / 0 / 0** ✅ |
| `fssai_db_in_fssai_qdrant` / `fssai_qdrant_not_in_db` | 12,819 / 0 | **12,819 / 0** ✅ |
| `act_name` coverage | 100% | **12,819 / 12,819 (100%)** ✅ |
| distinct documents matching DB | 29 / 29 | **29 / 29** ✅ |
| content-hash parity (unique DB hashes present) | all | **9,242 / 9,242** ✅ (12,819 rows share 9,242 unique hashes — content-duplicate rows preserved by design) |
| `provision_id` → Neo4j `LegalProvision` | 1:1 | **8/8 sampled = 1 match each** ✅ |
| Qdrant total | 27,343 | **27,343** (= 14,524 multi-domain + 12,819 FSSAI) ✅ |
| cross-hash chunk_id collisions | 0 | **0** ✅ |

### 8.3 Corpus-wide result after remediation

```
Neo4j 27,343  =  Qdrant 27,343  =  DB 12,819 + multi-domain 14,524     (ALL THREE STORES RECONCILED)
FSSAI DB 12,819 = fssai_legal_768 12,819                               (identity-preserving rebuild)
```

**Every chunk in the corpus now has a resolvable vector-side point with full identity.**

### 8.4 Residual notes

- The 4 orphan points and 1,096 stale-snapshot duplicates documented in §5.4/§3.4 were **deleted with the collection** (`--delete-collection`); their content is preserved in the STEP-1 backup JSON.
- The DB was **never modified** — `LegalChunk.qdrant_point_id == id` is now true against the live collection by construction.
- Neo4j was **never modified** — the KG already mirrored the DB chunk ids.
- Rollback remains possible from `reports/fssai_legal_768_pre_reingest_backup.json` (plan §7, sparse-aware).
- **Tests:** `tests/test_reingest_fssai.py` (15) added for `scripts/reingest_fssai_from_db.py` — all pass.

---

## 7. Artifacts

| File | Contents |
| --- | --- |
| `neo4j_chunks.csv` | 27,343 rows — chunk_id, provision_id, instrument_id, document_id, legal_domain, qdrant_point_id, qdrant_collection, section_number, chunk_index |
| `qdrant_chunks.csv` | 15,624 rows — point_id, chunk_id, collection, legal_domain, document_id, act_name, provision_id, instrument_id, status, section_number, content_hash, created_at, document_uri |
| `fssai_local_chunks.csv` | 12,819 rows — chunk_id, document_id, document_type, section_number, chunk_index, content_hash, qdrant_point_id, created_at, char_count, word_count |
| `corpus_identity_reconcile.json` | Full machine-readable reconciliation + classification + FSSAI root-cause evidence |
| `corpus_identity_extract.py` / `corpus_identity_reconcile.py` | Read-only tooling (re-runnable) |
| `ingest_run.log`, `resume_a3.log`, `resume_fa.log`, `resume_nu.log` | FSSAI ingestion + resume logs quoted in §5.2 |

*No data was modified in any store during this audit.*

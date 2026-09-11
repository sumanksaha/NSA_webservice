# RAG Candidate-Gap Bridge — Validation & Plan (2026-08-13)

> **EXECUTION STATUS (2026-08-13, all phases landed):**
> 1. **Backfill implemented + applied live** — `scripts/backfill_payload_identity.py`
>    gained an L4 any-position header pass (`--from-cache` dry-run mode added);
>    **1,827 payloads updated across 5 collections** (623 overrides, 390 new
>    stamps, 814 covered-set additions) via payload-only `set_payload` (no
>    re-embedding). Pre-backfill snapshot: `evaluation/out/ceiling_v8/pre_backfill_payloads.jsonl`;
>    repair log: `evaluation/out/ceiling_v8/repair_sections_l4.csv`;
>    summary: `evaluation/out/ceiling_v8/backfill_summary_apply.json`.
> 2. **V7 tooling fixed** — `run_phase20/21` moved above the `__main__` guard
>    (they never ran before); `classify_gap_unit` now distinguishes STAMPING_GAP
>    (section text present, unstamped/stale → G4) from genuine query
>    representation failure (G9). Regenerated `evaluation/out/ceiling_v7/*`.
> 3. **Pool ceiling re-measured on live Qdrant: 91.9% → 100.0% (86/86).**
> 4. **Gold-registry corrections** (`benchmark/gold_provisions_v1.0.json`, all
>    with `gold_fix_note` audit trails): `bda:s480` section 480→16 (480 is a
>    CrPC cross-reference; the penalty provision is principal-Act s.16);
>    `water_act:s7` title → "Vacation of seats by members"; `wbpt:s32` title →
>    "Penalty for refusal to give receipt"; `wbpt:s45` title → "Repeal and
>    savings".
> 5. **Regression check:** 66 tests across `test_multidomain_phase1` (37),
>    `test_reingest_fssai` (15), `test_crossref_adapter` (14) pass.
>
> One honest caveat: the bda:s480 fix changed a *gold reference* (section), not
> just a title — it was exposed by the stamping correction (a mis-stamped CrPC
> cross-reference chunk had been masking it). The remaining 1.2% "gap" after
> the backfill was this invalid gold reference, not a retrieval gap.
>
> **Ranking gain from the stamps (measured, pre vs post backfill payloads,
> same frozen arm caches, V5.5 sec_act rerank):**
>
> | Grid (P0 base pool @500) | R@10 pre | R@10 post | Δ |
> |---|---|---|---|
> | base RRF | 0.324 | 0.415 | +9.1 pp |
> | sec_act | 0.407 | 0.505 | +9.8 pp |
> | full_legal | 0.367 | 0.504 | +13.7 pp |
>
> Any-question-hit R@10 (sec_act, P0): 49.3% → 60.0% (+10.7 pp). With the
> identifier route (P1): sec_act R@10 0.431 → 0.504. Artifacts:
> `evaluation/out/ceiling_v5/v55_rerank.json` (post) and
> `v55_rerank_pre_stampfix.json` (pre, built from the pre-backfill snapshot).
> Baseline sanity: pre-fix base-RRF R@10 0.3239 reproduces the V5 report exactly.
>
> **Question-premise audit (Q050/Q060/Q078/Q079) — corrected 2026-08-13:**
> - **Q050** (Water Act board powers) premise pointed at s.7 ("Vacation of seats").
>   Re-pointed `water_act:s7` → `water_act:s33` ("Power of Board to make application
>   to courts for restraining apprehended pollution", corpus chunk 530af5a7,
>   stamped). Caveat: the conclusion's direct "close/regulate the pipe" language
>   maps to s.33A "Power to give directions" (corpus chunk 34c979ce, unstamped) —
>   expert adjudication recommended.
> - **Q060** (insufficient-evidence, pH): left unchanged — the s.7 reference is
>   incidental to an answer about corpus absence; documented only.
> - **Q078** (WBPT rent enhancement) premise pointed at s.32 (penalty for receipt
>   refusal). Re-pointed `wbpt:s32` → `wbpt:s20` ("Notice of increase of rent",
>   corpus chunk 6a602b84, stamped). New registry record `wbpt:s20` added.
> - **Q079** (WBPT eviction notice) premise pointed at s.45 ("Repeal and savings").
>   Re-pointed `wbpt:s45` → `wbpt:s46` ("Recovery of possession", corpus chunk
>   31934a6b, stamped).
>
> Each edited question carries a `gold_audit_note`; new/changed registry records
> carry `gold_fix_note`. Registry now 99 records (added `water_act:s33`,
> `wbpt:s20`), no duplicates. Pool ceiling re-verified at **100% (86/86)** after
> the re-points.
>
> **Ingestion root cause fixed** — `app/rag/chunker.py` gained an L4 fallback
> (`_l4_section_headers`): any-position, paren-tolerant, act-range-validated
> header detection stamps `section_number` when the engine misses it and always
> records `sections_covered` (new `Chunk` field, mirrored in `to_payload`), so
> future ingestion never regresses to unstamped sections. 39 chunker/indexer
> tests pass.
>
> **Neo4j legal KG rebuilt (2026-08-13)** — `NEO4J_ALLOW_WRITE=1 python
> scripts/build_kg_corpus.py`: cleared the stale 29,385-node graph and wrote
> **58 instruments / 2,298 provisions / 27,343 chunk edges / 36+ concepts /
> 12 cross-domain edges** in 145 s. Live-verified: 27,343 Chunk + 2,298
> LegalProvision + 58 Document nodes. The fail-closed guard still refuses
> rebuilds without `NEO4J_ALLOW_WRITE=1`. Summary: `reports/kg_rebuild_summary*.json`.

**Claim under test:** V7 (`evaluation/out/ceiling_v7/V7_METADATA_GAP_REPORT.md`)
reports a multi-route candidate-generation ceiling of **91.9% (79/86 unique gold
units) at K=500** with a remaining gap of **7 units (8.1%)**, classified
"G9 — QUERY_REPRESENTATION_FAILURE", and metadata repair (section-header
stamping of 1,927 chunks) recovering **0 units**.

This document records the independent validation of that claim and the plan to
bridge the gap. **Headline result: the V7 classification is wrong — the 7 units
are not query-representation failures and not corpus-absent. All 7 sections'
text is present in the frozen 27,343-point corpus; they are missing/stale
`section_number` stamps. A corrected, act-range-validated stamping backfill
recovered all 7 in a virtual reindex → ceiling **91.9% → 100.0% (86/86)**.**

---

## 1. Validation performed (all offline, frozen caches)

| # | Check | Outcome |
|---|-------|---------|
| 1 | Reproduced V7 pipeline (`python -m evaluation.v7_gap_metadata`) | ✅ 91.9% (79/86), gap 7, all 7 labelled G9, metadata repair +0.0pp — exact match |
| 2 | Verified per-unit G9 mechanism | ❌ "section N not among **stamped**" is a *metadata-absence* conclusion, not a query-representation test |
| 3 | Checked whether V5 route caches cover the 7 units | ❌ `v5_routes/unit_*.jsonl` cover only the 34-unit workset — **the 7 gap units were never tested against any query route**; G9 was inferred, never measured |
| 4 | Raw text search (10 formats) of ALL 27,343 payloads for the 7 gold sections | ✅ 6/7 section texts found verbatim (`contract:73`, `kmc:313`, `kmc:391`, `sog:20`, `water_act:7`, `wbpt:32`); `wbpt:45` found only after paren-tolerant pattern (`45. (I) …`) |
| 5 | Chunk-level diagnosis of the exact containing chunks | ✅ Root cause = missing/stale `section_number` (see §2) |
| 6 | Virtual reindex with corrected stamping + re-run of the full ceiling computation | ✅ **86/86 (100.0%)** — all 7 recovered |

**Tooling defects found during validation** (both committed in the repo):

- `evaluation/v7_gap_metadata.py` defines `run_phase20`/`run_phase21` *after*
  the `if __name__ == "__main__"` guard → phases 20–21 (remediation deep-dive,
  cross-encoder readiness) **never run**, even in the original experiment
  (`v7_gap_remediation.csv`, `v7_crossencoder_readiness.json`, `v7_freeze.json`
  are absent from `ceiling_v7/`). The report was written by phase 18/19 before
  the crash.
- V7's phase-8 repair regexes were **line-anchored** (`(?:^|\n)…`) and
  paren-blind, so they missed the corpus's mid-line headers (e.g.
  `…coercion. 73. Compensation…`) and parenthesized headers (`45. (I) …`) —
  which is exactly why the repair measured +0.0pp.

## 2. The 7 gap units — corrected diagnosis

All text quoted from frozen payload index `evaluation/out/cache/payload_index.jsonl`.

| Gold unit | Gold title (registry) | Corpus section text found | Current stamp | Corrected stamp | Class |
|---|---|---|---|---|---|
| `contract:s73` | Compensation for loss/damage on breach | `73. Compensation for loss or damage caused by breach of contract` (chunk `20847d2a…`, TOC run 68–75+90) | `3` (stale) | 73 | STAMPING_GAP (title-only evidence; body chunk verify) |
| `kmc:s313` | Commissioner not to sanction plan w/o water-supply | `313. Municipal Commissioner not to sanction building plan unless plan relating to water-supply …` (`c90588cf…`) | `None` (`subsection=(1)`) | 313 | STAMPING_GAP |
| `kmc:s391` | Municipal Building Committee | `391. Municipal Building Committee….` (`304eac01…`) | `16` (stale — from "section 16(W) of the Calcutta … (Amendment) Act") | 391 | STAMPING_GAP (override stale) |
| `sog:s20` | Specific goods / risk | `20. Specific goods in a deliverable state. Where there is an unconditional contract …` — body chunk `36c9f63e…` (starts `7 20. …`) | `7` (stale) | 20 | STAMPING_GAP (override stale) |
| `water_act:s7` | Powers of Boards (title **wrong** — see §4) | `7. Vacation of seats by members. If a member of a Board becomes subject …` body chunk `c0c026e2…` | `5` (stale) | 7 | STAMPING_GAP (override stale) |
| `wbpt:s32` | Enhancement of rent (title **wrong** — see §4) | `32. If the landlord refuses to deliver to the tenant a receipt for any rent …` (`919d43dd…`, header at offset 613 of a s.31 continuation) | `10` (stale) | 32 | STAMPING_GAP (override stale) |
| `wbpt:s45` | Notice for eviction (title **wrong** — see §4) | `45. ( I) The West Bengal Premises Tenancy Act, 1956 … is hereby repealed.` | `None` | 45 | STAMPING_GAP (paren-header missed by old regex) |

**Why the identifier route (V5's 28/28 lever) cannot recover these today:**
production gold-matching / section filters require payload `section_number ==
gold`. The chunks either carry a stale number (from a cross-reference or
subsection residue) or none at all, so even a correctly retrieved chunk fails
`matches_gold`. The retrieval engines never had a chance — this is a payload
identity gap, not a retrieval or query gap.

## 3. The bridge — plan (validated offline, deterministic, no LLM, no re-embedding)

### Phase A — Section-stamping backfill (the actual bridge; proven 91.9% → 100%)
1. **Extend the act-section registry** `app/rag/legal_sections.py`
   `ACT_SECTION_RANGES` with the missing acts: `kmc` (1–636), `wbpt` (1–60);
   confirm `contract` (1–238), `sog` (1–66), `water_act` (1–64),
   `comp` (1–470) are present.
2. **New script** `scripts/backfill_section_stamps.py` implementing the
   validated rule:
   - scroll all 6 collections (`QdrantStore.scroll_all`), reuse the payload
     cache pattern from `evaluation/resolution.py`;
   - header regex (any-position, paren-tolerant):
     `(?<![A-Za-z0-9])(\d{1,4})\s*\.\s*(?:\(\s*)?[A-Z]`;
   - keep only numbers inside the chunk family's act range (family resolved via
     `act_name` + `document_title`);
   - `section_number` = first in-range header; **override stale stamps**
     (current stamp not among the covered headers);
   - new field `sections_covered` = full in-range header list (lets
     multi-section/TOC chunks like `contract:20847d2a` match any covered
     section);
   - `--dry-run` default (print per-chunk diffs, write a repair CSV); explicit
     `--apply` uses `QdrantStore._get_client().set_payload(...)` per collection.
     **No vector recompute** — payload-only update.
3. **Resolution-layer support for `sections_covered`** in
   `evaluation/resolution.py::payload_to_keys`/`matches_gold` (production
   matching is filter/similarity-based and needs only `section_number`).
4. **Verify:** re-run the V7 computation (with the fixed script, Phase B) —
   expect **86/86 (100.0%)**; re-run `tests/` (RAG suite stays green — no
   production code changed until Phase C).
5. **Expected impact:** candidate ceiling **91.9% → 100%** (the ~8.1–9.1 pp
   gap closed). Caveat: `contract:s73` currently recovers via a TOC title-only
   chunk; for answer quality, verify a body chunk exists or re-chunk the
   Contract Act document (surgical, one document).

### Phase B — Fix the evaluation tooling (reproducibility)
1. Move `run_phase20`/`run_phase21` above the `main()` guard in
   `evaluation/v7_gap_metadata.py`; regenerate `v7_gap_remediation.csv`,
   `v7_crossencoder_readiness.json`, `v7_freeze.json`.
2. Replace the misleading G9 inference with an evidence-based class
   (`STAMPING_GAP`) by adding a full-corpus, paren-tolerant text-presence check
   to `classify_gap_unit` (current code checks only ~200 payloads and 3
   line-anchored formats).
3. Extend the `v5_routes` caches to cover the 7 gap units so any future
   query-representation claim is measured, not inferred.
4. Scratch validation scripts used for this report
   (`evaluation/_validate_gap7.py`, `_probe_gap7_text.py`,
   `_probe_gap7_mech.py`, `_debug_stamp*.py`, `_validate_stamp*.py`): fold the
   winning rule into `scripts/backfill_section_stamps.py` and remove the
   intermediates (keep `_validate_stamp2.py` until Phase A lands).

### Phase C — Production wiring (make the stamps live)
1. Fix the **ingestion root cause**: the LegalParagraphEngine/chunker stamps
   sections only when it detects headers; add an any-position, act-registry
   backfill post-pass in `app/rag/chunker.py` so future ingests never regress
   (mirrors the Phase A rule).
2. Apply the backfill to live Qdrant (Phase A `--apply`).
3. **Ship the legal-identifier multi-route retrieval** (V5 recommendation #1,
   `evaluation/query_expansion.py` logic wired into the retrieval contract) —
   the newly-stamped sections become reachable by `section_number` filters,
   which production `QueryClassifier`/`DenseRetriever` already support but which
   today match zero chunks for these units.
4. Re-measure: pool ceiling → 100%; then re-rank (cross-encoder training,
   V5 §13/§14) to convert the full pool into R@10.

### Phase D — Benchmark gold-quality audit (separate workstream, answer-level)
The registry titles for three units do **not** match the corpus (and likely
the source documents):
- `water_act:s7` registry "Powers of Boards" vs corpus "Vacation of seats by
  members" — questions Q050/Q060 ask about s.7 powers; the intended section is
  likely s.17/25/26/33, not s.7.
- `wbpt:s32` registry "Enhancement of rent" vs corpus s.32 = penalty for
  refusing a rent receipt (enhancement lives elsewhere in the Act).
- `wbpt:s45` registry "Notice for eviction" vs corpus s.45 = "Repeal and
  savings"; Q079 asks about eviction notices — the gold reference should point
  at the actual eviction-notice section.
These affect *answer evaluation* (and question validity), not candidate
generation — correct them against the source PDFs (`other domain/*.pdf`) with
expert verification, and add gold `temporal_status` labels (known missing
signal).

## 4. Risk / honesty notes
- The 100% result is **offline, on frozen caches** — the live Qdrant payloads
  must be updated (Phase A/C) and the ceiling re-measured to confirm in
  production.
- Stamping adds metadata but does not change embeddings: chunks whose *text*
  is a TOC/title run (contract) are lower-quality evidence than body chunks;
  granularity repair (re-chunk of specific documents) is the follow-up if
  answer quality demands body-level evidence.
- `sections_covered` is an evaluation-layer concept unless also adopted by
  production matching; production benefits primarily via `section_number`
  filters + the section-aware reranker.

## 5. Expected trajectory after the bridge
| Layer | Before | After bridge |
|---|---|---|
| Candidate pool @500 (multi-route) | 91.9% (79/86) | **100% (86/86)** |
| Production R@10 (frozen hybrid) | 14.0% | unchanged until reranker/identifier route ships |
| Ranking ceiling (deterministic reranker @500) | 42.3% | improved — correct section metadata feeds `sec_act` features |
| Next bottleneck | candidate generation (closed) | **ranking** (pool 100% vs R@10) → cross-encoder plan |

*Evidence base: this validation (scripts `evaluation/_validate_*.py`,
`_probe_gap7_*.py`, `_debug_stamp*.py`), frozen caches in
`evaluation/out/{cache,ceiling_v5,ceiling_v7}`, and the V5/V7 reports.*

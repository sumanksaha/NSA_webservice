# CE v2 Improvement Plan

> **Status:** Evaluated (2026-08-17) — revised implementation plan below. No code written yet.
> **Model:** `evaluation/out/models/legal_ce_v2_K500`
> **Baseline:** `evaluation/out/models/legal_ce_v1`
> **Training script:** `evaluation/train_legal_ce_v2.py`
> **Error analysis:** `evaluation/out/ce_v2_eval_output.log`, `evaluation/out/ce_v2_error_analysis.log`

## Current State (verified against the logs)

- V2 trained: 3 epochs, margin ranking loss, T1→T2→T3 curriculum, 14,629 pairs (10,347 train / 1,920 val / 2,362 test by question-id split)
- Test (21 queries, 2,362 pairs): R@1 +19%, MRR@10 +8%, nDCG@10 +6%, pairwise acc **−3.5%** (0.5826 → 0.5478), margin +0.44 (4.4× larger)
- 0 retrieval-fail cases (all golds in top-10 for both models)
- Top bottleneck: hierarchy/version confusion — 8 queries (Q016, Q020, Q049, Q080, Q085, Q097, Q100, Q102)
- Secondary: same-section hard negatives — 3 queries (Q054, Q118, Q120)
- Per-domain pairwise acc: fssai −6.5% (1,608 pairs), epa +18.1%, contract +9.6%, but kmc −35%, srf −50%, water_act −30% (tiny samples: 40/10/70 pairs — CIs meaningless)

---

## §0 — Evaluation of the original plan (what's valid, what's broken)

The original P1–P5 analysis is directionally right (hierarchy confusion → section signal; same-section negatives → harder negatives; domain imbalance → re-balance). Five things block it from being directly executable:

### G1 — The regression harness did not exist (rebuilt in Step 0, 2026-08-17)
`ce_v2_eval.py` and `ce_v2_error_analysis.py` were referenced by the plan but were **not in the repo** (scratch scripts that produced the logs, never committed). Both reference runs also **crashed**:
- `ce_v2_error_analysis.log`: `UnicodeEncodeError` on `\u0394` under cp1252 (Windows) — the failure-taxonomy table never printed, so the "8 hierarchy + 3 same-section" counts came from a partial run.
- `ce_v2_eval_output.log`: `ZeroDivisionError` on the empty "ambiguous" bucket.

**Resolution (Step 0, done):** both scripts are rebuilt as committed, crash-free tooling — `evaluation/ce_v2_eval.py` (pairwise + ranking + per-domain/tier/difficulty + bootstrap, ASCII-only output, per-(query, chunk) score cache at `out/cache/ce_v2_scores_{v1,v2}.jsonl`) and `evaluation/ce_v2_error_analysis.py` (per-query table + failure taxonomy). The rebuilt harness reproduces the original log **exactly** on every recoverable metric (pairwise acc/margin, R@1/R@5/R@10/R@20, MRR@10, nDCG@10, all 8 per-domain rows, all 3 tier rows, all 21 per-query V1_rk/V2_rk/Type cells). Two divergences, documented in the scripts + baseline JSON: (a) "By Query Difficulty" uses normalized benchmark difficulty (the original's medium=1200/hard=1162 buckets came from a deleted per-pair field and are unrecoverable); (b) the "Ambiguous vs Straightforward" section needs an explicit `--ambiguous-qids` file (the original's per-question labels are gone) and is guarded against the old zero-division crash. The frozen baseline lives at `out/cache/ce_v2_regression_baseline.json` (hierarchy 8 / same-section 3 / other 1 / correct 9 / failures 12). (`evaluation/hard_neg_eval.py` and `evaluation/verify_finetuned_ce.py` remain useful as live-pipeline cross-checks, but they measure a different surface — R@k on benchmark retrieval, not pairwise accuracy.)

### G2 — P1 has a train/serve skew the plan misses
The plan says P1 touches only `pairwise_dataset.py` ("no change needed" to the trainer). But the `§<section>` prefix must be applied **identically at inference time**, or the deployed model silently never receives the signal it was trained with. Inference paths that need the prefix:
- `app/rag/retrieval/reranker.py` — `Reranker._rerank_cross_encoder` and `EnsembleReranker`'s CE head (local CE, `chunk.section_number` is on `RetrievedChunk`)
- `app/rag/retrieval/remote_reranker.py` — `RemoteRerankClient._tei_predict` / local fallback (**client-side**, before the pair is sent to the Modal/TEI endpoint; the served model only sees text)
- `evaluation/rerank_legal.py`, `evaluation/ce_rerank_eval.py`, `evaluation/verify_finetuned_ce.py` — every eval path that builds `(query, text)` pairs
- Re-deploy after retrain: `scripts/push_ce_models.py` (Hub) + `modal_deploy/app.py` (Modal), per `docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md` M0

Shared helper `prefix_passage(text, section_number)` + feature flag (`RAG_CE_SECTION_PREFIX`, default off → zero behavior change for v1/unprefixed models) keeps all paths consistent.

### G3 — P3 is mathematically mis-scoped
Temperature scaling is order-preserving for positive T: `score/T` keeps every pairwise comparison identical. It **cannot fix the −3.5% pairwise accuracy** — the plan's stated objective. Re-scope P3 to what it can do: normalize score *scale* for downstream consumers (EnsembleReranker CE-bonus min-max, `sweep_ce_weights.py`, any thresholding), and explicitly verify rank-invariance in the calibration script. If the real goal is accuracy, P1/P2/P4 are the levers, not P3.

### G4 — Dataset builder drops the metadata P1/P4 need; section coverage is partial
`pairwise_dataset.py` never propagates `section` / `act_name` from the mining records into pair examples (verified: 0/14,629 records carry section metadata). Coverage in the mining file: 706/993 positives (71%), 2416/2720 negatives (89%). Missing positives are mostly instrument-level golds (`wbmo:order4`, `wbmo:order5` — West Bengal Meat Order clauses, no numbered section). P1 needs a defined fallback (no prefix when section is missing) shared between train and serve.

### G5 — P2 data reality + subsection quality
- Same-section negatives already exist for many flagged queries (Q018: 12, Q122/Q143/Q148: 11, Q150: 10, Q118/Q120: 8, Q020: 6) but are **zero** for Q049 (epa), Q050 (water_act), Q102 (fssai s26/s392) → those need re-mining, not just dataset tweaks.
- `same_subsection` is unreliable: Q049 has 7 same-subsection negatives but 0 same-section — the `subsection` payload value repeats across different sections, so a "subsection-level filter" needs validation before mining.
- Guard needed: re-mined pairs must not leak test/val question-ids into train (split is by question-id).

**Subsection payload audit (done 2026-08-17, 27,345 chunks):**
- Coverage: 12,599/27,345 chunks (46.1%) carry a `subsection` value; per-act it varies widely — Companies Act 70.4%, EPA 62.8%, Contract 58.2%, **FSSAI 32.6%**, KMC 29.1%, PCA 16.7%.
- Values are genuine clause markers (`(a)`, `(1)`, `(i)` … — 340 distinct values, 95 digit/paren-like), not junk text.
- **Cross-section collisions confirmed the G5 concern:** 112/340 values (32.9%) appear in ≥2 different sections — worst offenders `(1)` in 170 sections, `(2)` in 108, `(b)` in 106. A standalone `same_subsection` match is therefore noise (this is exactly why Q049's 7 same-subsection negatives are meaningless).
- **Within-section distinctness is good:** 0 sections where every chunk shares a single subsection value — so *within a section*, subsection values DO disambiguate chunks.
- **P2 filter design (de-risked, data-backed):** require `same_section AND same_subsection` — never subsection alone. The existing `hard_negative_rank` already weights `same_subsection` (1.0); P2's `--subsection-filter` must AND it with the section equality, and for fssai (33% coverage) fall back to same-section-different-chunk negatives without a subsection match (the existing tier-3 pool already provides these — Q118/Q120 already have 8 same-section negatives each).

### G6 — Subsection coverage: root cause + improvement paths (evaluated 2026-08-17)

**Where `subsection` comes from (verified):** exactly one writer — `app/rag/chunker.py::_extract_subsection_markers`, a *leading parenthetical-chain* regex (a `(1)(a)`-style chain anchored at the start of the text). It only fires when the chunk text **starts** with a chain like `(1)(a)`. The engine's `SectionParser` classifies marker chains / paragraph types (SUBSECTION/SUBSUBSECTION) and computes `hierarchy_depth`, but the paragraph dict handed to `Chunk.from_paragraph` carries only `hierarchy_depth` → `hierarchy_level` — **the marker value itself is never surfaced**, so the chunker's regex is the sole source. `LegalChunk` has no `subsection` column; the Qdrant payload is the source of truth (preserved through the fssai re-ingest via `metadata_json`).

**Why coverage is low — three structural causes (measured on the 27,345-chunk payload index):**

1. **hl1 boilerplate is 38% of the corpus and semantically has no subsection.** 10,356 chunks sit at `hierarchy_level=1` — headers, page fragments, form labels, OCR garbage (`"Concentrated"`, `"Address:"`, `"CHAPTER 1"`, `"muiclac"`). Only 53/10,356 (0.5%) carry a value. Excluding hl1, **substantive (hl≥2) coverage is 73.8%** (12,546/16,989) — the 46.1% headline is dragged down ~2× by chunks where "subsection" is meaningless.
2. **FSSAI regulations use dotted numbering, not parenthetical chains.** 8,661 fssai chunks are `regulation` type; 1,203 of the missing ones start with a dotted clause number (`3.04`, `2.4.15`, `5.2.4`) at `hierarchy_level=3` with `section_number=None`. The engine recognises these (depth 3 — clause classification), but the parenthetical regex cannot, and the value never reaches the payload.
3. **Continuation chunks have no marker of their own.** 12,770 chunks whose immediate predecessor carries a subsection inherit nothing — but naive "inherit from predecessor" is **unsafe**: 1,623 have section mismatches, and examples show cross-clause bleed (`"or (iii) slaughtering capacity…"` inheriting `(i)` from a list; `"Schedule 2 of these Regulations…"` inheriting `(1)`). Correct inheritance needs the hierarchy — which is **entirely absent** (`parent_chunk_id` 0/27,345 in payloads, `parent_id` 0/12,819 in the DB).

**Does P2 actually need this? (honest answer: partially)** P2's de-risked filter is `same_section AND same_subsection`, so `subsection` only matters for chunks that already have a `section_number`. fssai has section numbers on only 25.1% of chunks, and of those 20.6% carry a subsection. The P2 fallback (same-section-different-chunk, no subsection required) already covers recall for the rest. **Improving coverage buys P2 precision** (fewer same-section-different-subsection false friends promoted to tier-3), not recall. It also sharpens failure-taxonomy category B (same section, wrong subsection) — today low coverage pushes those cases into the broader hierarchy confusion (A) bucket, which is the #1 bottleneck.

**Latent bug found while tracing:** `evaluation/resolution.py:188` and `evaluation/root_cause.py:205` do `norm_section(payload.get("section_number") or payload.get("subsection"))` — when `section_number` is missing, `(1)` becomes the section identity and collides across 170 sections (G5). Should be fixed regardless of coverage work.

**Improvement paths (evaluated, ordered by value/effort):**

| Path | What | Value | Effort | Verdict |
|---|---|---|---|---|
| A. **Reframe the metric** | Report substantive (hl≥2) coverage separately from hl1; don't count headers/fragments as missing | Corrects a 2× overstated gap; free | 0 | Do first |
| B. **Backfill script** (identity-preserving, like `reingest_fssai_from_db.py`) | Recompute `subsection` for all 27,345 chunks with improved rules; update Qdrant payloads + DB `metadata_json` in place | The **only** way to move the existing corpus; unblocks taxonomy-B precision | ~1 day | Do if coverage is the goal |
| C. **Extraction-rule fix** (chunker) | Extend `_extract_subsection_markers` to catch dotted clause numbers at ingestion | Future ingests only — does not touch the live corpus | 0.5 day | Only as part of B (rule first, then backfill) |
| D. **Hierarchy rebuild + inheritance** | Rebuild `parent_chunk_id` (document order + `hierarchy_level`), then propagate subsection down with strict section scoping | Largest raw headroom (12,770), but noisy; needs hierarchy that doesn't exist | 2–3 days | Defer — do only if the KG/evidence-selector work needs hierarchy anyway |
| E. **Consumer-side fixes** | Fix the `section_number or subsection` fallback; keep P2's de-risked filter | Removes a real cross-section collision bug | 0.5 day | Do regardless |

**Recommendation:** the binding constraint for P2 is **section_number coverage** (fssai 25.1%), not subsection coverage — subsection only refines chunks that already have a section. Sequence: (1) report substantive coverage (free); (2) fix the `resolution.py`/`root_cause.py` fallback (E); (3) if a sharper taxonomy or P2 precision is wanted, do B+C (backfill with a dotted-number rule for regulations) — but decide the semantics first: dotted numbers are **regulation clause numbers**, not section subsections; mapping them into `subsection` conflates two hierarchies, so keep `subsection` parenthetical-only and (optionally) add a separate dotted-clause field if evaluation needs regulation-level granularity. Path D only pays if parent links are wanted for other reasons.

### G7 — fssai section_number coverage (25.1%): root cause + improvement paths (investigated 2026-08-17)

**Root cause (three distinct problems, measured on live Qdrant + DB + payload index):**

1. **Regulations dominate the corpus and legitimately have no Act section number.** 8,661/12,819 fssai chunks (67.6%) are `regulation` type — their identity is the regulation clause number (``2.4.15``, ``3.04``), not an Act section. Even a perfect Act-stamp can never lift section coverage past ~32% (4,065 `act` + 93 `notification` chunks).
2. **The Act document is under-stamped (194/722, 27%) and the L4 text-header regex cannot fix the rest.** Live-Qdrant verification (2026-08-17) corrected the earlier DB-only reading: `instrument_id=FSS_ACT_2006` **is already stamped on all live Act-doc payloads** (stamped after the reingest; only the DB `metadata_json` cache lacks it), and `prime_registry_docids` resolves the whitelist gate fine (document UUID `60939e3b…` IS whitelisted). Re-running `backfill_payload_identity --apply` as-is produces **0 changes**: L4 already stamped 87 Act chunks (`sections_covered` present), and the remaining 485 unstamped chunks are **subsection fragments** (`(1) Every food business operator…`) that never repeat the section number, so the L4 `N. Capital` header regex cannot match them. **The fix is header-anchored section propagation (L5), not identity stamping.**
3. **Regulation chunks with dotted clauses have no section anchor at all** — `section_number=None` on all 1,036 dotted-clause chunks (their clause number is their identity, which P2's `same_section` filter cannot use).

**What this means for P2:** the 25.1% headline is a mix of a *structural floor* (regulations — not fixable by stamping) and a *fragment-propagation gap* (Act doc — fixable). The P2 binding constraint is **Act-doc section coverage** (27%), not the headline number. **DONE (2026-08-17):** the header-anchored L5 pass was added to `backfill_payload_identity.py` (never overwrite; propagates the last L4-verified section header forward within a document, filling only hl≥2 unstamped fragments; engine cross-reference noise like ``sec='30'`` from "appointed under section 30" is ignored) and applied — Act doc **27% → 94%**, overall fssai **25.1% → 28.9%** (485/722 Act fragments correctly filled, e.g. `(a) "adulterant"` → sec=2 from the ``2. Definitions`` header), with a pre-backfill snapshot for rollback. Remaining: for regulation-level granularity use the new `clause_number` field (G6 tooling) rather than forcing Act sections onto regulations — already live (1,036 points) and wired into the miner's tier-3 logic.

### Bonus findings
- **Cache invalidation:** the trainer's tokenized cache (`tokenized_cache.pt`) is keyed on the content hash of `pairwise_training_v2.jsonl` + `pairwise_train_split.json`. Baking the prefix into the JSONL at dataset-build time (Option A) invalidates it automatically; applying the prefix inside the trainer (Option B) would silently reuse stale tokenizations. Prefer Option A.
- **P4 domain tagging:** `act_name` exists on mining positives/negatives; domain can be derived via `FamilyMap` or the question's `collections`. The trainer has no weighted sampling today — needs either build-time oversampling in `pairwise_dataset.py` or a `WeightedRandomSampler` in `MarginRankingLossTrainer`.
- **Train/val question split is fixed** in `pairwise_train_split.json` — keep it untouched across P1/P2/P4 so all runs are comparable on the same 21 test queries.

### G8 — fssai coverage: honest measurement + path to complete (evaluated 2026-08-17, live Qdrant)

**What "coverage" should mean for fssai (two identities, two document classes):**

- **Act documents** (`act`): identity = `section_number`. The main FSS Act 2006 is **94%** covered (L5 fix, G7). The two amendment acts are ~41–46% covered (L4 headers stamped; propagation not yet run on them).
- **Regulation/notification documents** (67.6% of the corpus, 8,754 chunks): identity = `clause_number` (dotted regulation clause, e.g. `2.4.15`). **`section_number` is meaningless here.**

**Critical finding — regulation section stamps are noise:** 1,518 regulation chunks carry a `section_number` (17.7%), but they are **page numbers / definition-list numbers / cross-references, not Act sections**: e.g. `sec=41 | '41'`, `sec=01 | '01 -'`, `sec=36 | '6. "State Licensing Authority" means…'`, `sec=31 | 'laid down under Section 31(8) and 32(4) of the Act.'`. These came from the earlier L4 repair regex (`\d{1,4}\. [A-Z]`) matching the *first* number on a line. They pollute section-based matching (`matches_gold`, `same_section` mining) with false positives — e.g. a regulation fragment stamped `sec=36` matches any Act section 36 gold. **Recommendation: strip `section_number` from all `regulation`/`notification` chunks (keep only on `act` docs), then re-measure.**

**Honest coverage today (act-sec + reg-clause, spurious reg sections excluded):**

| Metric | Value |
|---|---|
| Act docs, section_number | 53.1% (main Act 94%, amendments ~41–46%) |
| Reg/notification docs, clause_number | 11.0% (967 chunks; 3% of Food Additives' 1,984 substantive chunks have headers) |
| **Honest identity coverage (all 12,819 chunks)** | **24.4%** |
| hl1 OCR-noise chunks (`'muiclac'`, `'rof'`, page fragments) | 4,273 (33.3%) — semantically meaningless, excluded from substantive counts |

**Remaining gap after the two cheap wins (measured):** 5,208 substantive chunks lack both identities. Document-order propagation of the *last clause header* (never overwrite, reset at `PART`/`SCHEDULE`/`CHAPTER` boundaries — same semantics as the L5 pass) recovers **2,501** of them (48%), taking honest coverage to **43.9% of all chunks / 65.9% of substantive**. Act-doc section propagation on the two amendment acts recovers ~1,250 more.

**Path to "complete" (in order of ROI):**

1. **Strip spurious `section_number` from regulation/notification chunks** (1,518 points; keep the ~38 genuine cross-reference chunks out of `section_number` — they are text, not identity). Re-freeze baseline. *Quality fix — removes false same-section matches.*
2. **L5-style clause propagation across regulation docs** (2,501 fills): extend `backfill_payload_identity` or `backfill_subsection` with a clause-anchored propagation pass (mirror L5: header-anchored, never overwrite). Act amendment docs get the existing section propagation (1,250 fills). → honest coverage ~44% overall / ~66% substantive.
3. **Parenthetical sub-clause chains** (Licensing `(2) The petty food manufacturer…` under `2.1.1 Registration of Petty Food Business`, ~240 chunks): these sit *after* a dotted clause header but are sub-clauses (`(1)…(6)`) — they carry `subsection` values (186 chunks) but no clause. Propagate the parent clause into them too, or treat `subsection` as the sub-identity (P2 filter is `same_section AND same_subsection` — for regulations the clause takes section's role).
4. **Romanized-Hindi regulations** (Nutraceuticals, 433 substantive chunks): the Gazette text is Latin-transliterated Hindi (`2- ifjHkk"kk,a%` = Definitions) — no Devanagari, no dots. The dotted regex cannot see it. Options: (a) transliterate the marker patterns (`\d+[\-.]` prefix) and extract clause numbers for the English half of the bilingual document (56 chunks are already English-stamped), or (b) accept the bilingual halves as un-identifiable and rely on the English half + retrieval. Low ROI — these are 5% of the corpus.
5. **Noise floor (4,273 hl1 fragments, 33%)**: these can never carry identity. Options: (a) filter them at ingestion (drop `hl1` short/no-alnum chunks) — improves retrieval precision and every downstream coverage %; (b) keep and ignore. The 46%→28.9% headline swings are 80% explained by this floor — **coverage should be reported on substantive chunks**, not the raw corpus.
6. **`document_title` is empty on all 12,819 payloads** — the DB `title` column is NULL for this corpus (only `source_uri` filenames like `Food_Additives_Regulations-4.pdf` exist). Backfill `document_title` from the URI filename for human-readable identity.

**What this means for P2/mining:** clause propagation makes `same_clause` (already wired into the miner's tier-3) usable at scale — today only 11% of reg chunks have a clause, so the miner's clause tier rarely fires. Stripping reg section-noise makes `same_section` honest. Neither changes benchmark gold matching (fssai golds are 69 Act sections + 10 regulation-level units with no clause references), so the CE-v2 baseline should be **re-frozen after step 1** (metrics may shift from removed false matches) and again after step 2.

---

## §1 — Revised implementation plan

Ordered by dependency; each step ends with a measurable gate. v1 stays frozen as control throughout.

### Step 0 — Rebuild the regression harness + wire the gate (done, 2026-08-17)
- Recreated `evaluation/ce_v2_eval.py` (pairwise accuracy/margin, per-query R@1/R@5/R@10/R@20/MRR/nDCG, per-domain, per-tier, per-difficulty) and `evaluation/ce_v2_error_analysis.py` (per-query table + failure taxonomy) as **committed** scripts. Fixed the cp1252 `\u0394` crash (ASCII labels) and the ZeroDivisionError (empty buckets → 0 + note).
- Both read from `pairwise_training_v2.jsonl` + `pairwise_train_split.json` (test_qids), scoring v1/v2 with a per-(query, chunk) score cache (`out/cache/ce_v2_scores_{v1,v2}_<modeltag>.jsonl`). The cache is tagged by **model identity** (path + checkpoint mtime/size), so an in-place retrain (`train_legal_ce_v2 --fresh`) re-scores automatically instead of serving stale scores.
- Both scripts accept `--model-v1` / `--model-v2` so any candidate checkpoint can be gated, not just the in-place `legal_ce_v2_K500`.
- Baseline frozen to the **committed** `evaluation/ce_v2_baseline.json` (the gate reference for every later step; re-freeze via `ce_v2_eval --freeze-baseline` after an accepted retrain).
- **Gate tooling (automated):** `evaluation/ce_v2_gate.py` runs the harness and compares vs the baseline — hard gates (R@1/R@5/R@10/R@20, MRR@10, nDCG@10, pairwise acc, epa/contract pairwise acc must not regress vs baseline v2) always fail the run; plan targets (hierarchy ≤ 4, same-section ≤ 1, failures ≤ 12) are reported and enforced with `--strict-targets`. Wired into:
  - a local pre-commit hook (`ce-v2-gate` in `.pre-commit-config.yaml`) that runs after every retrain (staleness = checkpoint/data newer than the baseline freeze) and skips fast otherwise / on fresh checkouts,
  - a CI workflow (`.github/workflows/ce-v2-regression.yml`) with a torch-free `gate-logic` job (fixture tests via `tests/test_ce_v2_gate.py`, 14 tests, also picked up by the main validation job) and a `workflow_dispatch`-only `real-gate` job that downloads the published checkpoints + data and runs the full gate on a GitHub runner.
- **Published assets (2026-08-17):** `evaluation/publish_ce_v2_assets.py` uploads the four gate-data files (~57 MB) to `sumanksaha/ce-v2-gate-assets` (manifest README with sha256 hashes); `evaluation/fetch_ce_v2_gate_assets.py` downloads models + data into the harness layout (used by the CI `real-gate` job and usable locally). The candidate checkpoint is already on the Hub (`sumanksaha/Foodmultidomain` = legal_ce_v2_K500, Hub-reload parity verified — a CI-sim gate run against the downloaded checkpoint reproduces the baseline exactly); the control checkpoint is published as `sumanksaha/legal-ce-v1` via `scripts/push_ce_models.py --repo legal-ce-v1 --local legal_ce_v1 --org sumanksaha`. ⚠️ **The current `HF_TOKEN` is read-only** (`role: read`), so the uploads could not be executed from this session — run the two commands below with a write token to finish publishing, then trigger the `real-gate` workflow:
  ```bash
  python -m evaluation.publish_ce_v2_assets
  python scripts/push_ce_models.py --repo legal-ce-v1 --local legal_ce_v1 --org sumanksaha
  ```
- **Gate:** script reproduces the logged numbers (0.5826/0.5478 acc, MRR 0.5134/0.5942, 8 hierarchy + 3 same-section failures) without crashes; `ce_v2_gate` exits 0 on the frozen baseline, 1 on a simulated MRR/pairwise regression or breached targets.

### Step 1 — P1: section-augmented training (1.5–2 days incl. serve side)
- `evaluation/pairwise_dataset.py`: propagate `positive_section` / `negative_section` (and `act_name`) from mining records into each example; add `--section-prefix` mode that writes `§<section> <text>` into positive/negative text (fallback: **no prefix** when section is missing — record a coverage stat). Option A: prefix baked into the JSONL so the trainer's cache hash invalidates automatically.
- Trainer (`evaluation/train_legal_ce_v2.py` / `ranking_loss_trainer.py`): no logic change needed (Option A); sanity-check with `--max-steps 10` that the tokenized cache rebuilds.
- Inference consistency: new shared helper (e.g. `app/rag/retrieval/section_prefix.py`) `prefix_passage(text, section_number)`; wire into `Reranker._rerank_cross_encoder`, `EnsembleReranker` CE head, `RemoteRerankClient` (both TEI and local fallback paths), and the three eval scripts. Behind `RAG_CE_SECTION_PREFIX` (default off).
- Retrain: `python -m evaluation.train_legal_ce_v2 --fresh --save-every 50`; preserve the current checkpoint as `legal_ce_v2_K500_preP1` before `--fresh`.
- **Gate:** hierarchy_version failures 8 → ≤4; R@1 ≥ 0.4286, MRR@10 ≥ 0.5942, nDCG@10 ≥ 0.6901; pairwise acc ≥ v1's 0.5826; no regression in epa/contract.

### Step 2 — P2: same-section hard negatives (2 days, parallelizable with Step 1)
- Audit `subsection` payload quality first (distinctness per section; see G5).
- `evaluation/hard_negative_miner.py`: add `--subsection-filter` (same section + different clause/chunk within it); re-mine specifically the 13 flagged questions (Q014, Q016, Q018, Q020, Q049, Q050, Q102, Q118, Q120, Q122, Q143, Q148, Q150) and any query with 0 same-section negatives.
- `evaluation/pairwise_dataset.py`: consume the re-mined file; hard-guard that train pairs never include test/val question-ids.
- Retrain on the combined dataset (with the P1 prefix if Step 1 shipped — evaluate both together).
- **Gate:** same-section hard-neg failures 3 → ≤1; R@10 stays 1.0000 (no recall loss); pairwise acc on T3 tier ≥ v1's 0.5825.

### Step 3 — P3 re-scoped: score calibration for downstream fusion (0.5 day, post-hoc, no retrain)
- New `evaluation/calibrate_ce.py`: fit temperature T on the 2,362 test pairs minimizing log-loss on min-max-normalized scores. **Verify rank-invariance explicitly** (pairwise acc before/after identical — expected by construction) and report what T actually buys (score spread, fusion-weight comparability).
- Document in the script header: P3 does **not** fix accuracy; it only normalizes score scale for `EnsembleReranker`'s CE bonus / `sweep_ce_weights.py` / any threshold.
- **Gate:** calibration report shows T > 0, rank-invariance confirmed, and a note on which downstream consumer (if any) uses absolute scores.

### Step 4 — P4: domain-balanced re-sampling (1 day)
- `evaluation/pairwise_dataset.py`: add `domain` per example (positive's `act_name` → `FamilyMap`, or the question's collection); add `--domain-balanced` (oversample non-fssai so fssai:other ≈ 1:1:1:1) or a `sample_weights` path in the trainer (`WeightedRandomSampler`).
- Retrain (on top of P1/P2 dataset if available).
- **Gate:** fssai pairwise acc ≥ 0.583 (v1) while epa/contract gains (0.5724 / 0.3942) do not regress below v2; net MRR@10 ≥ 0.5942.

### Step 5 — P5: more test data for kmc/srf/water_act (3 days, lowest ROI, optional)
- 50–100 new test pairs per underrepresented domain so kmc (40), srf (10), water_act (70) get meaningful CIs. Only worth it after Steps 1–4 are gated, and only if the tiny-sample regressions are a real concern (they may be noise).

### Step 6 — Deploy (0.5 day, only after gates pass)
- `scripts/push_ce_models.py` (Hub) + re-deploy `modal_deploy/app.py` (Modal); set `RAG_RERANKER_MODEL` / endpoint env accordingly.
- Live cross-check: `evaluation/verify_finetuned_ce.py` on the 12-question sample through the production pipeline.
- Update `agents.md` / README with the new model version.

---

## Execution order

```
Step 0 (harness) → Step 1 (P1) → Step 2 (P2, parallel mining) → Step 3 (P3, anytime) → Step 4 (P4) → [Step 5 (P5, optional)] → Step 6 (deploy)
```

Step 0 is non-negotiable first — nothing can be measured without it. Steps 1 and 2 can run in parallel (mining doesn't block dataset rebuild). Step 3 can run anytime but should be re-scoped (G3) before any code. Step 4 compares against Step 1/2 models. Step 5 only if tiny-sample regressions matter.

---

## §2 — Elaborated plan after the 2026-08-18 re-freeze

> The corpus work (noise strip + L5/L6/L7 + titles) is applied live and the baseline is re-frozen.
> This section re-anchors the plan on the **new** numbers and reframes the priority order around the
> strongest signal in the data: **V2 regressions** (queries V1 solved that V2 broke).

### 2.1 Baseline re-anchored (frozen 2026-08-18)

| Metric | V1 (control) | V2 (K500) | Delta |
|---|---|---|---|
| Pairwise acc (2,362 pairs) | 0.5826 | 0.5478 | **−3.5%** |
| Margin | +0.1533 | +0.5982 | +0.4449 (4.4×) |
| R@1 / R@5 / R@10 / R@20 | .238 / .857 / 1.0 / 1.0 | .429 / .810 / 1.0 / 1.0 | +19% / −4.8% / 0 / 0 |
| MRR@10 / nDCG@10 | .5134 / .6321 | .5942 / .6901 | +.0808 / +.0580 |
| Failure taxonomy | — | hierarchy **9** / same-section **2** / other 1 / failures 12 | gates: ≤4 / ≤1 |

vs the pre-strip baseline: same-section 3 → 2 (one false match eliminated by the noise strip, as predicted);
hierarchy 8 → 9 (Q054 reclassified into it). R@1/MRR/nDCG/pairwise unchanged — the score cache is
payload-independent, so the corpus work moved only the *classification*, not the scores.

### 2.2 Failure decomposition — the actionable signal

All 12 failures with V1/V2 ranks (from `out/cache/ce_v2_error_analysis.json`):

| Query | Domain | Difficulty | V1_rk | V2_rk | Type | Verdict |
|---|---|---|---|---|---|---|
| Q049 | epa | moderate | **1** | **10** | hierarchy | **V2 regression** |
| Q080 | wbpt | moderate | **2** | **9** | hierarchy | **V2 regression** |
| Q097 | srf | hard | **2** | **7** | hierarchy | **V2 regression** |
| Q102 | fssai | moderate | **3** | **8** | hierarchy | **V2 regression** |
| Q118 | fssai | hard | **2** | **4** | same-section | **V2 regression** |
| Q120 | fssai | hard | **2** | **4** | same-section | **V2 regression** |
| Q016 | fssai | hard | 2 | 2 | hierarchy | both fail |
| Q054 | epa | moderate | 2 | 2 | hierarchy | both fail |
| Q150 | fssai | hard | 3 | 3 | other | both fail |
| Q085 | epa | hard | 9 | 3 | hierarchy | improved, still fail |
| Q100 | contract | hard | 9 | 3 | hierarchy | improved, still fail |
| Q020 | fssai | moderate | 4 | 2 | hierarchy | improved, still fail |

**The headline problem is not "V2 doesn't go far enough" — it is "V2 broke 6 answers V1 had".**
V2's wins are real and concentrated (5 of the 9 correct queries improved: Q018 4→1, Q122 4→1, Q078 7→1,
Q131 2→1, Q148 2→1), but its losses are spread across exactly the small/medium pools: epa moderate (Q049),
wbpt (Q080, 10 pairs), srf (Q097, 10 pairs), fssai moderate/hard (Q102, Q118, Q120). This is the pairwise
−3.5% in one table.

### 2.3 Root-cause hypotheses for the regressions (diagnose before retraining)

1. **T3-overfit + fssai skew.** 6,269/14,629 pairs (43%) are tier-3 adversarial, mined predominantly from
   the fssai-heavy corpus. The model may have over-fit fssai hierarchy patterns and lost precision on other
   domains' section semantics — consistent with Q049/Q080/Q097 (non-fssai, V1-solved) regressing while
   fssai hard queries improved.
2. **Margin is not accuracy.** Margin grew 4.4× while accuracy fell 3.5% — the model separates more
   aggressively but mis-orders. The 6 regressions are the "over-confident wrong" signature (rank 4–10,
   not 2).
3. **Zero section signal in the dataset (G4).** 0/14,629 pairs carry section/clause metadata; the model
   must infer legal hierarchy from raw text. The corpus now carries honest identity (82.4% substantive,
   act docs 93.9%, clause_number 2,777), so P1's prefix is finally *feasible* — and Q118/Q120 already have
   8 same-section negatives each, so their failure is the model, not the data.

### 2.4 Steps (ROI-ordered, each with a gate)

**Step 1 — Diagnose the 6 regressions (0.5 day, no training)** — *prerequisite; gates 2/3 choices*
- For each of Q049/Q080/Q097/Q102/Q118/Q120, dump V1-vs-V2 top-10 and answer: what does V2 rank above the
  gold — a same-section false friend, a same-family different-section, or an unrelated chunk? Tooling
  exists: `evaluation/inspect_failures.py` (debug capture per query) + `ranking_failure_dataset.py`.
- **Gate:** one written root-cause per regression, each mapped to the lever that fixes it (prefix / mining /
  balance). If Q049/Q080/Q097 are same-section false friends, P2 mining is the lever; if they are
  cross-family noise, it is P4 balance.
- **DONE (2026-08-18)** — diagnosis below from the cached per-query score data (no retrain needed):

  | Query | V2 top over gold | Class | Lever |
  |---|---|---|---|
  | Q049 (epa) | s5 authority-chunk ("constituted under sub-section (3) of s3") vs gold s6 | same-family, **adjacent section (5 vs 6)** | P1 prefix (explicit §5 vs §6); P2 does *not* fire (not same-section) |
  | Q080 (wbpt) | **s45 Repeal section** ("Repeal and Act XII…") vs gold s46 possession | same-doc, diff-section, tiny domain (10 pairs) | P1 prefix + P4/P5 |
  | Q097 (srf) | **title-page hl1** ("THE SPECIFIC RELIEF ACT, 1963 ACT NO. 47…") vs gold s10 | hl1 noise, tiny domain (10 pairs) | P1 prefix (§3 vs §10) + P4/P5; also corpus-hl1-noise filter (P5) |
  | Q102 (fssai) | KMC **s394** ("refuses sanction…", Indian Kanoon URL fragment) vs gold kmc:s392 | adjacent-section within KMC; kmc has **zero same-section negatives mined** (G5) | P1 prefix + P2 kmc re-mine |
  | Q118 (fssai) | **FSS Amendment 2-2011 "(B) In section 33,"** vs gold s33 | **same-section false friend** — the L7 amendment anchors stamped sec=33 onto amendment chunks | **P2 re-mine on the cleaned corpus** (only now surfaces as same-section) + P1 |
  | Q120 (fssai) | FSS Amendment 3-2023 s33J fragment / 2-2011 "(B) In section 33," vs gold s33 | same-section false friend (L7 amendment stamps) | **P2 re-mine** + P1 |

  **Cross-cutting finding:** the L7 amendment anchors (2026-08-18 corpus work) *created* the Q118/Q120
  false friends — amendment chunks now carry the referenced principal section. Re-mining on the cleaned
  corpus converts them into genuine same-section tier-3 negatives (P2's direct target), and P1's prefix
  teaches the model to reject amendment boilerplate. Q049/Q080/Q102 are adjacent-section confusions that
  P2 mining cannot see — P1's explicit section prefix is the lever. Q097 is hl1 title-page noise on a
  10-pair domain (P4/P5 territory).

**Step 2 — P1 section-prefix (1.5–2 days)** — *the biggest lever* (unchanged scope, now data-feasible)
- `pairwise_dataset.py`: propagate `section`/`clause_number`/`act_name` from mining records into each
  example (G4 confirmed: absent today); `--section-prefix` bakes `§<section>` into the JSONL (Option A —
  cache-safe, trainer unchanged). Fallback: no prefix when identity is missing; record coverage stat.
- Serve parity (the G2 list): `app/rag/retrieval/section_prefix.py::prefix_passage(text, section_number)`;
  wire local CE + `EnsembleReranker` + `RemoteRerankClient` (TEI **and** local-fallback) + the three eval
  scripts; `RAG_CE_SECTION_PREFIX` flag, default off (zero behavior change for v1).
- Now possible because the corpus is 82.4% substantively identified (act docs 93.9% have `section_number`;
  regulations carry `clause_number`).
- **Gate:** hierarchy 9 → ≤4; R@1 ≥ 0.4286, MRR@10 ≥ 0.5942, nDCG@10 ≥ 0.6901, pairwise acc ≥ 0.5826
  (v1); no epa/contract regression.

**Step 3 — P2 same-section hard negatives (1–2 days, parallel with Step 2)**
- `hard_negative_miner.py`: add `--subsection-filter` = same_section **AND** same_subsection (the G5
  de-risked design; never subsection alone).
- Re-mine on the **cleaned** corpus (post-noise-strip): the old mining pool (2,720 negatives) was built
  against payloads carrying 1,852 noise stamps; `same_section` is now honest.
- Target: the flagged set + the two still-failing same-section queries (Q118/Q120). Note from G5: these
  already have 8 same-section negatives — Step 1 must confirm whether *more* data helps or the model needs
  the prefix.
- Guard: train pairs must never include test/val question-ids (split is by question-id).
- **Gate:** same-section 2 → ≤1; R@10 stays 1.0000; T3-tier pairwise acc ≥ v1's 0.5825.

**Step 4 — P4 domain balance (1 day)** — *the regression antidote*
- `pairwise_dataset.py`: derive `domain` per example (positive's `act_name` → `FamilyMap`, or the
  question's collections); `--domain-balanced` oversamples non-fssai toward ≈1:1 per domain (or
  `WeightedRandomSampler` in `MarginRankingLossTrainer`).
- Directly targets the 6 regressions' domains: epa, wbpt, srf, fssai moderate.
- **Gate:** the 6 regression queries' MRR@10 each ≥ their V1 value; fssai acc ≥ 0.583 (v1) while
  epa/contract gains (0.5724 / 0.3942) hold; net MRR@10 ≥ 0.5942.

**Step 5 — P3 calibration (0.5 day, anytime, re-scoped per G3)** — no accuracy claim; normalize score
scale for fusion consumers; verify rank-invariance explicitly.

**Step 6 — P5 more test data (3 days, optional)** — wbpt (10), srf (10), kmc (40), water_act (70) need
real test pairs so Q080/Q097-type regressions get measurable CIs instead of single-query noise. Only after
Steps 1–4 gate.

**Step 7 — Re-freeze + deploy (0.5 day, after an accepted retrain)**
- `ce_v2_eval` → `--freeze-baseline` (committed reference) → `ce_v2_gate` (hard gates + `--strict-targets`).
- `scripts/push_ce_models.py` (Hub) + re-deploy `modal_deploy/app.py` (Modal); set `RAG_RERANKER_MODEL`/
  endpoint env; live cross-check via `evaluation/verify_finetuned_ce.py`; update `agents.md`.

### 2.5 Sequencing

```
Step 1 (diagnose 6 regressions, 0.5d) → [Step 2 (P1 prefix, 1.5-2d) ∥ Step 3 (P2 re-mine, 1-2d)]
→ Step 4 (P4 balance, 1d) → Step 5 (P3, anytime) → [Step 6 (P5, optional)] → Step 7 (re-freeze + deploy)
```

Step 1 is non-negotiable first — it decides whether P2 mining or P4 balance is the real fix for the
regressions. Steps 2 and 3 land together in one retrain (dataset + mining are independent). Step 4 stacks
on the Step 2/3 dataset. Re-freeze only after an accepted retrain; the frozen baseline is the gate reference
for every step.

## Retraining Commands

```bash
# Step 1 (P1)
python -m evaluation.pairwise_dataset --section-prefix
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50

# Step 2 (P2, after re-mining)
python -m evaluation.hard_negative_miner --offline --subsection-filter   # new flag
python -m evaluation.pairwise_dataset --section-prefix
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50

# Step 3 (P3, post-hoc, no retraining)
python -m evaluation.calibrate_ce --model evaluation/out/models/legal_ce_v2_K500

# Step 4 (P4)
python -m evaluation.pairwise_dataset --domain-balanced
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50
```

## Evaluation Checklist (now runnable — Step 0 tooling)

- [x] Step 0: `ce_v2_eval.py` + `ce_v2_error_analysis.py` rebuilt, reproduce the logged baseline without crashes; baseline frozen to `out/cache/ce_v2_regression_baseline.json` (hierarchy 8 / same-section 3 / other 1 / failures 12)
- [x] G6 (2026-08-17): subsection-coverage root cause + improvement paths evaluated (substantive hl≥2 coverage 73.8% vs 46.1% headline; dotted-number regulations; absent parent hierarchy; `resolution.py:188`/`root_cause.py:205` fallback bug flagged)
- [x] G6 tooling shipped (2026-08-17): `clause_number` payload field + `_extract_clause_number` guard (chunker), `evaluation/subsection_audit.py` (substantive-vs-hl1 report, JSON), `scripts/backfill_subsection.py` (identity-preserving dry-run/apply), `tests/test_subsection_tooling.py` (30 tests) — dry-run: subsection +0, clause_number +1,036 (967 fssai)
- [x] Fallback bug fixed (2026-08-17): `evaluation/resolution.py:188` + `evaluation/root_cause.py:205` no longer use `subsection` as a section-number fallback (was a no-op today, latent `2.4.15`→`"2"` collision once dotted values land)
- [x] G7 (2026-08-17): fssai section_number root cause investigated — regulations = structural floor (67.6%, no Act sections by design); Act doc 18% stamped because the L3/L4 canonical-doc gate fails closed (document UUID ≠ gold registry, `instrument_id` empty) — fix = stamp `instrument_id` then re-run `backfill_payload_identity --apply`
- [x] G7 fix applied live (2026-08-17): corrected diagnosis — `instrument_id` was already on live Qdrant (only the DB cache lacked it); the real blocker was fragment chunks that never repeat the section number. Added header-anchored **L5 propagation** to `backfill_payload_identity.py` (never overwrite; propagates the last L4-verified section header within a document) — dry-run + apply: **5,789 fills, 0 overwrites** (485 fssai Act doc, 460 env, 4,111 commercial, 302 animal, 431 wb_state); Act doc **27% → 94%**, overall fssai **25.1% → 28.9%** (capped by regulations). Pre-backfill snapshot kept for rollback.
- [x] `clause_number` backfill applied live (2026-08-17): `scripts/backfill_subsection.py --apply` — **1,036 points** stamped in Qdrant (967 fssai + 68 env + 1 wb_state), DB `metadata_json` mirrored for the fssai rows; payload index cache rebuilt (27,351 points); CE-v2 baseline **re-frozen** (`evaluation/ce_v2_baseline.json`, exact reference numbers: R@1 0.4286, MRR 0.5942, nDCG 0.6901, gates 8/3)
- [x] Miner clause wiring (2026-08-17): `evaluation/hard_negative_miner.py` — `legal_similarity_score` now returns `same_clause` (dotted clause-number equality, regulation fragments with no section); `assign_tier` promotes same-family + same-clause to tier 3 (adversarial) and requires the section anchor for `same_subsection` (G5); `hard_negative_rank` adds +1.0 for `same_clause`; `tests/test_hard_negative_reranking.py` extended (45 tests pass, 8 new)
- [x] G8 (2026-08-17): fssai coverage evaluated on live Qdrant — regulation `section_number` stamps are noise (1,518 = page/def-list/xref numbers, not Act sections); honest identity coverage (act-sec + reg-clause) is **24.4%**; clause propagation recovers 2,501 more (→43.9% overall / 65.9% substantive); 4,273 hl1 OCR-noise chunks (33%) can never carry identity; `document_title` empty corpus-wide. Roadmap: strip reg section noise → clause propagation → sub-clause handling → Romanized-Hindi (low ROI) → noise filtering at ingestion
- [x] G8 step 1 tooling (2026-08-18): `scripts/strip_reg_section_noise.py` — deletes spurious `section_number` from regulation/notification/rule chunks via Qdrant null-delete (`{"section_number": None}`), DB `metadata_json` mirror, pre-strip snapshot + evidence CSV + `--verify` residue check. Default scope = regulation,notification (1,518+36); **rule (298) added to the default 2026-08-18** (same page-number/def-list/xref noise profile — `161 27960/2022/UPC-II-HO`, `6 Summary of the mechanisms…`); restrict via `--document-types`.
- [x] G8 step 2+3 tooling (2026-08-18): **L6 clause propagation** added to `scripts/backfill_subsection.py` — header-anchored propagation of the last guarded dotted clause header forward within a document (mirror of L5: never overwrite, `PART`/`SCHEDULE`/`CHAPTER`/`ANNEXURE` reset, scoped to regulation/notification/rule via `--clause-doc-types`). Dry-run: **+1,741 fills** (1,693 regulation, 26 notification, 22 rule) → clause coverage **1,036 → 2,777**; this also gives G8 step-3 parenthetical sub-clause chains (`(1) Every petty Food Business Operator…` under `2.1.1`) their parent clause.
- [x] G8 combined honest identity coverage (2026-08-18, simulated on frozen cache): **44.9% → 51.2%** of all chunks (71.6% of substantive hl≥2); reg/notification `section_number` noise → 0. Tests: `tests/test_subsection_tooling.py` 30 → **49** (clause-propagation + strip suites, 19 new).
- [x] Applied live (2026-08-18): `scripts/strip_reg_section_noise.py --apply --verify` — **1,852 fields deleted in total** (1,547 fssai + 7 env + 261 animal + 37 env-rule; 1,518 regulation + 36 notification + 298 rule), post-apply residue **0** (only act/unknown/circular keep `section_number`); `scripts/backfill_subsection.py --apply` — **1,741 clause fills** (1,693 fssai + 48 env), DB `metadata_json` mirrored for 1,693. Payload cache refreshed; live verified: clause_number **2,777 (10.2%)**, honest identity coverage **51.2%** overall / **71.6%** substantive. Also fixed a latent footgun: `--apply` now implies a live scroll (the old path silently applied 0 from the frozen cache) + a fail-fast guard when 0 updates are written
- [x] **Re-frozen the CE-v2 baseline (2026-08-18)** — `ce_v2_error_analysis` → `ce_v2_eval` → `--freeze-baseline` on the post-strip/post-P1/P2 payload cache. Taxonomy shift as predicted: same_section_hard_neg 3→2 (one false same-section match eliminated by the noise strip), hierarchy_version 8→9, failures 12; pairwise/ranking metrics unchanged (score cache is payload-independent). Gates now: hierarchy 9 / same-section 2 (targets ≤4 / ≤1). P3 re-ingestion (BNS + rule docs ≈2,129) is **deferred (user, later)** — noted in `plan.md` §11; re-freeze again after P3 lands.
- [x] Corpus-wide coverage evaluated (2026-08-18): `evaluation/coverage_audit.py` (committed, tests `test_coverage_audit.py` 14) — **51.3% of all chunks / 71.6% of substantive (hl≥2)** identified; gap = 4,824 substantive chunks, **83% (4,025) paren_fragments** fillable by propagation. Root causes per doc class: FSS amendments have **zero extractable headers** (identity = referenced section via cross-refs), LLP/SR headers exist but are mis-stamped by in-text cross-ref precedence, BNS is space-stripped OCR, SWM/PCA rule headings merged into tables, Nutraceuticals is Romanized-Hindi, Food Additives' substantive content is 99% complete (its gap is a 4,618-chunk reversed-text/table hl1 floor). `document_title` missing on 12,820 (12,819 recoverable from `document_uri`). **Plan:** `docs/COVERAGE_COMPLETENESS.md` — P1 title backfill (trivial) → P2 L7 propagation (header-trust + amendment anchors, ≈1,917 fills → ~83% substantive, no re-ingestion) → P3 re-extract broken-OCR docs (BNS + rule docs, ≈2,129) → P4 transliteration (low ROI) → P5 noise filter
- [x] **P1 + P2 implemented and applied live (2026-08-18):** `scripts/backfill_document_title.py` — 12,819 titles (29 docs, 27,350/27,351 covered), DB mirrored; `derive_l7` in `scripts/backfill_payload_identity.py` (header-trust corrections + amendment anchors, `--no-l7` opt-out, repair CSV) — 2,075 updates (L7 correction 42 + L7 propagation 1,834 + L5 180 + L4 19), section 11,243 → 13,318. **Fixed en route:** L1/L2/L3 would have re-stamped the stripped reg/rule/notification noise (1,624 chunks carry `provision_id` built from old page-number stamps) — `derive_section` now gated to act-type chunks. **Post-apply: 58.0% of all / 82.4% of substantive (hl≥2) identified**; commercial 99.9%, fssai 86.9%, act docs 93.9%. Convergence: second apply fixed 31 L4-vs-L7 disagreements (space-form header dropped, L4 loop runs last, L7 skips L4-verified chunks) — **re-run now idempotent (0 changes)**. Tests: `test_backfill_l7.py` (37, incl. `derive_title`/`derive_changes`) — 160 affected pass, ruff clean. Remaining: P3 re-ingestion (BNS + rule docs ≈2,129) → P4 transliteration → re-freeze CE-v2 baseline (`evaluation/ce_v2_baseline.json` — section stamps changed, `matches_gold`-sensitive)
- [x] §2 elaborated (2026-08-18): post-re-freeze plan — failure decomposition shows **6 of 12 failures are V2 regressions** (Q049 1→10, Q080 2→9, Q097 2→7, Q102 3→8, Q118/Q120 2→4; V1 solved them, V2 broke them); root-cause hypotheses (T3/fssai overfit, margin≠accuracy, zero section signal per G4); steps re-sequenced: Step 1 diagnose the 6 regressions → Step 2 P1 prefix (now data-feasible: 82.4% substantive identity) ∥ Step 3 P2 re-mine on the cleaned corpus → Step 4 P4 balance (the regression antidote) → Step 7 re-freeze + deploy. Gates per step in §2.4
- [x] **Step 1 done (2026-08-18)** — the 6 regressions diagnosed from cached scores: Q049 = adjacent-section (s5 authority chunk vs gold s6), Q080 = s45 Repeal vs gold s46 (tiny 10-pair domain), Q097 = title-page hl1 vs gold s10, Q102 = KMC s394 vs gold kmc:s392 (kmc has zero same-section negatives mined), **Q118/Q120 = same-section false friends created by the L7 amendment anchors** (amendment chunks now stamped sec=33). Lever mapping: P1 prefix for the adjacent-section cases, **P2 re-mine on the cleaned corpus for Q118/Q120** (only now surfaces as same-section), P4/P5 for the tiny domains. Full table in §2.4 Step 1.
- [x] **Step 2 P1 implemented (2026-08-18):** `app/rag/retrieval/section_prefix.py` (new — `prefix_passage(text, section, clause, force)`, `§`-marker, `RAG_CE_SECTION_PREFIX` default off, idempotent, section-wins-over-clause, no-identity fallback); `RetrievedChunk` + dense/sparse retrievers now carry `clause_number` (serve parity for regulations); `Reranker._rerank_cross_encoder` + `EnsembleReranker` CE head prefix pairs at build time (covers local CE + remote client + its local fallback — the served model only sees prefixed text); `evaluation/ce_rerank_eval.py` `rank_with_ce` prefixed; `evaluation/pairwise_dataset.py` joins the **authoritative payload index** (stale pre-strip mining sections overridden) and propagates `positive/negative_{section,clause,act}`; `--section-prefix` bakes via `force=True` (train/serve toggles independent). Verified on real data: prefix on **88.6% of 14,629 examples** (§16-style + §2.4.15-style clause prefixes). `verify_finetuned_ce.py` covered via EnsembleReranker. Tests: `tests/test_section_prefix.py` (23 new).
- [x] **Step 3 P2 implemented (2026-08-18):** `--subsection-filter` in `evaluation/hard_negative_miner.py` — same_section AND same_subsection (G5 de-risked design, never subsection alone), threaded through `mine_question` + `mine_live` + `mine_offline` + `main`; falls back to same-section-only when no AND-match exists (fssai 33% subsection coverage). Tests: `TestSubsectionFilter` (3 new).
- [x] **Step 4 P4 implemented (2026-08-18):** `--domain-balanced` (+ `--domain-balance-cap`, default 3.0) in `evaluation/pairwise_dataset.py` — oversamples underrepresented domains to `min(largest, cap × own)`; verified on real data: **27,207 examples (1.9×)**, fssai 57% → 30.7%, srf 10→30, cpa 16→48; `--domain-balance-cap None` = uncapped equalize (~10×, GPU-only). Stats JSON reports domain distribution + prefix/identity coverage.
- [ ] Run Steps 2+3 together: `python -m evaluation.hard_negative_miner --offline --subsection-filter` (re-mine on the cleaned corpus) → `python -m evaluation.pairwise_dataset --section-prefix --domain-balanced` → retrain `python -m evaluation.train_legal_ce_v2 --fresh` (preserve current checkpoint as `legal_ce_v2_K500_preP1` first) → gate via `ce_v2_eval` + `ce_v2_error_analysis` + `--freeze-baseline`
- [ ] Re-run `python -m evaluation.ce_v2_eval` after each improvement (score cache makes it fast)
- [ ] Verify R@1, MRR@10, nDCG@10 on the 21 test queries (gates in each step above)
- [ ] Verify pairwise accuracy on all 2,362 test pairs
- [ ] Check hierarchy-version failures specifically (8 → target: 4 or fewer)
- [ ] Check same-section hard negatives (3 → target: 1 or fewer)
- [ ] Verify no regression in epa/contract domains
- [ ] Verify train/serve prefix parity (same `§<section>` format + fallback in local, remote, and eval paths)
- [ ] Update `AGENTS.md` with new model version when deployed

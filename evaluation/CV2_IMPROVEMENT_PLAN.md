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
- `same_subsection` is unreliable: Q049 has 7 same-subsection negatives but 0 same-section — the `subsection` payload value repeats across different sections, so a "subsection-level filter" needs validation (require same-section AND same-subsection, and audit distinctness of `subsection` per section) before mining.
- Guard needed: re-mined pairs must not leak test/val question-ids into train (split is by question-id).

### Bonus findings
- **Cache invalidation:** the trainer's tokenized cache (`tokenized_cache.pt`) is keyed on the content hash of `pairwise_training_v2.jsonl` + `pairwise_train_split.json`. Baking the prefix into the JSONL at dataset-build time (Option A) invalidates it automatically; applying the prefix inside the trainer (Option B) would silently reuse stale tokenizations. Prefer Option A.
- **P4 domain tagging:** `act_name` exists on mining positives/negatives; domain can be derived via `FamilyMap` or the question's `collections`. The trainer has no weighted sampling today — needs either build-time oversampling in `pairwise_dataset.py` or a `WeightedRandomSampler` in `MarginRankingLossTrainer`.
- **Train/val question split is fixed** in `pairwise_train_split.json` — keep it untouched across P1/P2/P4 so all runs are comparable on the same 21 test queries.

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
  - a CI workflow (`.github/workflows/ce-v2-regression.yml`) that exercises the gate logic torch-free via `tests/test_ce_v2_gate.py` (14 tests, also picked up by the main validation job) — the real gate runs on the machine that owns the checkpoints.
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
- [ ] Re-run `python -m evaluation.ce_v2_eval` after each improvement (score cache makes it fast)
- [ ] Verify R@1, MRR@10, nDCG@10 on the 21 test queries (gates in each step above)
- [ ] Verify pairwise accuracy on all 2,362 test pairs
- [ ] Check hierarchy-version failures specifically (8 → target: 4 or fewer)
- [ ] Check same-section hard negatives (3 → target: 1 or fewer)
- [ ] Verify no regression in epa/contract domains
- [ ] Verify train/serve prefix parity (same `§<section>` format + fallback in local, remote, and eval paths)
- [ ] Update `AGENTS.md` with new model version when deployed

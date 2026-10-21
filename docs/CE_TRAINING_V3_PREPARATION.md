# CE Training v3 — Preparation & Evaluation Plan

**Status:** preparation (pre-run evaluation). **Owner:** RAG/CE training line. **Repo:** `NSA_webservice`.
**Source of truth:** `evaluation/train_legal_ce_v3.py`, `evaluation/ranking_loss_trainer.py`,
`tests/test_train_legal_ce_v3.py`, `evaluation/README.md`, and the frozen artifacts under
`evaluation/out/cache/` + `evaluation/out/ceiling_v5/` (all gitignored; read via terminal).

This document answers the five questions for the **next set of `legal_ce_v3` cross-encoder training**:
1. Data — what is being trained on and where it came from
2. Scope — what v3 covers and what it deliberately does not
3. Improvement aimed — what v3 is expected to fix over v2
4. Parameter measured — the metrics and artifacts that decide success
## 1. Data — the training substrate

All inputs are **frozen** (no live Qdrant / Neo4j / LLM). `legal_ce_v1` is the frozen control and is never
retrained; `legal_ce_v2_K500` is the baseline and is left untouched by v3.

### 1.1 Pairwise pool (ranking substrate)

| Artifact | Path | Content |
|---|---|---|
| Pairwise records | `evaluation/out/cache/pairwise_training_v2.jsonl` | 27,207 records (each = query + positive + negative) |
| Pool stats | `evaluation/out/cache/pairwise_training_v2_stats.json` | tier distribution, splits, domain distribution |
| Train/val/test split | `evaluation/out/cache/pairwise_train_split.json` | qid-level split — all pairs of one question stay in one split |

**Tier distribution (frozen pool):**
- Tier 1 random: 2,239 pairs
- Tier 2 semantic hard: 13,301 pairs
- Tier 3 adversarial legal: 11,667 pairs
- Unique questions: 136; train 95 qids / 18,756 pairs; val 20 qids / 4,228 pairs; test 21 qids / 4,223 pairs

**Domain distribution** (pairs): fssai 8,340 · kmc 5,964 · epa 5,496 · water_act 2,262 · contract 1,038 · comp 816 · air_act 708 · wbmo 768 · wbpt 243 · bda 144 · sog 192 · limitation 384 · pcra 240 · partnership 150 · cpa 48 · srf 30. Positive section coverage 0.756 / prefix coverage 0.772.

**Split discipline (enforced):** pairs are never re-split by pair — that would leak question text across train/val. Validation pairs are **not** pooled, so `val_loss` stays on the full frozen val split and remains comparable with v2.

### 1.2 v3 question pool (`--use-pool`)

Artifact: `evaluation/out/cache/train_pool_v1.json` (version `train-pool-1`, dated 2026-10-05).
Built from `ceiling_v5/failure_attribution.json` (C_hybrid arm) + `full_review_tabulation.json` (150/150 human verdicts).

| Bucket | Count | Weight | Effect in training |
|---|---|---|---|
| `priority_provision` | 13 qids | 3.0 | oversampled 3× — provision_check wrong-provision selection |
| `priority_promotable` | 13 qids | 2.0 | oversampled 2× — promotion-candidate qids |
| `guard` | 61 qids | 1.0 | kept once; regression guard |
| `untrainable` | 63 qids | 0.0 | **dropped** — generation / never_retrieved errors ranking training cannot address |

Verdict counts: `evaluator_miss: 57`, `model_wrong: 89`, `reference_narrow: 4`.
`eval_gate`: `promotion` (26 qids — after retrain `gold_in_prompt` must flip True) and `guard_check` (61 qids — no human-correct → wrong regression).

**Rationale (from the pool file):** trainable error = `retrieval_rank` (gold in pool, ranked below the prompt window), dominated by provision wrong-provision selection (13 qids). `generation` / `never_retrieved` (63 qids) are NOT addressable by ranking training and must not be booked against it.

Qids absent from the pool are treated as guard (kept once) — a pool covering a subset of qids never silently deletes data.

### 1.3 Failure attribution context

`ceiling_v5/failure_attribution.json` classifies each qid as `retrieval_rank` / `generation` / `never_retrieved`.
Only `retrieval_rank` qids enter the trainable pool. See `evaluation/README.md` §"Question pool (ceV3)" for the full bucket→qid mapping.

5. How it will be run — commands, flags, resume/monitoring, promotion gates

---
## 2. Scope of the training (v3 profile)

`legal_ce_v3_K500` is a **deliberately lighter fine-tune than v2** (enforced by `tests/test_train_legal_ce_v3.py`):

| Parameter | v3 value | v2 (baseline, for contrast) |
|---|---|---|
| Epochs | 2 | 3 |
| Batch size | ≤ 8 (actual 8) | 24 |
| Max length | ≤ 192 (actual 192) | 256 |
| Learning rate | ≤ 1e-5 (actual 1e-5) | 2e-5 |
| Loss | Margin ranking, margin 1.0 | Margin ranking, margin 1.0 |
| Curriculum | disabled (`False`) | progressive T1→T2→T3 |
| Base model | `cross-encoder/ms-marco-MiniLM-L-6-v2` | same |
| Pooling | `--use-pool` applies train_pool_v1 weights | none (whole pool, uniform) |
| T3 mass cap | T3 ≤ 2.0 × (T1 + T2), deterministic keep | cap 2.0 (gate script) |
| Output dir | `evaluation/out/models/legal_ce_v3_K500/` | `legal_ce_v2_K500/` |

**v3-specific mechanics (source: `train_legal_ce_v3.py`):**
1. Load all 27,207 pairs; keep only pairs whose `question_id` is in the **train** split (val/test held out).
2. If `--use-pool`: bucket each pair by qid; drop weight-0 (`untrainable`) pairs; repeat weight-w pairs `max(1, round(w × scale))` times (3× / 2× / 1×). Qids absent from the pool are kept once as guard.
3. Apply the **T3 mass cap**: if tier-3 count > 2.0 × (tier-1 + tier-2), truncate tier-3 to `2.0 × (T1+T2)`, keeping the deterministic first N after sorting by `(neg_chunk_id, query)` — reproducible across resumes.
4. Train with `MarginRankingLossTrainer` (crash-safe `train_state.pt`, resume-capable) for 2 epochs, `val_cap=480`, `val_batch_size=64`.
5. Write `ce_train_summary_v3_k500.json` **only when a run completes** (carries `best_val_loss` / `global_steps_completed`); `training_status.json` with null losses is a resumability artifact, not evidence of a completed run.

**What v3 does NOT scope in:**
- No generation-side fixes (untrainable bucket dropped by design).
- No widening of narrow references (that is the `v4`/reference-widening track, `ceiling_v5/widen_v4/`).
- No changes to `legal_ce_v1` (frozen control) or `legal_ce_v2_K500` (frozen baseline).
- No curriculum (v3 is a single-phase margin run).

Existing artifact check: `evaluation/out/models/ce_train_summary_v3_k500.json` currently holds a **calibration/smoke run** (`max_steps: 1`, `global_steps_completed: 10`, `best_val_loss: 1.3761`, 30.7 s, CPU, 2 threads, 8.3 GB RAM, `n_train: 23,917` after pool oversampling). The next set = the **full uncapped run**.
## 3. Improvement aimed

v3 targets the **retrieval_rank** cohort only — questions whose gold provision is in the candidate pool but ranked below the answer-generation prompt window, dominated by **provision_check wrong-provision selection** (the 13 `priority_provision` qids).

| Aim | Mechanism | Target qids |
|---|---|---|
| Promote gold provisions into the prompt window | Pool-weighted oversampling: priority_provision 3×, priority_promotable 2× | 26 qids (`eval_gate.promotion`) |
| Prevent the adversarial tier (11,667 pairs) from eating the 2-epoch budget | T3 mass cap at 2.0× T1+T2 | all tier-3 |
| Avoid booking generation/never_retrieved errors against the ranker | Drop `untrainable` bucket (63 qids, weight 0.0) | 63 qids |
| No regression on already-human-correct questions | Guard bucket kept once; `guard_check` rule | 61 qids (`eval_gate.guard_check`) |
| Keep v2 as an apples-to-apples frozen control | val split never pooled; `best_val_loss` comparable with v2 | 20 val qids |

**Success gate (preregistered):** after retrain, `gold_in_prompt` must flip `True` for the 26 promotion qids, and no guard qid may flip human-correct → wrong. These mirror the CE gate's v1-solved regression guards. Related gate targets from `train_legal_ce_gate.py`/`mine_gate_hard_negs.py`: hierarchy_version failures ≤ 4, same_section_hard_neg ≤ 1, total ≤ 12 (v4_gate track; informative for v3 targeting).

## 4. Parameter measured

**Primary model-selection metric:** `best_val_loss` (margin ranking loss on the frozen val split). Lower is better; it is the value recorded in `train_state.pt` and `ce_train_summary_v3_k500.json`.

**Measured / recorded parameters (from `ce_train_summary_v3_k500.json` schema):**
- `epochs`, `batch_size`, `lr`, `max_len`, `margin`, `t3_cap_ratio` (hyperparams; asserted against v3 caps in tests)
- `best_val_loss` (+ per-epoch `val_loss` history and `train_loss` history)
- `global_steps_completed` vs `total_steps_target`
- `n_train`, `n_val` (post-pool, post-cap pair counts)
- `pool_info` (in/out/dropped pairs, `bucket_pairs`, `scale`, weights applied) and `t3_info` (t3_before/after, capped flag)
- `elapsed_seconds`, `hardware` (device, threads, ram_gb), `checkpoint_path`, `resumed` flag

**Run monitoring (pollable, 0-LLM):**
- `training_status.json` written every 5 optimizer steps: `global_step`/`total_steps`, `percent`, `epoch`/`epochs`, `phase`, `train_loss`, `val_loss`, `best_val_loss`, `peak_rss_mb`, `eta_seconds`
- CLI: `python -m evaluation.train_legal_ce_v3 --status` (one-shot) or `--watch` (polls every 30 s until `done`/`interrupted`)

**Test-enforced invariants** (`tests/test_train_legal_ce_v3.py`, offline, no torch):
- `V3_EPOCHS == 2`, `V3_BATCH_SIZE <= 8`, `V3_MAX_LEN <= 192`, `V3_LR <= 1e-5`
- T3 cap arithmetic (cap at ratio × (T1+T2); no-op when within ratio; deterministic across runs)
- Pool behavior: drops untrainable, oversamples priority buckets by weight, `scale` multiplies weights, scale 0 drops all, unlisted qids kept once, deterministic and order-preserving, empty pool = no-op

**Comparison baseline:** `ce_train_summary_v2_k500.json` (v2: `best_val_loss 0.7181`, 1,292 steps, ~8.6 h, 4 threads, `n_train 18,756` unpooled). v3's lighter profile trades raw val-loss for targeted, cheap, pool-weighted fine-tuning on the trainable cohort.

## 5. How it will be run

All commands run from the repo root `C:/github/NSA_webservice` with the venv activated. PowerShell note: use `;` instead of `&&`/`||`.

### 5.1 Entry points

```powershell
# Recommended entry point (per evaluation/README.md): full v3 run with the question pool
python -m evaluation.train_legal_ce_v3 --use-pool --pool-scale 1.0

# Calibration / smoke: stop after N optimizer steps (validates plumbing end-to-end, cheap)
python -m evaluation.train_legal_ce_v3 --max-steps 10

# Full run, resume from existing train_state.pt (default)
python -m evaluation.train_legal_ce_v3 --use-pool

# Ignore any existing checkpoint and start from scratch
python -m evaluation.train_legal_ce_v3 --fresh --use-pool

# One-shot status poll
python -m evaluation.train_legal_ce_v3 --status

# Watch until done (30 s poll interval)
python -m evaluation.train_legal_ce_v3 --watch
```

### 5.2 Flags

| Flag | Default | Meaning |
|---|---|---|
| `--use-pool` | off | Apply `train_pool_v1.json` weights (recommended) |
| `--pool-scale` | 1.0 | Multiplier on pool weights (e.g. 2.0 doubles oversampling) |
| `--max-steps` | null | Calibration stop after N optimizer steps |
| `--max-epochs` | null | Cap at N epochs (overrides V3_EPOCHS=2) |
| `--batch-size` | 8 | Per-step batch size (≤ 8) |
| `--device` | auto (CPU) | Override device auto-detection |
| `--threads` | 2 | Torch intra-op thread cap |
| `--save-every` | 50 | Checkpoint save interval (optimizer steps) |
| `--t3-ratio` | 2.0 | T3 mass cap ratio vs T1+T2 |
| `--no-t3-cap` | off | Disable T3 cap (full pool as-is) |
| `--fresh` | off | Ignore `train_state.pt`, restart |
| `--status` / `--watch` | off | Read/poll `training_status.json` |

### 5.3 Resume & crash-safety

Training is resumable through `evaluation.ranking_loss_trainer.MarginRankingLossTrainer`:
`train_state.pt` (model + AdamW + RNG + epoch shuffle) is saved every `--save-every` steps (default 50).
A `--fresh` run discards it. Only `ce_train_summary_v3_k500.json` (written on completion) counts as evidence of a finished run — a `training_status.json` with null losses is **not** completion evidence.

### 5.4 Promotion / release

If the v3 run passes the preregistered gate (26 promotion qids flip `gold_in_prompt=True`; no guard regression), the checkpoint can be served/published:

```powershell
# Dry run first (no upload)
python scripts/push_ce_models.py --dry-run

# Push v2 baseline (v3 not yet registered in MODEL_KEYS — add "legal_ce_v3_K500": "legal-ce-v3-k500" before pushing v3)
$env:HF_TOKEN="hf_xxx"; python scripts/push_ce_models.py --repo legal_ce_v2_k500 --local legal_ce_v2_K500
```

`push_ce_models.py` runs a parity check (sanity pairs scored before/after upload, tol 0.05) and never uploads training artifacts (`tokenized_cache.pt`, `train_state.pt`).

### 5.5 Pre-flight checklist (before the full run)

- [ ] `tests/test_train_legal_ce_v3.py` passes (v3 profile caps + pool logic)
- [ ] `train_pool_v1.json`, `pairwise_training_v2.jsonl`, `pairwise_train_split.json` present in `evaluation/out/cache/`
- [ ] Decide `--pool-scale` (start 1.0) and whether `--no-t3-cap` is needed (default: cap ON)
- [ ] Confirm hardware budget: v3 = 2 threads, CPU, ~8 GB RAM, ~30 s per calibration step (full run far cheaper than v2's ~8.6 h)
- [ ] Record v2 baseline metrics from `ce_train_summary_v2_k500.json` for comparison
- [ ] After run: diff `ce_train_summary_v3_k500.json` vs v2 baseline; verify gate (promotion flips, no guard regression)

## 6. References

- `evaluation/README.md` — data flow, pool schema, model outputs, status polling
- `evaluation/train_legal_ce_v3.py` — v3 trainer + profile constants
- `evaluation/ranking_loss_trainer.py` — shared crash-safe trainer (BASE_MODEL, MARGIN, splits, pair loading)
- `evaluation/train_legal_ce_gate.py` / `evaluation/mine_gate_hard_negs.py` — v4_gate targeted training (hierarchy + same-section negatives; gate targets ≤ 12 total failures)
- `tests/test_train_legal_ce_v3.py` — offline invariants for the v3 profile
- `ceiling_v5/failure_attribution.json`, `full_review_tabulation.json` — source of the train pool
- `docs/INDEX.md` — repo doc index (add this file there when the plan is ratified)


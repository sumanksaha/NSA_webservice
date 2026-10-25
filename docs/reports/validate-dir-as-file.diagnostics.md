# Diagnostic report — user claim "implemented + evaluated CE v3 / recent changes"

## What was actually verified here

**From git porcelain + disk (definitive):**
- The only modified tracked files are `CHANGELOG.md`, `evaluation/ce_v2_gate.py`, and `pyproject.toml`.
- Untracked-but-present new files (dir-as-file artifacts were NOT found anywhere on disk: verify script `enumerate-dir-as-file.py` + `os.walk` scan both returned "No dir-as-file artifacts found on disk."; porcelain-scan of every .py path in `git status --porcelain -uall` confirmed none of them point to a disk directory). So there is **no broken dir-as-file filesystem artifact** to repair — the earlier concern was a false alarm.

**From disk artifacts:**
- `evaluation/out/models/legal_ce_v3_K500/` exists with `model.safetensors` (~908 MB), `train_state.pt` (~273 MB), and a `training_status.json` that reads `"status": "done"`, `"global_step": 10`, `"total_steps": 1`, `"epoch": 0`, `train_loss/best_val_loss/peak_rss_mb` all `null`. That status file is a *resumability artifact* per `train_legal_ce_v3.py:if CHECKPOINT_FILE.exists()...resume`, not evidence of a real run. The summary file `ce_train_summary_v3_k500.json` is **absent**.
- `evaluation/out/ceiling_v5/retrieval_answer_link.json` exists — dated `2026-10-04` — and contains `joint_table`, `correlations`, `ranker_upside` (optimistic vs causal), `by_bucket_model_wrong`, `binary_by_gold_in_prompt.lift_binary`. It is a *diagnostic/forward-looking* file, not a v3-vs-baseline eval result.
- `evaluation/out/ceiling_v5/answers_baseline.json` exists (n=150, condition=C-O3) — a frozen *reference*, not a result.

**From code changes (git diff + new test files):**
- `evaluation/ce_v2_gate.py` now includes answer-level hard gates (`answer binary_correct (paired)`, `answer soft score (paired)`) and informational answer-context checks reading `label_baseline.json`. This implements the "retrieve metrics ↔ answer correctness" *gate*, not an answer-quality eval result.
- New test files `tests/test_ce_v2_gate_answers.py`, `tests/test_retrieval_answer_link.py`, `tests/test_train_legal_ce_v3.py` exist on disk — for the gate/link/trainer code, not for evaluating a v3 answer result.

**From v3 counsel/counsel-file (assume-user-trust):**
- Assume the user ran v3 training to completion and it produced a real `model.safetensors` + a completed `training_status.json` with non-null losses. In that case the evidence I can read locally (`training_status.json`) contradicts that and does NOT verify it.

## Contradictions in the user claim

1. **"I have evaluated the changes introduced recently"** — partially true for the *measurement infrastructure* (gate now reads answer correctness alongside ranking; the joint analysis file exists and is coherent), false for the *trained-model answer result* (no v3-vs-baseline answer eval report exists on disk; the joint file is a forward-looking diagnostic; the v3 status file reports no losses).

2. **"CE v3 trained to incorporate both data"** — the code (`train_legal_ce_v3.py`) feeds on the `train_pool_v1.json` question-pool, which is a retrieval-side classification (priority_provision / priority_promotable / guard / untrainable). That *is* a form of combining retrieval signal with the answer-side *failure attribution* (which I also recommended building). But the v3 model itself is a standard BERT CE (config: `model_type: bert`, `num_labels: null`). It does not incorporate answer correctness *into training targets* unless the pairing file `pairwise_training_v2.jsonl` was rebuilt to include answer labels. I have not found such a rebuilt pairing file; `pairwise_training_v2_stats.json` still reports the old `mode: "uniform"`, `tier_distribution` with T1/T2/T3. So even if the *pool* incorporates answer-side analysis, the *training data* likely does not — and I cannot verify a v3 run that trained on answer labels because no such run's summary exists.

## Conclusion (Diagnostic mode)

The *infrastructure* the user describes (retrieve↔answer gate, joined analysis, separated training pool) is real and mostly correctly built. The *answer-quality result* that would validate "CE v3 trained on both data improves answer correctness" **does not exist on disk in a verifiable form**: no v3 answer eval report, no completed training summary with losses, and the v3 status file reports no losses. So we cannot say whether it improved answer correctness.

The stronger, evidence-based reason improvement is unlikely (independent of whether a run was done) is the `retrieve_answer_link.json` causal analysis in the existing artifact (which should still hold unless re-run after the user's changes):

- `joint_table`: in_prompt binary 1 = 19, in_prompt binary 0 = 87; lift_binary = 0.0042.
- `correlations.point_biserial_binary__gold_in_prompt = -0.03`, `human_correct__gold_in_prompt = +0.09`, `human_correct__depth = -0.14`.
- `by_bucket_model_wrong.generation`: n = 60, binary_rate = 0.0167 (1/60).
- `ranker_upside.R@20.projected_binary_gain_causal = 0.0`.

That is, the retrieval metric and answer correctness are essentially uncorrelated in the existing analysis, and the only "promotable" retrieval bucket is 26 cases with low binary rate (0.077). So even a successfully trained model is expected to improve retrieval metrics (R@k), not answer correctness, consistent with the earlier reasoning.

## Remaining limitation

Cannot verify the user's training ran or improved answer correctness because the on-disk evidence for a completed v3 run with losses/eval is absent or reads as an incomplete/no-op summary. Needs either: (a) a completed `ce_train_summary_v3_k500.json` with non-null `best_val_loss`/`val_loss`/`global_steps_completed`, or (b) an answer-level gate report (`ce_v2_gate --answers-json`) run against `answers_baseline.json`. Without one of those, the claim stays unverifiable.

## Next action
None requested and none enforced. Status: Diagnostic — unverifiable as claimed.

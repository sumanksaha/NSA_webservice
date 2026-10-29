# Enlarging the evaluation set before joint model training (Step 3)

## The blocker (re-confirmed on disk)

Answer-level claims in this repo are measured on `benchmark_v1.0.jsonl` = **150 questions**, of which only **21** form the pairwise test split (train 95 / val 20 / test 21). Every answer-level experiment (all A/Bs, the window-A/B causal estimate, `retrieval_answer_link` conversions) runs on the full 150 — including the 95 train questions that feed training. So answer-level metrics leak into training and cannot support causal claims.

The minimum detectable effect (MDE) at n=150 is too large for the 1–3pp improvement targets. Under the standard paired-binary assumption (McNemar design, two-sided α=0.05, 80% power), the smallest net-gain δ detectable given a discordant (flip) fraction f:

    δ_min = 2.802 × sqrt(f / n)

| n  | f=5% flip | f=10% flip | f=20% flip |
|----|-----------|------------|------------|
| 150 | 5.1pp | 7.2pp | 10.2pp |
| 500 | 3.5pp | 5.0pp | 7.1pp |
| 1000 | 2.5pp | 3.5pp | 5.0pp |
| 2000 | 1.8pp | 2.5pp | 3.5pp |
| 5000 | 1.1pp | 1.6pp | 2.2pp |

Even under optimistic assumptions, n=150 cannot distinguish 1–3pp effects — consistent with the empirical finding in window A/B (|t|≤1.42, 0/7 flips). **Conclusion: no answer-level claim of ≤3pp is supported by the current 150-set; a held-out set of ≥1000 (ideally 2000–5000) answered, gold-labelled questions is required.**

Two things help *within* n=150:
1. **Pairwise eval has real power.** 4,223 test pairs (21 qids) → MDE on *ranking accuracy* ≈ 0.7pp at f=10%. This is enough to gate ranker retrains **but only on ranking**, never on answer correctness.
2. **The pair-count (not question-count) drives pairwise MDE.** Adding questions adds pairs quadratically; the existing harness already uses ~4.2k pairs.

## How to enlarge: evaluated options

### Option A — corpus-driven deterministic question templates (recommended for speed, lowest LLM cost)
The corpus contains 1,861 provisions plus a gold registry of 99 provisions with titles/headings. For each provision section we can generate a template question ("Under Section X, what [action] is [permitted/prohibited] to [actor]?", "What is the maximum [limit] for [category]?") with the section text + provision title as the *mechanical reference*. Pros: machine-generated reference, no LLM quota, n≈800+ feasible in one pass. Cons: question quality and relevance vary; some templates produce degenerate questions (missing answerable fact). **Requires human spot-review of a stratified sample (e.g., 80 questions across 3-4 template classes) before using as gold; accept the reviewed subset as gold.**

### Option B — LLM-generated questions with rubric + human spot-review (higher quality, quota-costly)
Use the gold registry + corpus sections as prompts for question generation with a strict template (must be answerable from ≤2 cited provisions, must state which operator/limit it tests). Budget ~2–3h LLM spend per 200 questions + human spot-review of ~15% (n≈30). Pros: better question quality, rubric coverage. Cons: quota + human-hours; still needs spot-review.

### Option C — reuse `benchmark_food_intent_v1.1.jsonl` (26 questions) + the AutoSearch harness
26 food-intent questions exist but use a different metric surface (different answer type). AutoSearch (`docs/auto_search_report.json`) is corpus-fill focused, not question-generation; repurposing it is non-trivial. Low priority.

### Option D — keep answering at n=150 but raise the bar on claims
If no expansion happens: gate answer claims at ≥5pp only (MDE 5.1pp at n=150, f=5%), publish confidence intervals on every answer A/B, and never claim ≤3pp. This is the "safe but slow" path and contradicts the joint-training motivation.

## Recommended path

1. **Run the gate with `--answers-json` after every future training run** (new hard gates in `ce_v2_gate.py`) — this makes ranking improvements falsifiable against answer quality *now*, with n=150 context, before any new questions exist. It costs nothing and immediately enforces the recommendation ("every training run is falsifiable").
2. **Build Option A+B hybrid:** template-generate ~400–600 questions from provisions/registry, human-spot-review 80 stratified (accept/reject with reason), publish the accepted pool + reviewer audit. Target accepted pool ≥800 (to pair with the 21 qids → ~200–400k pairs).
3. **Re-split and freeze a new baseline** (`ce_v2_baseline.json` + `answers_baseline.json`) on the bigger held-out test before the first joint-training run, and document the split percentages.
4. **Until then:** ranker retrains gated on ranking (pairwise MDE ≈0.7pp is fine); answer-level claims reported with MDE-aware language (e.g., "≥X pp change; at n=150 the smallest detectable gain is ~5pp").

## What is already in place (so only Step 3 remains)

- ✅ **Labels fixed before training** — `build_label_baseline.py` produces `evaluation/out/ceiling_v5/label_baseline.json` (150 qids: 58 human-correct, 38 unresolved split into 14 widen-overlay recoverable + 28 human-label-only; 89 model_wrong). The bridge/pool/trainer already consumed the 150/150 human verdicts.
- ✅ **Retrieval↔answer metrics joined** — `retrieval_answer_link.py` reports R@5/10/20 + MRR alongside soft/binary + human_correct, conversion rates, ranker-upside (optimistic vs causal 0/7), elasticity, and point-biserial/Spearman correlations; `ce_v2_gate.py` now reads `binary_correct` (via `answer_context_checks`) alongside R@k/MRR and supports `--answers-json` paired hard gates.
- ❌ **Eval-set size** — the single remaining blocker to joint training (Step 3).
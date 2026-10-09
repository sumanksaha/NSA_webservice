# Evaluation artifacts

Reference for the training/eval data files this directory produces. Read this
instead of probing an unfamiliar `out/` JSON with ad-hoc scripts.

## Reading these files

Everything under `evaluation/out/` is **gitignored**. The file-reading tools
refuse those paths (`BLOCKED`), so read them through the terminal:

```bash
python -c "import json,sys; print(json.dumps(json.load(open('evaluation/out/cache/train_pool_v1.json')), indent=2))"
```

Check a value's type before slicing it — several top-level keys are objects, not
strings (`recommended`, `weights`, `counts`, `eval_gate`, `qids`).

## Data flow

```
evaluation/out/ceiling_v5/hard_negative_mining.jsonl
        + payload_index.jsonl
        └─► pairwise_dataset.py ─► out/cache/pairwise_training_v2.jsonl
                                        ├─► pairwise_train_split.json   (by question_id)
                                        └─► train_legal_ce_v2.py ─► models/legal_ce_v2_K500/

out/ceiling_v5/{failure_attribution,full_review_tabulation}.json
        └─► train_pool_v1.json
                └─► train_legal_ce_v3.py --use-pool ─► models/legal_ce_v3_K500/
```

`legal_ce_v1` is the frozen control and is never retrained. `legal_ce_v2` is the
baseline. `legal_ce_v3` is the lighter pool-weighted fine-tune.

## Training substrate

### `cache/pairwise_training_v2.jsonl` (27,207 lines)

One JSON object per line. Built by `python -m evaluation.pairwise_dataset`.

| Field | Meaning |
| --- | --- |
| `query` | benchmark question text |
| `positive` / `negative` | passage texts the ranker compares |
| `tier` | 1 random · 2 semantic · 3 adversarial |
| `tier_label` | `random` / `semantic_hard` / `adversarial_legal` |
| `question_id` | e.g. `Q124` — the split key |
| `gold_unit` | e.g. `fssai:s33`; its `:`-prefix is the domain |
| `positive_*` / `negative_*` | `section`, `clause`, `act` — authoritative identity joined from `payload_index.jsonl` |
| `neg_chunk_id`, `neg_features` | provenance of the negative |

### `cache/pairwise_training_v2_stats.json`

`mode`, `total_pairs`, `tier_distribution`, `unique_questions`, `split`
(question- and pair-counts per split plus the `train_qids` / `val_qids` /
`test_qids` arrays), `section_prefix`, `domain_balanced`, the `*_coverage`
ratios, and `domain_distribution`.

**Split discipline:** all pairs from one question stay in one split. Never
re-split by pair — that leaks question text across train and val.

### `cache/pairwise_train_split.json`

Just the split block above: counts plus the three qid arrays.

### `cache/payload_index.jsonl`

`{id, payload}` per chunk — the authoritative source for section/clause/act
identity. Use it rather than any `section` field carried in a mining record;
mining records pre-date the noise strip and are stale.

## Question pool (ceV3)

### `cache/train_pool_v1.json`

Classifies all 150 benchmark questions by whether ranking training can address
their failure. Built from `ceiling_v5/failure_attribution.json` +
`full_review_tabulation.json`.

| Key | Type | Contents |
| --- | --- | --- |
| `weights` | dict | `priority_provision: 3.0`, `priority_promotable: 2.0`, `guard: 1.0`, `untrainable: 0.0` |
| `counts` | dict | question count per bucket (13 / 13 / 61 / 63) |
| `qids` | dict | bucket → list of qids |
| `verdict_counts` | dict | `evaluator_miss: 57`, `model_wrong: 89`, `reference_narrow: 4` |
| `eval_gate` | dict | `promotion` (26 qids), `guard_check` (61 qids) + their `*_check` rules |
| `rationale` | str | why `retrieval_rank` is trainable and `generation`/`never_retrieved` are not |
| `recommended` | **dict** | `{"trainer": "python -m evaluation.train_legal_ce_v3 --use-pool (optionally --pool-scale 1.0)"}` |

`untrainable` qids are dropped by the trainer — their error is generation-side,
so booking it against a ranker retrain would be wrong.

## Model outputs (`out/models/`)

| Path | Meaning |
| --- | --- |
| `legal_ce_v*_K500/train_state.pt` | resumable checkpoint (model + AdamW + RNG + epoch shuffle) |
| `legal_ce_v*_K500/training_status.json` | pollable progress, written every 5 steps |
| `legal_ce_v*_K500/tokenized_cache.pt` | tokenized pairs, keyed on a hash of the data files |
| `ce_train_summary_v*_k500.json` | **written only by a completed run** |

`training_status.json` with null losses is a resumability artifact, **not**
evidence of a completed run. Only `ce_train_summary_*.json` carries
`best_val_loss` / `global_steps_completed`.

Monitor a run: `python -m evaluation.train_legal_ce_v3 --status` (or `--watch`).

## Answer-side reference (`out/ceiling_v5/`)

| File | Use |
| --- | --- |
| `answers_baseline.json` | frozen C-O3 answers, n=150 |
| `retrieval_answer_link.json` | retrieval ↔ answer correctness joint analysis |
| `failure_attribution.json` | per-qid bucket: `retrieval_rank` / `generation` / `never_retrieved` |
| `full_review_tabulation.json` | 150/150 human verdicts |
| `evaluator_v3_candidates.json` | 61 qids needing reference widening |

## Benchmarks

See `benchmark/README_v1.0.md`. Benchmark v1.0 is frozen; changing a question
means minting v1.1 or later.

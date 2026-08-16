# CE v2 Improvement Plan

> **Status:** Planning (post-training analysis complete)
> **Model:** `evaluation/out/models/legal_ce_v2_K500`
> **Baseline:** `evaluation/out/models/legal_ce_v1`
> **Training script:** `evaluation/train_legal_ce_v2.py`
> **Error analysis:** `evaluation/ce_v2_eval.py` (reference results in conversation)

## Current State

- V2 trained: 3 epochs, margin ranking loss, T1→T2→T3 curriculum, 10,347 pairs
- Test (21 queries, 2,362 pairs): R@1 +19%, MRR@10 +8%, nDCG@10 +6%, pairwise acc -3.5%
- 0 retrieval-fail cases (all golds in top-10 for both models)
- Top bottleneck: hierarchy/version confusion (same-document, nearby-section negatives)

## Tasks (ranked by ROI)

### P1 — Section-identifier-augmented training (HIGH ROI, LOW effort)

- **Objective:** Fix hierarchy-version confusion (8 cases, 38% of queries, net MRR -1.16)
- **Approach:** Prepend `§<section>` to every passage text in the training pipeline. Re-train V2 with the same hyperparameters (3 epochs, margin=1.0, curriculum).
- **Files:** `evaluation/pairwise_dataset.py` (add section prefix), `evaluation/train_legal_ce_v2.py` (no change needed — uses pairwise data)
- **Dataset:** Same 10,347 pairs, reformatted. ~5 min data prep.
- **Expected gain:** Reverse 4 hierarchy regressions, +0.15 MRR@10, +0.2 R@1
- **Risk:** Minimal — section numbers are ground-truth metadata
- **Owner:** TBD
- **Estimate:** 1 day (data prep + retrain ~7h)

### P2 — Same-subsection hard negatives (HIGH ROI, MED effort)

- **Objective:** Fix same-section hard negative failures (3 cases, 14%, net MRR -0.50)
- **Approach:** Mine additional hard negatives from the same subsection but different provisos/clauses. Use `hard_negative_miner.py` with a subsection-level filter.
- **Files:** `evaluation/hard_negative_miner.py` (add subsection filter), `evaluation/pairwise_dataset.py`
- **Dataset:** Target 300-500 new pairs per high-volume test query (Q014, Q016, Q018, Q020, Q049, Q050, Q102, Q118, Q120, Q122, Q143, Q148, Q150)
- **Expected gain:** +0.08 MRR@10, +0.15 hard-negative accuracy
- **Risk:** Moderate — may reduce recall on genuinely similar passages
- **Owner:** TBD
- **Estimate:** 2 days (mining + data prep + retrain)

### P3 — Calibration dataset (MED ROI, LOW effort)

- **Objective:** Fix confidence calibration (margin 4.4x larger but accuracy -3.5%)
- **Approach:** Temperature scaling on V2 using the 2,362 test pairs as calibration set. Find optimal temperature T that minimizes pairwise loss on test data.
- **Files:** `evaluation/ce_rerank_eval.py` (add calibration function)
- **Dataset:** Existing test pairs (no new data needed)
- **Expected gain:** +2-3% pairwise accuracy
- **Risk:** None — post-hoc calibration
- **Owner:** TBD
- **Estimate:** 4 hours (implementation + eval)

### P4 — Domain-balanced re-sampling (MED ROI, MED effort)

- **Objective:** Fix fssai domain regression (-6.5% on 1,608 pairs) while maintaining epa/contract gains
- **Approach:** Oversample non-fssai acts to match fssai volume, or add domain-aware loss weighting (inverse-frequency).
- **Files:** `evaluation/pairwise_dataset.py` (add domain balancing), `evaluation/train_legal_ce_v2.py`
- **Dataset:** Re-weight existing 10,347 pairs by domain (fssai:comp/epa/contract = 1:1:1:1)
- **Expected gain:** +4-8% on non-fssai domains, net +0.02 MRR
- **Risk:** Low — standard technique
- **Owner:** TBD
- **Estimate:** 1 day (data prep + retrain)

### P5 — More test data for small domains (LOW ROI, HIGH effort)

- **Objective:** Enable proper statistical evaluation of kmc, srf, water_act
- **Approach:** Collect 50-100 additional test pairs for each underrepresented domain
- **Files:** `evaluation/evidence_set_selector.py` (extend sample), `evaluation/benchmark/`
- **Dataset:** New test pairs — 50-100 per domain
- **Expected gain:** Better error analysis, more reliable CIs
- **Risk:** None
- **Owner:** TBD
- **Estimate:** 3 days (data collection + validation)

## Execution Order

```
P1 → P2 (parallel) → P3/P4 → P5
     P3 (parallel, post-hoc)
```

P1 should be done first — it's the highest-impact, lowest-effort fix. P2 can be done in parallel (mining takes time but doesn't block P1). P3 is a post-hoc fix that can run anytime. P4 depends on having P1/P2 trained models to compare against. P5 is lowest priority.

## Retraining Commands

```bash
# P1
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50

# P2 (after mining new pairs)
python -m evaluation.pairwise_dataset --with-subsection-negatives
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50

# P3 (post-hoc, no retraining)
python -m evaluation.calibrate_ce --model evaluation/out/models/legal_ce_v2_K500

# P4
python -m evaluation.pairwise_dataset --domain-balanced
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50
```

## Evaluation Checklist

- [ ] Re-run `ce_v2_eval.py` after each improvement
- [ ] Verify R@1, MRR@10, nDCG@10 on the 21 test queries
- [ ] Verify pairwise accuracy on all 2,362 test pairs
- [ ] Check hierarchy-version failures specifically (8 → target: 4 or fewer)
- [ ] Check same-section hard negatives (3 → target: 1 or fewer)
- [ ] Verify no regression in epa/contract domains
- [ ] Update `AGENTS.md` with new model version when deployed

# Evaluator v4 — "Optimum Binary Ruler" Design Review

**Status:** DESIGN REVIEW — written before anything is built (per the "deep-dive design doc first" decision)
**Date:** 2026-10-17
**Ground truth for every number below:** `evaluation/out/ceiling_v5/full_review_tabulation.json`
(150/150 human audit: `model_wrong` 89 · `evaluator_miss` 57 · `reference_narrow` 4).

## 1. Purpose

The token-overlap binary ruler is provisionally broken: it credits **~8–12%** of
answers where human adjudication credits **~61%**. Before any LLM-side
optimization resumes, the ruler itself must be rebuilt so that:

1. `CREDIT` — a recall-first binary that tracks *true* (human-comparable) accuracy;
2. `CLAIM` — a precision-first binary safe to gate promotions/retirements on.

Both derive from one deterministic, 0-LLM component scorecard. This document
reviews the *existing* evaluator chain (v1 → v2 → v3), states the measured
constraints, lists the defects a v4 must not inherit, and specifies the v4
design, acceptance criteria and sequencing. **No code was changed to produce
this review.** One significant new finding was made during the review (§4, F2)
and it changes the interpretation of a previously published headline number.

## 2. Ruler lineage — what exists, what it measured

| Version | Rule (as implemented) | Measured outcome |
| --- | --- | --- |
| **v1** (`evaluation/grading.py` + `token_overlap`) | soft = token overlap vs a *single* `acceptable_conclusion`; binary = soft > 0.5; 0/1/2 score with `critical_error` zeroing; abstention computed but **not** folded into binary (v1 gap, fixed by v2) | recovers **0/57** evaluator misses (`false_negative: 57`, precision 0.0 — `v3_component_rescore.json`) |
| **v2** (`rescore_evaluator_v2.py`, `evaluator_v2_overlay.json`) | reference-side only: max overlap over {v1 conclusion ∪ widened_conclusions}; abstention-credit folded into binary on IE questions; banned abstain markers for 2 qids | fixes **19/57** misses under best condition; **38 remain unresolved**, all with best-arm soft in **[0.248, 0.496]** — *none above 0.5* |
| **v3 triage** (`triage_evaluator_v3.py`) | splits the 38 unresolved: `widen_overlay` = never widened (14 qids: Q012, Q028, Q068, Q071, Q083, Q090, Q119, Q128, Q132–Q135, Q137, Q146) · `overlay_insufficient` = widened once, still fails (24 qids: Q004, Q006, Q008, Q009, Q013, Q019, Q029, Q030, Q041, Q056, Q061, Q065, Q066, Q069, Q077, Q087, Q092, Q101, Q106, Q113, Q140, Q142, Q143, Q150) | verdict: 14 need *more reference* (human legal task); 24 need a *different rule, not more prose* |
| **v3 component rule** (`evaluator_v3_component_rule.py`) | threshold sweep over best-arm soft + component availability audit | **negative result, measured:** soft distributions of unresolved misses (median 0.413) and model_wrong (0.379) overlap almost completely; precision peaks **0.41** at mid-thresholds and *degrades* as the threshold falls; conclusion: "Component signals are required." |
| **v3 component rescore** (`v3_component_rescore.py`) | re-runs `grade_answer` over worksheet answers; rules compared: `proposed_v3 = provision_correct OR (legal_correct AND NOT hallucination)` | proposed_v3 recovers **53/57 (93%)** but falsely credits **82/89** model_wrong (precision **0.39**); `legal_only` = 57/57, precision 0.39 |
| **top-lawyer rubric** (`rubric_top_lawyer.py`) | 4 deterministic dimensions vs audit's own `model_action` taxonomy: provision_check 0.80 · reasoning_depth 0.617 · abstention_discipline 0.50 · presentation 0.983 (failure cohort n=60); **false_abstention 0.383**, missed_abstention 0.117 | the only existing semantic, 0-LLM scorer; thresholds frozen to share `DIMENSION_PASS_THRESHOLD = answer_scoring.BINARY_CORRECTNESS_THRESHOLD = 0.5` |

## 3. Measured constraints any v4 design must accept

1. **No threshold on soft overlap can separate the classes** — measured, not
   assumed (sweep table in `evaluator_v3_component_rule.json`; precision 0.41
   ceiling). Threshold tuning is permanently off the menu.
2. **Reference widening as done so far cannot lift misses over the line** —
   after v2 widening, 0 of 96 arm-scores of unresolved misses sit above 0.5.
   The 14 `widen_overlay` qids have never been widened at all; the 24
   `overlay_insufficient` qids prove that *one extra paragraph* is not the fix.
3. **The evaluation oracle is small and noisy:** n=150, MDE ≈ 5.1pp; two
   identical live runs flip the pooled binary on **8/89** questions
   (`FINDINGS_89_STATUS.md`). Any binary decision layer needs replicates or
   deterministic components.
4. **Abstention is the second-largest confusion source:** discipline rate 0.50
   and false-abstention 0.383 in the failure cohort, driven by a hand-written
   regex lexicon with banned markers for only 2 qids.

## 4. Design-review findings (defects & risks to fix in v4)

### F1 — `legal_correct` is itself a token-overlap threshold
`grading.py:119`: `legal_correct = concl_ratio >= 0.35 or concepts_ratio >= 0.45`.
The v3 "component" rule is phrasing-independent **only in its provision-identity
leg**. The `legal_correct` leg is an overlap gate with sub-thresholds pulled below
0.5 — which is exactly what drives the 82 false credits. A v4 `CLAIM` gate that
re-uses this leg will inherit the failure.

### F2 — the 93% recovery was measured with the provision leg **disarmed** (new finding)
`v3_component_rescore._components_for()` shims the grader with
`cited_chunk_ids=[], evidence_chunk_ids=[], payload_index={}`. In `grade_answer`,
`provision_correct` is computed by joining `cited_chunk_ids → payload_index →
matches_gold`; with an empty payload index it can **never** be True (and forces
`critical_error → score 0`). Consequences, visible in the published numbers:

- `proposed_v3` (TP 53, FP 82) ≈ `legal_only` (TP 57, FP 90) modulo the
  hallucination conjunct — i.e. **the entire measured recovery comes from the
  overlap leg**, not from provision identity;
- the headline "93% recovery / precision 0.39" describes *concept-overlap
  recovery*, not component-rule recovery;
- the unmeasured promise ("provision identity is phrasing-independent, so a
  correct paraphrasing answer still passes") remains **plausible but unproven**,
  because the worksheet's `[n]` markers are prompt positions, not chunk ids.

**v4 implication:** the component rule must first be measured with *real* chunk
ids and payload identity over the stored raw run outputs (they exist — the
artifacts C/D/E per-question files carry contexts), not over the shimmmed
worksheet. This is a re-measurement, not a re-implementation.

### F3 — per-question OR over 4 arms conflates arm-level with question-level truth
`v3_component_rescore.evaluate()` credits a question if **any** of the arms
(C-O3/D2/D3/E1) passes. (The tabulation's `machine` does the same packing.) A
binary ruler must be **per-answer** (per arm / per run); cohort-level "best arm"
one-or-ing belongs in the *report*, not the ruler. Under F2's shimming the same
overlap-heavy answer text repeats across arms, further inflating apparent
credit.

### F4 — the 0.5 threshold is shared by three unrelated mechanisms
`DIMENSION_PASS_THRESHOLD` (rubric pass) = `BINARY_CORRECTNESS_THRESHOLD`
(pooled binary) = the v1/v2 soft cutoff. Nothing justifies one constant across
an answer-overlap gate, a dimension pass, and a decision gate. v4 calibrates
each component threshold independently against the human tabulation.

### F5 — abstention lexicon is the known, un-expanded failure surface
`_ABSTAIN_PHRASES`/`_REFUSE_PHRASES` regex matching yields **false abstention in
38.3%** of the failure cohort; v2's `banned_abstain_markers` covers only 2 qids.
`abstain_credit` then folds these errors directly into the binary. v4 must
calibrate the abstention component against the tabulation (target: false
abstention ≤ 10%) before crediting with it.

### F6 — `critical_error` couples "no gold provision cited" to a zero score
`grading.py:156`: `if not provision_correct and units: critical_error = True`.
For any 0/1/2 *score* report this conflates "answer named the wrong provision"
with "citations failed to resolve" (an infrastructure failure, e.g. empty
payload index — exactly what F2 demonstrated). v4 separates `provision_identity`
(infrastructure resolvable) from `provision_wrong` (a genuine legal error).

### F7 — detached reference canonicalization
v1's single `acceptable_conclusion` string, v2's one-`add` overlay, and the
grader's separate `gold_concepts` are three disconnected reference shapes. The
rubric (reasoning_depth) covers concepts; the binary covers conclusions; a
correct answer can appease one and fail the other. v4 needs one versioned,
multi-reference reference bundle per qid (§5.3).

## 5. v4 design

```
Layer 0 — inputs (per answer, per run)
  question bundle:  eid, acceptable_conclusion, widened refs (v2 ∪ authored v4),
                    gold_concepts, expected_abstention (IE flag), penalty-structure flag
  answer side:      answer text, cited chunk ids, evidence texts, payload index

Layer 1 — components (deterministic, individually calibrated vs human tabulation)
  C1 provision_identity   cited chunks cover a gold unit via payload-key matches_gold
                          (identity, not token overlap)          [fixes F1/F2/F6]
  C2 conclusion_coverage  max coverage over the reference bundle — calibrated
                          thresholds per qid-class; never the sole gate
  C3 abstention_discipline calibrated lexicon; abstain ⇔ reference expects abstention
  C4 citation_legitimacy  [n] markers resolve to gold-covering chunks
  C5 hallucination_guard  unchanged evidence-blob check (deterministic)

Layer 2 — derived binaries (published separately, never pooled)
  CREDIT (recall-first):    C1 OR (C2 ∧ ¬C5) OR abstain_credit        → tracks accuracy
  CLAIM  (precision-first): C1 ∧ C2 ∧ ¬C5 ∧ ¬false-abstention        → gates promotions
  both publish their confusion matrix vs the human tabulation, per cohort.

Layer 3 — scorecard
  per-dimension rubric rates (unchanged top-lawyer dimensions) + v1/v2/v4
  binaries side-by-side + near-threshold bands + 3-replicate variance for any
  live arm.
```

### 5.1 Anti-Goodhart rules
- **R1:** identity components resolve via payload keys (`matches_gold`), never
  via reference-text token overlap alone.
- **R2:** the reference bundle is frozen and versioned
  (`evaluation/evaluator_v4_references.json`, additive merge — same discipline as
  `step0_dual_score_widen.validate_widening`); a ruler change requires a ruler
  version bump, never an in-place reference edit.
- **R3:** thresholds are calibrated **only** against the completed human
  tabulation — never against the LLM's own answers.
- **R4:** every report shows v1/v2/v4 together (the convention v2 set); the v1
  binary stays frozen as control.
- **R5:** crediting is per-answer; cross-arm ORs happen only in the report layer.

### 5.2 Acceptance criteria (measurable, staged)
- **A1 (v4.0):** components measured with real chunk ids; CREDIT recovers ≥ 90%
  of the 57 misses at a *published, honest* false-credit count (F2 re-measurement
  may change the 0.39 precision figure — that is a demanded, not feared, result).
- **A2 (v4.1):** after the 14-qid reference widening: CLAIM false-credit
  precision ≥ 0.60 — i.e. crediting a model_wrong at ≤ 40% as a *stated bound*.
- **A3 (v4.2):** CLAIM precision ≥ 0.80, evaluator_miss ≤ 5%, false abstention ≤ 10%.
- **A4:** determinism: two offline runs produce identical verdicts.
- **A5:** near-threshold band counts published next to every binary figure
  (v2 convention).
- **A6:** aggregate correctness under v4 is re-baselined for the Experiment B
  arms before any new LLM-side lever is gated with it.
- **A7 (soft-ruler validity):** agreement with the human partial-credit audit,
  per question — target MAE ≤ 0.25 on the 0/0.5/1 human verdict scale
  (`yes`/`partial`/`no` from `human_correct`) and per-verdict-class means
  separated enough to rank the classes (mean_soft[yes] > mean_soft[partial] >
  mean_soft[no] with non-overlapping IQR bands). Aggregate agreement (soft
  mean ≈ 38.7% true mean) is explicitly NOT acceptance: the current ruler
  already achieves it by accident while mislabeling individual questions.

### 5.3 Critical path (sequencing)
1. **Re-measure with real chunk ids** (F2): re-run `grade_answer` over the
   stored per-question run artifacts (C/D/E), matching `[n]` markers to chunk
   ids from the cached contexts where available; publish the corrected
   component confusion matrix.
2. **Calibrate components** — per-component ROC against the human tabulation;
   select CREDIT/CLAIM operating points with published bands (F1/F4/F5).
3. **Reference widening** for the 14 `widen_overlay` qids — human legal review,
   additive-merge via the existing validated widening workflow.
4. **Per-qid disposition of the 24 `overlay_insufficient`** — rule-side fixes
   only (abstention marker FPs, temporal, multi-provision, penalty-structure);
   explicitly **not** more reference prose.
5. **Abstention lexicon v2** calibrated to ≤ 10% false abstention.
6. **Implement `evaluation/evaluator_v4.py`** (components + two binaries +
   scorecard), freeze references, bump ruler version.
7. **Re-baseline** Experiment B arms; publish the superseded-vs-v4 comparison.

## 6. Open questions (need a human decision)
- **O1:** `CLAIM` semantics — is "names the right provision AND concludes
  within the reference bundle AND no unsupported claims" an acceptable
  *promotion gate*, or should gates additionally require citation legitimacy
  (C4) on grounded questions?
- **O2:** the 15/61 control-cohort "passes" include abstention credits — recheck
  under C3 before claiming a control baseline.
- **O3:** LLM-as-judge as a *third opinion* — allowed post-v4.0 only, budgeted,
  and validated against the tabulation; the 0-LLM composite remains primary
  (kill-switch economics + determinism).
- **O4:** do the 4 `reference_narrow` verdicts re-attribute after reference
  widening, or stay a permanent excluded class?

## 7. Sources reviewed
`evaluation/grading.py` · `evaluation/rescore_evaluator_v2.py` ·
`evaluation/triage_evaluator_v3.py` · `evaluation/evaluator_v3_component_rule.py` ·
`evaluation/v3_component_rescore.py` · `evaluation/rubric_top_lawyer.py` ·
`evaluation/answer_scoring.py` ·
`evaluation/out/ceiling_v5/{full_review_tabulation.json, evaluator_v3_triage.json,
evaluator_v3_component_rule.json, v3_component_rescore.json,
rubric/rubric_summary.json}` — read under the "no ruler change before review"
discipline; nothing in that set was modified.

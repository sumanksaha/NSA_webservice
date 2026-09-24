# Experiment F - Results

## Conditions (evaluator v2)

| Condition | n | Soft v2 | Binary v2 |
|---|---:|---:|---:|
| F0 | 150 | 0.3853 | 0.1200 |
| F1 | 150 | 0.3854 | 0.1200 |
| F2 | 150 | 0.3865 | 0.1200 |
| F1F2 | 150 | 0.3861 | 0.1200 |

## F1 gate (abstention calibration)

- targets machine-incorrect: 29 | recovered: 0
- insufficient-evidence regressions: 0 (must be 0)
- binary gain: +0.0000 (must be >= +0.02)
- **passes: False**

## F2 gate (provision disambiguation)

- targets machine-incorrect: 31 | fixed: 0
- already-correct regressed: 0 (must be 0)
- **passes: False**

## Guard + accounting

- recovered: 16 | recovery-rejected: 0 (rate 0.000, must be <0.10)
- generations: 68 ok / 105 attempts (cap 150)

## Post-run addendum: per-status accounting and the honest verdict (2026-09-23)

Run history: initial pass gated only 13 F1 questions (deterministic preconditions
filtered the force-gate); fixed gate semantics and reran the 27 dropped targets
(+24 generations). Final accounting: **68 successful generations of the 150 cap**
(105 attempts; 37 transport/429 failures logged separately).

### F1 on the 29 machine-incorrect audit targets

| Outcome | n | Meaning |
|---|---:|---|
| recovered (substantive answer) | 16 | model produced an answer; 6 identical to D2 (restated), 10 changed |
| justified_abstention_by_model | 14 | model named a genuinely missing statutory element (Rule 63 text, Order 12, s25-29 Water Act text, KMC water-connection rule...) |
| recovery_failed (429 x3 attempts) | 3 | Q036, Q047, Q084 - transport, not model |

### The two decisive negative findings

1. **The abstentions were mostly CORRECT.** 14 of 26 completed recoveries named a
   specific genuinely-missing statutory element - the exact texts the questions
   ask about are simply not in the O3 evidence. The audit's "over-abstention"
   cluster is substantially an **evidence-availability problem mislabeled as a
   calibration problem**: the model refuses because the answer truly is not there.
2. **Where the model answered, the answer did not improve.** All 16 recovered
   answers remain binary-incorrect (0/12+ threshold crossings); mean soft delta
   +0.0003. The free-tier model, given the same evidence a second time and told
   its answer was judged a refusal, restates or paraphrases the same position.
   Q052 is emblematic: the audit expected the s51/s52 remedy route, the evidence
   supports the s43-order/penalty route the model keeps giving.

### F2

33 re-decision calls executed; soft +0.0012 aggregate, 0 already-correct
regressions (the safety property held), but 0 of 31 audit targets crossed the
0.5 binary bar. The re-decisions produce locally-corrected provision strings
that do not change the substantive position the evaluator scores.

### Pre-registered gate outcomes

- **F1 gate: FAIL** (0/29 recovered to binary-correct; IE regressions 0 as required)
- **F2 gate: FAIL** (0/31 fixed; already-correct regressions 0 as required)
- Guard health: reject rate 0.00 (<0.10 as required) - the layer is SAFE but not additive.

### Design decision rule applied (sec 10)

"Neither passes -> model errors require deeper semantic representation rather
than surface calibration; pivot to Experiment G." Additionally, the F1 evidence
reframes the next intervention: the dominant missing-evidence families (Water Act
section texts, WB Meat Order order texts, KMC water rules, PCA Rules schedules)
motivate an **evidence-completion experiment** (targeted corpus expansion) more
than any generation-side change.

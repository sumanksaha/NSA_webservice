# Current-stage assessment: soft and binary correctness, all 150 questions

Produced 2026-10-11, **no LLM calls** (budget for the day is zero). Everything
here is computed from the recorded generation cache and the re-measured
retrieval; nothing was re-generated.

Sources: `evaluation/out/ceiling_v5/reeval_89/gen_C_hybrid.jsonl`,
`gen_C_hybrid_section.jsonl` (150 records each), joined on qid to
`retrieval.jsonl` for cohort.

Sources (KG): `evaluation/kg_effect_on_failures.py` driven by production
`KGContextExpander`, reachable Neo4j instance (`21090cf9`). No LLM calls.

## 1. The numbers

`C_hybrid` (baseline), all 150:

| cohort | n | soft mean | soft median | soft range | binary | of which abstention-credited | **substantive** |
|---|---|---|---|---|---|---|---|
| failure (model_wrong) | 89 | 0.334 | 0.353 | 0.00-0.62 | 4/89 (4.5%) | 1 | **3/89 (3.4%)** |
| control | 61 | 0.357 | 0.366 | 0.00-0.54 | 15/61 (24.6%) | 11 | **4/61 (6.6%)** |

`C_hybrid_section` (scoped) is **worse on both cohorts**: failures 3/89 binary and 2/89 substantive; controls 13/61 binary and 4/61 substantive. The substantive failure passes are `Q015`, `Q024` (baseline adds `Q047`).

## 2. The headline binary gap is mostly composition, not quality

Control contains **14 `insufficient_evidence` questions (23%)**; failure contains
**1 (1%)**. The abstention credit applies only to IE questions, where the
benchmark itself says "a correct abstention is the right answer". So 11 of
control's 15 binary passes are *abstentions*, i.e. passes without a substantive
answer.

- Binary gap as recorded: 24.6% vs 4.5% -> **5.5x**
- Substantive gap: 6.6% vs 3.4% -> **1.9x**

Control's soft scores are also *lower* than failure's maximum (0.54 vs 0.62)
while its binary rate is higher. The gate is rewarding abstention, not similarity.
The IE rule itself is correct and should not be removed; the point is only that
the binary column is not a clean quality signal.

## 3. Where the failures actually are

Concept shortfall across the 85 incorrect failure questions:

| shortfall | count |
|---|---|
| 0 concepts missing, still scored wrong | 12 |
| 1 concept short | 33 |
| 2 concepts short | 18 |
| 3-4 concepts short | 21 |
| 5+ concepts short | 1 |

**72 of 85 are within 1-4 concepts of the reference.** Only one failure is
genuinely far away.

## 4. Retracted: the "59/60 gate reachability" figure

An earlier version of this document stated that `concept_gate_reachability.json`
showed `gold_echo_eliminates: 59/60` and concluded "the gate is effectively a
concept-coverage gate and it is nearly reachable". **That was wrong and is
withdrawn.**

Two reasons:

1. **The artifact is pre-filtered.** All 60 of its rows carry
   `concepts_all: true` and `binary: 1` — it was computed over the questions
   that had *already passed*. Proving that passing questions would pass says
   nothing about the 90 that did not, so it is not reachability evidence.
2. **It does not reproduce.** Re-derived with the frozen scorer
   (`reprobe_gold_echo`), the reference conclusion passes the gate **150/150**
   and a gold-concept echo passes **0/150** — the inverse of what it claims.
   Per-qid agreement is 4/60 and 1/60, i.e. chance.

There is also no gold-echo failure mode to guard against: gold concepts are
computed only under `evaluation/` and appear nowhere in
`app/rag/generation/`, so they never enter the prompt.

## 5. KG effect measurement

`evaluation/kg_effect_on_failures.py` ran against the 89 failure cohort with a
reachable Neo4j instance (Aura `21090cf9`). The KG is the production
`KGContextExpander`, RRF-fusing expanded provisions into the LLM context without
re-ranking retrieval.

| metric | value |
|---|---|
| mean ctx coverage (no KG) | 0.444 |
| mean ctx coverage (with KG) | 0.486 |
| **mean delta** | **+0.0418** |
| questions with positive delta | **46/89** |
| questions crossing 0.5 threshold | **9** |
| expansion errors | **0** |

The KG raises the distinctive coverage of the context that reaches the LLM.
This is the precondition for the KG helping binary/soft correctness -- not proof,
since an LLM still has to use it.

### 9 questions crossing the 0.5 coverage threshold

`['Q007', 'Q016', 'Q021', 'Q049', 'Q053', 'Q076', 'Q079', 'Q085', 'Q088']`

These questions' context coverage goes from below to above 0.5 with KG injection.

### What this revises in the earlier assessment

- **Retracted: "the KG has no demonstrated path"** — the earlier analysis used
  arm C (no KG). The KG is arms D/E/F. The measurement was never run on the
  failure cohort; the 5-question pilot (`kg_injected_gold: 0`) had retrieval
  recall 1.0, so the KG couldn't contribute. On the 89 failures (gold-in-top10
  45/89), the KG *does* add distinctive coverage.
- **The answers are wrong, not the gate — AND the KG helps.** The two findings
  are not mutually exclusive. The gate's length asymmetry (jaccard 0.096 vs
  coverage 0.628) is the fundamental blocker, but the KG *does* add distinctive
  content the retrieval alone doesn't provide. 46/89 questions see a positive
  delta, and 9 cross the 0.5 threshold.
- **What the KG optimisation would target**: increasing distinctive coverage
  (currently 0.444→0.486 mean) and reducing boilerplate in KG provisions
  (boilerplate share 0.112 of reference). The 9 questions crossing 0.5 are the
  lowest-hanging fruit.
- **What the KG cannot fix**: the gate's length asymmetry (answers 6x reference
  length), or generate substantive content beyond the KG provisions.

### Updated decision framework

| metric | pass | fail | gap |
|---|---|---|---|
| corpus word presence | 0.908 | 0.887 | +0.021 |
| gold provisions | 0.459 | 0.364 | +0.094 |
| retrieved context | 0.613 | 0.437 | +0.176 |
| **the answer itself** | **0.789** | **0.424** | **+0.365** |
| **KG distinctive coverage** | **varies** | **varies** | **varies** |

The KG is now part of the evidence: it raises distinctive coverage, which is a
precondition for the LLM to produce correct answers, but it does not by itself
clear the gate.

## 6. Near-miss character of the concept gaps, for the record

`concept_gate_diagnosis.json` classifies the 104 missing concepts as 61
near-miss / 43 far. In light of the KG measurement, near-miss is better read
as the answers reaching the right register without the specifics, not as a
retrieval gap. The split should be re-measured after the KG is enabled and
its effect on coverage is settled.

## 7. Retrieval state for the same questions

| | failure gold-in-top10 | control gold-in-top10 |
|---|---|---|
| before remediation | 45/89 | 36/61 |
| **after remediation** | **45/89** | **40/61** |

Control improved by 4; failures held. This matches the prediction that the
stamping remediation removes a mislabelling rather than adding text.

## 8. Data-integrity problems found while producing this

Both silently distort a naive roll-up:

1. **`cohort: None` on some generation records.** Grouping on that field
   demotes failures into control. The cohort is now taken from `retrieval.jsonl`,
   which labels every qid; this restored exact 89/61 splits (the first run
   reported 88/62 and 87/63).
2. **`concepts_missing` is a list, not a count.** Any numeric comparison against
   it raises; `len()` is required.

## 9. Standing caveats

- Generation is the **pre-remediation** cache. Retrieval was re-measured after
  remediation; generation cannot be refreshed without an LLM budget.
- Gold resolution remains at 2 unresolved units (`Q034 pcra:s63`, `Q042 pcra:s63`),
  the OCR-blocked `pcra` family (P0-2). This is separate from the FSSAI s52/s63
  operative-text gap that sizes the procurement at 2 questions.
- The KG now raises distinctive coverage (0.444→0.486 mean, 46/89 positive
  delta, 9 crossing 0.5). The KG is now part of the evidence pipeline, but
  does not by itself clear the gate — the length asymmetry blocker remains.
- The gold labelling check: gold provisions cover only 0.369 of the reference's
  distinctive mass, less than the answer's 0.442. The reference draws on material
  beyond the labelled gold. This should be checked before any further pipeline
  work, because it bounds everything else.
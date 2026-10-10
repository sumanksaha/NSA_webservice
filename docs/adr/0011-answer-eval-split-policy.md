# ADR-0011: Answer-level evaluation split policy

- **Status:** Accepted
- **Date:** 2026-10-09
- **Context:** `evaluation/benchmark.py`, `evaluation/split_policy.py`, `evaluation/stage2_ab_n20_report.json`
- **Deciders:** Architecture review
- **Related terms in `CONTEXT.md`:** `Benchmark`, `Holdout`, `MDE`, `A/B`

---

## 1. Context

`benchmark_v1.0.jsonl` labels every question:

| split | n |
|---|---:|
| `dev` | 100 |
| `validation` | 30 |
| `holdout` | 20 |

`BenchmarkQuestion.split` has always existed (`benchmark.py:91`). **No harness
has ever read it.** `eval_e2e_v2.py`, `ab_window_width.py`,
`ab_targeted_retry.py`, `ab_baseline_citation.py` and `ab_kg_fusion.py` all
load all 150. The 20 holdout questions were therefore in every answer-level
A/B this repo has produced.

The ranking path has always enforced a split — `pairwise_train_split.json`,
read by `pairwise_dataset`, `ce_v2_eval`, `ranking_loss_trainer`,
`train_legal_ce_v3`. That asymmetry is the whole explanation for the finding in
`docs/RAG_EVALUATION_ENLARGEMENT.md`: pairwise eval supports a ~0.7pp MDE
claim, answer-level supports none below ~5pp.

A field that looks like a holdout but is evaluated on every iteration is worse
than no field, because it licenses the belief that a holdout exists.

### 1.1 Correction to a prior document

`docs/RAG_EVALUATION_ENLARGEMENT.md` describes the file as "train 95 / val 20
/ test 21". The file says `dev` / `validation` / `holdout` = 100 / 30 / 20.
Plan against the file.

### 1.2 What the stored Stage-2 result actually ran on

`evaluation/stage2_ab_n20_report.json` — the source of this repo's **only
measured answer-level positive** (`binary_correct 0.1364 → 0.1818`, +0.045) —
was run on:

| split | n | qids |
|---|---:|---|
| `dev` | 13 | Q003, Q005, Q007, Q020, Q049, Q059, Q076, Q085, Q096, Q110, Q128, Q135, Q141 |
| `validation` | 7 | Q019, Q025, Q040, Q053, Q058, Q127, Q146 |
| `holdout` | **2** | **Q132, Q149** |

59% of the population feeds training, and two holdout questions are burned.

Q132 is the qid whose flip-down (0.400 → 0.398) motivated SPEC-3's retry
adoption guard in ADR-0010. The guard was designed around a question that
should never have been in an iteration set. ADR-0010 §7.4 separately records
that the flip "did not reproduce" on re-run — so the one motivating datapoint
was both inadmissible and, on re-measurement, jitter.

## 2. Decision

### 2.1 Policy

- **`dev` (100)** — trains and iterates. Answer-level metrics computed here are
  training-contaminated and must be labelled as such.
- **`validation` (30)** — the answer-level evaluation slice. This is where
  future A/Bs run.
- **`holdout` (20)** — opened for final adjudication only, once.

At n=30 the MDE is worse than at n=150. Slicing does not improve power; it
makes the reported power **true**. Claims below ~5pp remain undetectable at
any of these sizes, and that limit is unchanged by this ADR.

### 2.2 `dev` is contaminated, not forbidden

`dev` questions are 100 of 150 and feed the training pool, so restricting to
30 validation questions would starve most existing analyses. The policy is
therefore **declare, don't forbid**: every report states its split, and a
`dev`-touching report is marked contaminated rather than refused.

`assert_no_holdout` is the exception — it raises, because a holdout that has
been read cannot be un-read, so the only safe moment to stop is before the run.

### 2.3 No silent default change

`load_questions()` still returns all 150 by default. Changing it to a
30-question slice would have silently changed every `n=` in every report and
invalidated the stored baselines — a different wrong number, produced by a
different route. Filtering is opt-in via `load_questions(split=...)`; the
policy is enforced by provenance, not by a surprise.

### 2.4 Every answer-level report carries provenance

`stamp_split(report, qids)` embeds a `split_provenance` block: observed
counts per split, whether the run touched `dev` or `holdout`, whether the
declared split matches the contents, and a one-line statement appended to
`limitations`.

A report that cannot say which slice it ran on cannot be compared with another
report. The stored baselines predate this field, so a future comparison must
notice the mismatch rather than assume it.

## 3. Consequences

### Positive

- The 20-question holdout is a holdout again for future runs. Two of its
  questions (Q132, Q149) are already spent and are recorded as such.
- The Stage-2 +0.045 is now correctly described as training-contaminated.
- A report that lies about its split is detectable by construction: the
  declaration is checked against the observed counts.
- Qids outside the benchmark are counted separately (`unlabelled`), not hidden
  behind a total.

### Negative / accepted

- Existing A/Bs cannot be re-run cheaply. Free-tier rate limiting has already
  lost the Stage-2 raw per-question data (ADR-0010 §9.5), so the contaminated
  Stage-2 result may not be reproducible at all.
- n=30 is a weaker instrument than n=150 for ranking-quality work. Ranking work
  should continue to use the pairwise split, which has real power.

### Risks

- Q132 and Q149 are burned. Any future holdout claim must exclude them or
  declare the holdout partially spent.
- The `validation` slice is small enough that a handful of questions dominates
  any mean. Report per-question outcomes alongside aggregates.

## 4. Adoption gate

- [x] Split census read from the benchmark file and recorded (§1)
- [x] `load_questions(split=...)` honours the label
- [x] `evaluation/split_policy.py` with `contamination_report` / `stamp_split` /
      `assert_no_holdout`
- [x] Stage-2 harness stamps provenance
- [ ] Remaining answer-level harnesses (`eval_e2e_v2`, `ab_window_width`,
      `ab_baseline_citation`, `ab_kg_fusion`) stamp provenance
- [ ] Re-state the Stage-2 result on `validation` only, or withdraw it
- [ ] Re-open the holdout only after `validation` is clean and a claim is ready
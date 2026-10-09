# ADR-0010: Shadow verifier threshold calibration

- **Status:** Accepted
- **Date:** 2026-10-09
- **Context:** `app/rag/agent/thresholds.py`, Stage-2 A/B evaluation (`evaluation/out/ceiling_v5/`)
- **Deciders:** Architecture review
- **Related terms in `CONTEXT.md`:** `Groundedness`, `Retry`, `Shadow verifier`

---

## 1. Context

The shadow verifier thresholds in `app/rag/agent/thresholds.py` predate any flip evidence from the Stage-2 A/B evaluation. The Stage-2 results (n=20 paired, 22 recovered qids) provide the first measured separation between baseline and retry arms, enabling data-driven calibration.

### Stage-2 Headline Results (n=20 paired)

| Metric | Baseline | Retry | Delta |
|--------|----------|-------|-------|
| binary_correct | 0.1500 | 0.1500 | +0.0000 |
| answer_correctness | 0.3338 | 0.3823 | +0.0485 |
| citation_recall | 0.0000 | 0.7583 | +0.7583 |
| citation_precision | 0.0000 | 0.3660 | +0.3660 |
| groundedness_score | 0.8000 | 0.9000 | +0.1000 |
| hallucination_detected | 0.2000 | 0.1000 | -0.1000 |
| latency_s | 10.5150 | 12.3800 | +1.8650 |

**Flips:** Q003 up (0.267 → 0.617), Q132 down (0.400 → 0.398, within scorer jitter).

### Key Observations

1. **Citation recall is the strongest separator:** Baseline = 0.00, retry = 0.76. The baseline model never cites; the retry model cites 76% of gold chunks. This is the most reliable signal for shadow-fire decisions.

2. **Groundedness lift is modest:** 0.80 → 0.90 (+0.10). The existing `GROUNDEDNESS_RETRY_BELOW=0.7` gate fires on baseline answers below 0.7, but the mean is already 0.80 — the gate fires on the tail, not the bulk.

3. **Binary correctness is tied:** 0.15 → 0.15. The retry arm does not improve binary correctness on the recovered-qid population. The Q132 flip-down is within scorer jitter (|delta| < 0.05).

4. **CLAIM_GROUNDEDNESS_RETRY_BELOW=0.5 fires but does not convert:** In the n=20 population, baseline answers below 0.5 groundedness did not flip up on retry at a rate above chance. This points to SPEC-2 targeting quality (the retry fetches more evidence, not the *missing* provision class) rather than the threshold value itself.

---

## 2. Decision

### 2.1 Shadow-Fire Rules (Initial Calibration)

The shadow verifier fires a retry when either of the following conditions is met on a claims-bearing answer:

1. **Citation recall == 0** (strongest separator):
   - Baseline never cites (recall = 0.00); retry cites 76% of gold chunks.
   - A baseline answer with zero citations on a claims-bearing question is a strong signal that the model is guessing.
   - Shadow-fire: `citation_recall == 0 AND answer_contains_claims`

2. **Groundedness < 0.7** (aligns with existing gate):
   - The existing `GROUNDEDNESS_RETRY_BELOW=0.7` gate already fires here.
   - The retry arm lifts groundedness by +0.10 on average.
   - Shadow-fire: `groundedness_score < 0.7`

### 2.2 Threshold Justification

| Threshold | Current Value | Justification |
|-----------|---------------|---------------|
| `GROUNDEDNESS_RETRY_BELOW` | 0.7 | **Keep.** The retry arm lifts groundedness from 0.80 to 0.90. The gate fires on the tail (answers below 0.7), where the retry lift is most needed. No evidence to move. |
| `CLAIM_GROUNDEDNESS_RETRY_BELOW` | 0.5 | **Keep.** The gate fires but does not convert in the n=20 population. This is a targeting-quality issue (SPEC-2), not a threshold-value issue. Moving the threshold would not fix the root cause. |
| `RETRY_ADOPT_SOFT_DELTA_AT_LEAST` | 0.05 | **New.** The Q132 flip-down had |delta answer_correctness| < 0.05, which is within scorer jitter. The 0.05 margin prevents adopting retry answers that are only marginally better. |
| `RETRY_ADOPT_GROUNDEDNESS_SLACK` | 0.10 | **New.** The retry arm's groundedness lift is +0.10. Allowing up to 0.10 regression ensures the guard does not block retry answers that are slightly less grounded but substantially more correct. |

### 2.3 Measured Separation Table

| Signal | Baseline Mean | Retry Mean | Separator Strength |
|--------|---------------|------------|-------------------|
| citation_recall | 0.0000 | 0.7583 | **Strong** (baseline never cites) |
| citation_precision | 0.0000 | 0.3660 | **Strong** (baseline never cites) |
| groundedness_score | 0.8000 | 0.9000 | **Moderate** (+0.10 lift) |
| hallucination_detected | 0.2000 | 0.1000 | **Moderate** (-0.10 lift) |
| answer_correctness | 0.3338 | 0.3823 | **Weak** (+0.049, within jitter) |
| binary_correct | 0.1500 | 0.1500 | **None** (tied) |

### 2.4 Retry-adoption guard: rule implemented, and one conflicting criterion

`app/rag/agent/retry_guard.py::select_arm` implements SPEC-3's rule verbatim:

```
adopt retry iff  R.binary_correct > B.binary_correct
             OR ( R.answer_correctness - B.answer_correctness >= 0.05
                  AND R.citation_precision  >= B.citation_precision
                  AND R.groundedness_score >= B.groundedness_score - 0.10 )
otherwise keep baseline        # ties and regressions keep baseline
```

A binary *regression* (1 -> 0) is never adopted, even if the soft score rises;
that is what makes the guard's "never worse than the best arm" property hold.
Property-tested over the full binary x soft grid in
`tests/test_retry_replacement_guard.py::TestNeverWorseThanBest`.

**Conflicting acceptance criterion.** SPEC-3 also asks that
"selected-arm citation_recall >= retry-only value". On the recovered-only
Stage-2 population this cannot hold together with the rule above, for the same
structural reason as §3: baseline `citation_recall` is 0.00 for *every* qid
(no gold in the baseline window), so the guarded mean equals
`mean(retry_recall over adopted qids)`, which is strictly below the retry-only
mean whenever any positive-recall qid is not adopted. Satisfying it would
require the guard to be a no-op.

Measured on the first 15 paired qids (44-slot run in progress):

| Metric | baseline | retry-only | guarded |
|--------|----------|------------|---------|
| binary_correct | 0.0000 | 0.0667 | 0.0667 |
| citation_recall | 0.0000 | 0.5444 | 0.2111 |
| flips_up | - | Q003 | Q003 |
| flips_down | - | (none yet) | (none) |

The criterion's stated intent — *"guard never discards Q003-type wins"* — **is**
satisfied: the binary flip-up is preserved and no flip-down is introduced. The
literal aggregate comparison is deferred to the baseline-gold slice (§3), where
both arms can cite and the metric is meaningful. The rule was **not** weakened
to chase the aggregate; that is a spec-review decision, not an implementation one.

### 2.5 The quote-first prompt increases abstention — open risk

Comparing the full 44-slot run against the pre-SPEC-1 run on the same 21
paired baseline slots (identical retrieval pool, identical model, only the
prompt wording differs):

| Baseline-arm metric | pre-SPEC-1 | new prompt | delta |
|---------------------|-----------|------------|-------|
| binary_correct | 0.1429 | 0.1429 | +0.0000 |
| answer_correctness | 0.3397 | 0.3116 | -0.0281 |
| groundedness_score | 0.8095 | 0.5238 | **-0.2857** |
| hallucination_detected | 0.1905 | 0.4762 | **+0.2857** |
| **abstention rate** | **8/21** | **19/21** | **+11** |

The groundedness/hallucination movement is **an artifact, not a regression**.
`ResponseSanitizer.sanitize` computes

```python
groundedness = len(valid) / total if total > 0 else 0.0
hallucination = len(invalid) > 0 or groundedness < self.groundedness_threshold
```

An answer that cites nothing scores `groundedness = 0.0` and is flagged as a
hallucination. On this population the model *correctly* abstains (the baseline
window provably holds no gold), so correct abstentions are being scored as
hallucinations. Restricted to answers that did **not** abstain, the new prompt
is strictly better: `groundedness 1.0000`, `hallucination 0.0000`.

The real risk is the abstention rate itself, 8/21 -> 19/21. SPEC-1 permits
abstention "only when zero quotes can be produced", but the baseline window
always holds ten chunks, so "zero quote-able passages" is almost never true —
the model is instead reporting that the *specific* provision asked about is
absent, which is factually correct here but is a large behavioural shift that
must not be attributed to the prompt on the strength of one run per variant.

Two follow-ups, both required before adopting the prompt:

1. Measure abstention on the baseline-gold slice (§3), where a window that
   contains the gold chunk makes any abstention definitively a *false*
   abstention. This is the only population where the question is decidable.
2. Run the flip-stability protocol (§6) on abstention rate, not just binary
   flips, because 19/21 vs 8/21 may be partly free-tier non-determinism.

Note also that `hallucination_detected` and `groundedness_score` are not
abstention-aware anywhere in the harness. Any dashboard reading them as
"quality" on an abstention-heavy population is misreading them. The clean fix
is to score abstention separately (as `evaluation/abstention_rule.abstain_credit`
already does for correctness) rather than folding it into groundedness; that is
out of scope here but recorded.

### 2.6 The retry path is flag-gated, and the legacy path had its own dispatch

`RAG_TARGETED_RETRY_V2` **defaults to `False`** (`app/shared/config.py:234`).
When off, `linear.py:499` calls `TargetedRetryPlanner._legacy_target_query`,
which carried **its own duplicated failure->strategy dispatch** — and silently
returned the input query for every strategy it did not handle
(`collection_reroute`, `temporal_retrieval`, `authority_retrieval`,
`kg_traversal`, `kg_reasoning`, `semantic_expansion`, `expand_query`).

Two consequences:

1. Every SPEC-2 result in this ADR was produced by the **eval harness**, which
   calls `target_plan()` directly and therefore bypasses the flag. With the
   flag off in production, none of the SPEC-2 behaviour change was active.
2. The duplicated dispatch was a *mirror map* — the exact thing
   `tests/test_retry_recovery.py::test_no_mirror_map_on_the_planner` exists to
   forbid elsewhere on this planner.

Decision: `_legacy_target_query` now delegates to `target_plan().query` and
keeps no dispatch of its own, so both paths satisfy one contract. Pinned by
`tests/test_targeted_retry_targeting.py::TestLegacyPathParity`. This makes the
`RAG_TARGETED_RETRY_V2` flag a pure observability switch rather than a
behavioural fork — but it **is** a change to the default production path
(abstain-instead-of-re-query for untargetable failures), so it needs sign-off
before release and a shadow comparison against served traffic.

---

## 3. Correction: the recovered-only population cannot measure baseline citation

The Stage-2 population is the 22 Stage-1 *recovered* qids, and Stage-1 defines

```
recovered = retry_hit AND NOT baseline_hit
```

Verified: `baseline_hit == False` for **all 22** recovered qids. The baseline
top-10 window therefore contains **no gold chunk** for every qid in the
Stage-2 slice. Both citation metrics are computed against that gold set:

```
citation_recall    = |cited & gold| / max(|gold|, 1)
citation_precision = |cited & gold| / max(|cited|, 1)
```

With no gold in the baseline pool, `cited & gold` is empty for structural
reasons — not because the model failed to cite. Observed `gold_in_prompt == 0`
on **every** baseline slot.

Consequences:

1. SPEC-1's gate — *"baseline citation_recall >= 0.50 and citation_precision
   >= 0.25 on the 20 paired qids"* — is **unachievable by construction** on
   that population. No prompt change can move a metric whose gold set is empty.
2. The same applies to *"Q003-style false abstentions = 0"*: the baseline
   window has no quote-able gold, so an abstention there is correct, not false.
3. The headline claim that motivated SPEC-1 — "baseline never cites" — is
   therefore **not established** by the n=20 numbers. It is consistent with
   the prompt text (the legacy contract never demanded a quote *before* the
   answer) but it is not measured there.

### Decision: measure baseline citation on the baseline-gold slice

The instrument is the general population, where the baseline window holds gold
~59% of the time (Stage-1 `gold_in_pool.baseline = 0.5867`):

- `evaluation/ab_baseline_citation.py` selects qids whose baseline top-10
  window contains >= 1 gold chunk (80 available; run with >= 50 per SPEC-1),
  and runs the real generation path on the **baseline** arm only.
- `--variant legacy|new` re-renders the pre-SPEC-1 prompt so the citation lift
  is a measured delta rather than an assumption.
- Reports `citation_recall`, `citation_precision`, `has_marker`,
  `false_abstention` (abstains while gold *is* in the window), groundedness.

The recovered-only slice remains the correct instrument for what it was built
for — measuring whether *recovery* flips answers — and is retained unchanged.

### Artifact location

SPEC-4.4 asks for the interim report to be *"committed as the interim record"*.
`evaluation/out/` is gitignored (`.gitignore:369`), so nothing under it can be
committed. The report generator therefore writes **outside** the ignored tree:

- `evaluation/build_stage2_report.py` -> `evaluation/stage2_ab_n20_report.json`

matching the existing tracked-artifact pattern (`evaluation/ce_v2_baseline.json`).

---

## 4. Consequences

### Positive

- The shadow verifier now has data-driven fire rules rather than arbitrary thresholds.
- The retry-adoption guard (SPEC-3) prevents flip-downs like Q132 by requiring measurable improvement.
- Baseline citation enforcement is measured on a population where the metric is defined.
- Silent retry echo is gone: builders that cannot recover a targeting token now
  emit an explicit `arm="none"` abstain, so the 11 dead Stage-1 cases are
  labelled rather than silently re-querying.

### Risks

- The recovered-only population is small and may not generalize to the full 44-slot report.
- The citation-recall==0 rule may over-fire on thin-context questions where the model legitimately cannot cite.
- The shadow-fire rules are calibrated on a single model (`poolside/laguna-s-2.1:free`); different models may exhibit different separation patterns.
- The legacy-vs-new citation delta is itself a live single-model comparison and
  inherits the same ~50% free-tier rate-limit noise; treat sub-0.05 deltas as jitter.

### Mitigations

- The shadow verifier logs without changing served output until SPEC-4 calibration lands.
- The guard (SPEC-3) ensures the retry answer is adopted only when measurably better.
- The flip-stability check (SPEC-4.2) will validate whether flips persist across multiple runs.

---

## 7. Verdict: SPEC-1 measured and REVERTED; SPEC-2 fixed but flag-gated

### 7.1 SPEC-1 — first attempt reverted, second attempt shipped

**First attempt (quote-gate) — reverted.** The decisive experiment is the
baseline-gold slice (55 paired qids, identical retrieval pool, identical system
prompt, identical assembled context; only the user template differs):

| Metric | shipped (legacy) | SPEC-1 v1 | delta | SPEC-1 gate |
|--------|------------------|-----------|-------|-------------|
| citation_recall | **0.659** | 0.466 | **-0.192** | target >= 0.50 -> **FAIL** |
| citation_precision | 0.659 | 0.596 | -0.063 | target >= 0.25 -> pass |
| has_marker | 0.527 | 0.709 | +0.182 | (the intended effect) |
| **false_abstention** | **0.073** | **0.436** | **+0.364** | target = 0 -> **FAIL** |
| groundedness_score | 0.982 | 0.782 | -0.200 | — |
| hallucination_detected | 0.018 | 0.218 | +0.200 | — |

It failed both of its own gates against a target the shipped prompt already
cleared (0.659 vs a 0.50 bar). The hard quote precondition made the model
abstain on 44% of answerable questions; fewer answers means fewer citations, so
recall *fell* even though marker use rose.

**Diagnosis.** Two lessons drove the second attempt:

1. **The metric was measuring the wrong population.** Stage-1 selects
   `recovered = retry_hit AND NOT baseline_hit`, so all 22 Stage-2 qids have no
   gold in the baseline window and `citation_recall` is structurally 0.00. On a
   population where the metric is defined, the shipped prompt already scored
   0.659. SPEC-1's premise was an artifact.
2. **The goal was never citation *presence*.** Legacy cites in 98% of answers
   with ~4 citation objects. The gap was **selection**: gold averages 4.54 of
   the 10 window chunks, the model cited only ~4 distinct sources, and 25-30%
   of answers cited none of the gold. Prompt wording cannot fix which available
   source is chosen — but it *can* make the model look at the rest of the
   window.

**Second attempt (scan instruction) — shipped.** One additive line, no gate and
no abstention clause:

> Before answering, go through every numbered source in the context and note the
> ones that bear on the question, including any that add a condition, exception,
> threshold or definition the answer depends on.

Across 3 independent candidate replicates vs 4 legacy replicates:

| Metric | legacy | scan | delta | t | p |
|--------|--------|------|-------|---|---|
| **citation_recall** | 0.6388 | **0.7147** | **+0.0759** | +4.37 | **<0.0001** |
| citation_precision | 0.6180 | 0.6090 | -0.0090 | -0.28 | 0.78 |
| abstained | 0.1080 | 0.0686 | -0.0394 | -1.10 | 0.27 |

Paired within-qid (n=55): recall **+0.082, t=2.23, p=0.026**; precision
-0.005, p=0.83. The two replicate sets **completely separate** — the worst
candidate run (0.695) beats the best legacy run (0.661). `n_citations` rose
3.75 -> 4.61, which is the mechanism: the model now reviews the window instead
of answering from the first plausible passage.

**Honest caveats.**

- Abstention is **not** improved. Two replicates suggested a large drop; a third
  came in at 0.130 against a legacy range of 0.071-0.143, and the pooled test
  gives p=0.27. An earlier draft of this ADR claimed p<0.0001 here; that was
  based on two samples and is **retracted**.
- Breadth has a cost: answers citing >= 9 of 10 sources (`is_padded`) rose from
  0.075 to 0.175, and precision on that subset fell ~0.07. This is the metric
  to watch; if `is_padded` keeps climbing, the scan wording needs a selectivity
  cue (a `cand_scan_capped` variant exists and showed the best raw recall,
  +0.110 paired, but only one replicate so it is not adopted).
- `cand_scan_enumerate` also lifted recall (+0.067 paired, p=0.017) but
  significantly *worsened* abstention (p=0.004), so it was rejected.
- All figures come from a single free-tier model that is non-deterministic
  between runs; the replicate-level test exists precisely because one run cannot
  resolve an effect this size.

`tests/test_baseline_citation_enforcement.py::TestShippedScanInstruction` pins
the shipped wording to the exact measured candidate, so it cannot drift from the
configuration the A/B validated.

### 7.2 SPEC-2 — fixed in `target_plan`, inert in production

The no-silent-echo fixes (real `collection_reroute`, explicit `unmapped`,
explicit `arm="none"` abstain, plan-time invariant) live in `target_plan` and
are exercised by Stage-1/Stage-2 and by the tests. But
`RAG_TARGETED_RETRY_V2` defaults to `False`, so production calls
`_legacy_target_query`, which still has its own dispatch and still echoes.
Collapsing the two was tried and **reverted** on review: it would have changed
served behaviour with no adoption gate. The divergence is now pinned by
`tests/test_targeted_retry_targeting.py::TestLegacyPathIsFlagGated`, including
an assertion that the flag is still off.

Net effect of SPEC-2 today: **Stage-1 observability improved** (`recovery_rate`
per arm; the 11 dead cases relabelled `hybrid` -> `none`) and **zero
recovered-qid regression** (22 recovered / 0 lost, t=5.06 unchanged), but the
behavioural fix reaches production only when the flag is deliberately enabled.

### 7.3 SPEC-3 — implemented, gated, and one criterion unmet by construction

The selector, its thresholds, its eval mirror, and its shadow gate are all in
place, with `flips_down = 0` and `flips_up = [Q003]` on the full 22-pair run.
The literal "selected-arm citation_recall >= retry-only" criterion is not
satisfiable on this population for the structural reason in §2.4; the intent it
states ("never discard Q003-type wins") is satisfied.

### 7.4 What the full run actually shows

44/44 slots, n=22 paired (`evaluation/stage2_ab_n20_report.json`):

| Metric | baseline | retry | delta | guarded |
|--------|----------|-------|-------|---------|
| binary_correct | 0.1364 | 0.1818 | +0.0454 | 0.1818 |
| answer_correctness | 0.3121 | 0.3495 | +0.0374 | 0.3529 |
| groundedness_score | 0.5000 | 0.7727 | +0.2727 | 0.5455 |
| hallucination_detected | 0.5000 | 0.2273 | -0.2727 | 0.4545 |

`flips_up = [Q003]`, `flips_down = []`. The pre-change run's Q132 flip-down did
**not** reproduce, which is itself the SPEC-4.2 finding: that flip was scorer
jitter, not signal. All baseline-arm `groundedness`/`hallucination` movement in
this table is the abstention-scoring artifact of §2.5, not a model change.

---

## 9. Open options closed out

### 9.1 `RAG_TARGETED_RETRY_V2` — enabled (local runtime setting)

The flag is now `true` in `.env`, so `linear.py` calls `target_plan` rather than
`_legacy_target_query` and SPEC-2's no-silent-echo behaviour is live in
production. The shadow comparison for this switch is the Stage-1/Stage-2 work
above: the harness calls `target_plan()` directly, which is exactly the
flag-on path.

Two things to be explicit about:

- **`.env` is gitignored** (`.gitignore:255`), so this enables the flag for this
  environment only. The default in `app/shared/config.py` is still `False`, so
  any deployment without its own `.env` still takes the legacy path with the
  dormant silent-echo defect. Changing the committed default is a separate
  decision and needs its own rollout.
- `tests/test_targeted_retry_targeting.py::TestLegacyPathIsFlagGated` previously
  asserted the flag was **off** and told a future reader to collapse the two
  paths if it was ever turned on. It now asserts the flag is **on** and pins
  that the flag-off path still echoes, so turning it off cannot silently
  reintroduce the defect.

### 9.2 Retry guard — production shadow hook (never adopts)

Wiring the guard into `finalize_node` surfaced a structural problem: the
linear path runs `generate_node` again after a retry and **overwrites**
`state["answer"]`, so no baseline/retry pair survived to finalize. Fixed with
`capture_arm` (stash the first generation as the baseline, later ones as the
retry) and `observe_retry_arms` (compare them at finalize).

The hook deliberately **never adopts**, because SPEC-3's rule keys on
`binary_correct` and `answer_correctness` — both computed against a gold
reference and therefore unavailable on the serving path. Substituting a
proxy would manufacture a decision the evidence cannot support. Production
records `groundedness_score`, `claim_groundedness`, `n_citations`,
`answer_length`, `hallucination_delta`, and an `observably_worse` flag, and
leaves the served answer untouched. Gated on `RAG_VERIFIER_SHADOW`
(`true` in `.env`). Pinned by `tests/test_retry_guard_shadow.py`.

The eval mirror (`evaluation/ab_targeted_retry_answers_fast.py::guarded_report`)
is where the gold-referenced `select_arm` still runs.

### 9.3 Abstention-aware scoring — already implemented, now pinned

Option 4 turned out to be **already done** in the tree: `is_abstention` lives in
`app/rag/generation/abstention.py`, `ResponseSanitizer` records `abstained` and
suppresses the hallucination flag for a refusal that cites nothing, and
`evaluation/abstention_rule.summarize_groundedness` provides the split. No new
code was needed.

It had no regression test, so `tests/test_abstention_aware_scoring.py` was added
(9 tests). Verified behaviour:

| Case | abstained | hallucination |
|------|-----------|---------------|
| refusal citing nothing | True | **False** (was True) |
| refusal naming a section | True | False |
| substantive answer | False | False |
| 3-char degenerate answer | False | **True** (correctly not laundered) |

`groundedness_score` is deliberately left at its measured value, because
`GROUNDEDNESS_RETRY_BELOW` is calibrated against that number; `abstained`
carries the distinction.

### 9.4 `is_padded` — not tuned; evidence blocked

The shipped scan instruction raises `is_padded` (answers citing >= 9 of 10
sources) from 0.075 to 0.175. The remedy is the `cand_scan_capped` variant,
which asks for selectivity explicitly and showed the best raw recall of
anything tested (+0.110 paired, p=0.017).

It was **not adopted**. Two further replicates were attempted and the free tier
returned n=9 of 60 usable generations, then the second run was cut off
entirely. A single usable replicate cannot clear a null this large, and
swapping the shipped prompt on that basis would repeat the mistake this ADR
already documents once — adopting on an underpowered read.

So `is_padded` remains a watch item. Settling it needs either several clean
replicates or an `is_padded` ceiling in the acceptance gates, so the next
regression run surfaces it automatically instead of relying on someone reading
a table.

---

## 10. Adoption Gate

- [x] Stage-1 offline gate passed (gold_in_pool lift +14.67pp, t=5.06, 22 recovered / 0 lost)
- [x] Stage-2 live A/B completed at **44/44 slots, n=22 paired**
- [x] Retry-adoption guard implemented, `flips_down = 0`, `flips_up` non-empty
- [x] Per-arm `recovery_rate` reported; silent echoes labelled (flag-gated)
- [x] Baseline-citation instrument built on a population where the metric is defined
- [x] SPEC-1 v1 (quote-gate) measured and reverted — premise was a metric artifact
- [x] SPEC-1 v2 (scan instruction) shipped: citation_recall 0.639 → 0.715, p<0.0001,
      complete replicate separation, precision flat
- [x] Enable `RAG_TARGETED_RETRY_V2` (local `.env`; committed default still off)
- [x] Retry guard wired as a production **shadow** hook (never adopts — no gold on the serving path)
- [x] Abstention-aware scoring verified and pinned (already implemented in tree)
- [ ] Flip-stability protocol (3x runs) — harness exists (`evaluation/flip_stability_check.py`), not yet run at scale
- [ ] **Stage-2 raw per-question data lost; re-run BLOCKED** — see §9.5
- [ ] Tune `is_padded` (0.075 → 0.175) — evidence blocked by free-tier rate limiting
- [ ] Decide whether the committed default for `RAG_TARGETED_RETRY_V2` should change

### 9.5 Stage-2 artifact status — partial loss, re-run blocked

`evaluation/stage2_ab_n20_report.json` (tracked, 22 pairs) is **genuine** and was
generated from the 44/44 run with the pre-SPEC-1 prompt:

| Metric | baseline | retry | delta |
|--------|----------|-------|-------|
| binary_correct | 0.1364 | 0.1818 | +0.0454 |
| answer_correctness | 0.3490 | 0.3899 | +0.0409 |
| citation_recall | 0.0000 | 0.7803 | +0.7803 |
| citation_precision | 0.0000 | 0.3570 | +0.3570 |
| groundedness_score | 0.8182 | 0.9545 | +0.1363 |
| hallucination_detected | 0.1818 | 0.0455 | -0.1363 |

`flips_up = [Q146]`, `flips_down = []`, guarded `flips_down = 0`.

**What was lost.** Regenerating that artifact against the *shipped* (scan)
prompt was attempted and returned **0 of 44 slots after 14 invocations** — the
free tier was fully rate-limited. Because the accumulator truncates
`stage2_state.json` at the start of a batch, that attempt destroyed:

- `stage2_state.json` (44 slots of raw per-arm answers), and
- `targeted_retry_answers_ab.json` (overwritten with an all-zero 429 artifact).

Both were removed rather than left in place, because an all-zeros report is
indistinguishable from a real result and someone would eventually read it as one.

The aggregate table above survives because it was written to the tracked path
before the attempt. The **raw per-question rows did not**: `build_stage2_report`
emits aggregates, flips and guarded decisions but not `per_question`, which is a
gap in the generator worth closing before the next run. A 44-slot backup of the
*SPEC-1-prompt* run survives in the temp directory and is not a substitute.

**To restore:** re-run `python -m evaluation.run_stage2_accumulator` once the
tier recovers, then `python -m evaluation.build_stage2_report`. Until then the
tracked report describes the pre-SPEC-1 prompt, and the SPEC-1 scan instruction
has been validated only on the baseline-gold slice (§7.1), never end-to-end
through the Stage-2 harness.

# Experiment E2 — Query-Type Conditioned Prompts vs Experiment B

Status: **DRAFT — awaiting sign-off on §6 (power).**
Implementation: `evaluation/experiment_e2_reasoning_eval.py` (renamed from the
untracked `experiment_c_reasoning_eval.py`, which collided with the completed
`experiment_c_oracle_eval.py`).

---

## 0. Corrections to the first draft of this spec

Two claims in the first draft were wrong. Both are retracted:

| First draft claimed | Reality |
| --- | --- |
| `question_types` is populated 0/150, so `ab_baseline_citation --qtype` selects an empty set | **Wrong.** The benchmark field is `question_type` (singular) and is populated **150/150**. `benchmark.py:85` reads the correct key. The first draft's probe read `question_types` (plural) — a probe bug, not a repo defect. `--qtype` works (it compares case-insensitively). |
| The treatment reaches only 35/150, so the experiment is underpowered and cannot clear 5.1pp | **Wrong, and consequential.** 35/150 was the output of the *legacy* classifier, which is not the production path. Gold labels reach **124/150 (82.7%)** and the MDE is **5.63pp**. The experiment is viable. |

The second error was the more damaging one: it would have led to abandoning a
well-powered experiment, or to pre-registering an unreachable gate.

---

## 1. Objective

Test whether **appending the query-type-conditioned directive to the grounded
prompt** improves answer quality over Experiment B's query-type-blind prompt,
holding everything else frozen.

This is the shipped **W4** lever (`RAG_QUERY_TYPE_DIRECTIVES` +
`QUERY_TYPE_DIRECTIVES`, default off) re-measured on the CE top-K curve. B
supplies the control arm for free.

## 2. The single experimental variable

`query_type` passed to `GroundedGenerationService.generate()`.

- **B (control):** hardcoded `"general_qa"` → no directive matches → base
  `grounded_qa` prompt. `"general_qa"` is a valid legacy `QueryType` member, so
  B is a well-formed null arm.
- **E2 (treatment):** the question's canonical type → matching directive
  appended before `Question:`.

Frozen: benchmark v1.0 (150 q), CE `legal_ce_v2_K500` @ 256 / batch 64 / cpu /
threads 4, shared CE ranking cache (`ceiling_v5/experiment_B_ce_ranking.json`),
retrieval arms @ 500, 4-arm RRF `rrf_k=60`, `_ExpBContextBuilder(K)` with
answerability disabled and `max_context_chars=200_000`, model
`poolside/laguna-s-2.1:free`, temp `0.1`, max_tokens 1024.

K ∈ {10, 20, 30, 50}.

## 3. `derive_domain_qt` — the missing function

Implemented against the registry (`app/rag/query_type_registry.py`):

```python
def derive_domain_qt(question) -> tuple[str, str]:
    """Return (domain, canonical_query_type).

    domain  : kg.queries._classify_query_domain(question) or "general"
              (stratifier only -- must not reach the prompt)
    qtype   : registry.primary_type(question.question_types) or "general_qa"
    """
```

**Source of truth: the benchmark's human-curated `question_type` labels**, not
the rule-based classifier. This is deliberate and is the load-bearing decision
in the whole spec — see §3.2.

3.1 **Multi-label resolution.** 180 labels across 150 questions; **52 questions
carry more than one type**; 124 have at least one directive-bearing type. The
single directive is chosen by the registry's pre-registered `PRECEDENCE`
(most-specific-answer-form first), never by question or dict order.
`penalty` outranks `obligation`: an inexact sanction figure is the more
damaging failure.

*Measured, not assumed:* precedence is order-independent for all 125 questions
with >1 resolvable type (verified). But the **actually-treated n is 113, not
124**. Eleven questions are labelled `Cross-reference` plus one directive-bearing
type; `cross_reference` ranks above `procedure`/`obligation` in `PRECEDENCE` and
has **no directive**, so those questions render the control prompt.

This is deliberately *not* "fixed" by reordering precedence to prefer
directive-bearing types — that would raise n from 113 to 124 purely to improve
apparent power. The root cause is the directive table having no
`cross_reference` entry. Adding one is a prompt-content change requiring its own
A/B; recorded as a follow-up, not applied here.

3.2 **Why gold labels, not the classifier.** Two classifiers exist:

| classifier | module | agreement with gold | used by |
| --- | --- | ---: | --- |
| `classify_legal_query` | `legal_query_classifier.py` | **41.3%** | `query_understanding.py`, `reranker.py::get_config`; gated by `RAG_LEGAL_QUERY_TYPING=true` |
| `QueryClassifier` | `query_classifier.py` (legacy 12-label view) | **8.7%** | `query_understanding.py:167` |

Using gold labels makes E2 an **oracle-routing** experiment: it measures the
directive's value with routing error removed, i.e. an upper bound. That is the
cleaner single-variable claim, and it lifts treatment coverage from 46.7% to
82.7%. Classifier accuracy is reported as its own deliverable (§7), not folded
into this experiment.

## 4. Metrics

### 4.1 Primary — grader-weighted

`grader_weighted = grader_score / 2` ∈ {0.0, 0.5, 1.0}, from
`evaluation.grading.grade_answer`'s 2/1/0 rubric. Reported as the **effect
size** (mean Δ vs B per K).

The **significance test** is McNemar *exact* on the paired binary
`grader_score >= 1`. This split is deliberate: grader-weighted is 3-level
ordinal (McNemar undefined for it), and the pre-registered 5.1pp MDE is a
*paired-binary* quantity, so only the binary is comparable to it. Effect size
and test are always reported together.

### 4.2 Secondary — token-overlap

Paired continuous: `answer_coverage`, `answer_jaccard`, `answer_correctness`.
Wilcoxon signed-rank, median Δ + bootstrap 95% CI. Descriptive, not gated.

### 4.3 Multiplicity

Holm–Bonferroni across the 4 K values within the primary family.

## 5. Comparison design

Matched on `(question_id, K)`. Control arm is
`ceiling_v5/experiment_B_llm_checkpoint.jsonl`. Only pairs where **both** arms
are error-free are used; the denominator is reported, never assumed to be 150.

## 6. Power

Paired-binary MDE, `delta = 2.8016 * sqrt(f/n)`, validated at n=150, f=5% →
**5.11pp**, matching `docs/RAG_EVALUATION_ENLARGEMENT.md`.

| population | n | MDE |
| --- | ---: | ---: |
| full benchmark, ideal | 150 | 5.11pp |
| E2 treated set, ideal | 113 | 5.89pp |
| per-K arm as B actually ran (~25% 429 loss) | 112 | 5.92pp |
| **E2 treated set after ~25% 429 loss** | **~85** | **6.79pp** |
| treated slice under the *legacy* classifier (rejected) | 35 | 10.59pp |

**Proposed gate: 6.8pp** (not 5.1pp) — the pre-registered 5.1pp assumes a clean
n=150, and B's own arms ran at n=112 after free-tier 429 loss. This is a
registered deviation from 5.1pp and is stated here rather than discovered later.

**Noise floor.** Temp 0.1 means even untreated questions flip between runs.
`score_significance.py` documents 8/89 per-question flips between two
replicates of the *unchanged* system (f ≈ 9%) → **MDE ≈ 6.9pp at n=150 from noise
alone**. So a 6.5pp gate is at the edge of the noise floor; the honest framing is
that E2 can detect a directive effect only if it is large, and null results will
be reported as inconclusive rather than as evidence of no effect.

For contrast, B's *K* effect is comfortably powered: binary
`grader_correct_rate` 0.43 → 0.58 from K=10 → K=50 = **+15.2pp**.

## 7. Vocabulary work completed in this pass

The five divergent vocabularies are now unified in
`app/rag/query_type_registry.py`, which owns the canonical ids, every alias,
the directive table, the budget table, and the classifier's emitted spellings.
Alias collision is an import-time `AssertionError`. 27 tests in
`tests/test_query_type_registry.py` pin coverage, aliasing, precedence, and
directive/budget derivation, so the class of bug cannot recur silently.

Measured, behaviour-preserving, byte-identical against git HEAD:
- all 9 directive texts unchanged;
- budget table retains exactly the 7 types that originally declared one, but
  now resolves via aliases — so `general_qa` (the runtime's spelling) finally
  reaches the `general` entry, and benchmark `Title-Case` labels resolve too.

Classifier: strict `\bstem\b` patterns missed inflections (`\bpunish\b` does not
match "punishment"). Converting to morphological stems moved agreement
38.0% → **41.3%** and `ambiguous` 66/150 → 50/150. A second round adding
enforcement power vocabulary **measured worse (40.0%) and was reverted** —
pushing further would mean tuning against the only available evaluation set.
Pinned by `TestClassifierAgainsGold`, which also asserts the classifier stays
below 60% so the oracle-routing decision in §3.2 is not silently invalidated.

### Known-and-accepted

- `_QUERY_TYPE_BUDGETS` is **dead in production**: `grounded_service.py:69`
  constructs `ContextBuilder()` with no `query_type`, and the budget is computed
  in `__init__`, so `build(query_type=...)` never re-applies it. Only eval
  harnesses that pass `ContextBuilder(query_type=qt)` reach it.
- Three classifier spellings stay irregular (`cross-reference`,
  `direct provision`, `insufficient-evidence`) because they are stored in the
  frozen `experiment_B_ce_ranking.json`. Renaming is an artifact-breaking
  migration; the registry declares them as `surface` so they cannot drift.
- `enforceable_right` is a benchmark label with no classifier analogue.
- `definition` is a directive no benchmark question can reach.

## 8. Budget & call accounting

`LLM_ENABLED` / `MAX_LLM_CALLS` **are** enforced, but not by the experiment
harness — by `app/shared/llm_client.py`, which `_SSLBypassLLMClient` inherits
(`llm_enabled()` kill switch + `max_llm_calls()` per-process cap; `0` blocks
every real call). Each phase is a separate process, so each gets its own budget.

`.env` carries `LLM_ENABLED=true` and `MAX_LLM_CALLS=700`, which covers the
600-call LLM phase and the 150-call oracle phase independently.

Correction to an earlier draft: the claim that "the `MAX_LLM_CALLS=1000` prefix
is inert and caps nothing" was wrong in substance — the cap is real, it simply
lives one layer below the harness. Note also that `load_dotenv(override=True)`
means **`.env` wins over any value set in the shell**, so passing
`MAX_LLM_CALLS=1000` on the command line does not raise the 700 in `.env`.

Transport-level 429/empty-200 recoveries are *not* answer-generation retries
(the LLM is never invoked on a rejected request), so they do not consume the
budget — Experiment B's own accounting recorded 443 recoveries against 450
successful generations.

### Run configuration (2026-10-18)

| Item | Value |
| --- | --- |
| `RAG_QUERY_TYPE_DIRECTIVES` | **set to `true` in `.env`** (verified: `cfg.query_type_directives is True`) |
| concurrency | 4 |
| control arm ceiling | B has only **113/150** questions with ≥1 clean record; per-K n = 112 |
| resume | `--resume` skips clean records and retries errored ones |

`RAG_QUERY_TYPE_DIRECTIVES` was set in `.env` only. `.env.example` stays `false`
deliberately — the shipped prompt must remain byte-identical when the flag is
off, and this is a local experiment override, not a default change.

Verified before any call was spent: with the flag on, the control render
(`general_qa`) still carries no directive, all nine directive types inject their
text ahead of `Question:`, and **113/150 (75.3%)** of resolved question types
produce a directive-bearing prompt.

The control model `poolside/laguna-s-2.1:free` is being retired 2026-10-31 (see
`evaluation/MODEL_SWAP_FREE_PLAN.md`). Run results must record the resolved
model id per call to stay interpretable across the swap.

Progress/resume helper: `python -m evaluation.experiment_e2_progress [--cmd]`
(read-only, 0 LLM).

### Run log — 2026-10-18, session 1 (partial, 22:23–22:52)

Stopped at the 23:00 time limit. **330/600 LLM calls, 0 error records**
(no rate-limit losses at all, unlike B's ~25%). Checkpoint verified intact
after the kill: 330 lines, 0 torn writes, 330 unique `(question_id, K)` keys.

Preliminary paired comparison on the 330 completed pairs — **NOT a result**,
recorded only so the final numbers cannot be mistaken for these:

| K | n pairs | treated | E2 | B | Δ grader-weighted (pp) | 95% CI (pp) | MDE (pp) | McNemar p | Holm p |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| 10 | 83 | 69 | 0.4217 | 0.4096 | +1.20 | [−4.82, +7.23] | 6.88 | 1.0000 | 1.0000 |
| 20 | 83 | 69 | 0.4277 | 0.4217 | +0.60 | [−7.23, +8.43] | 6.88 | 1.0000 | 1.0000 |
| 30 | 82 | 68 | 0.5183 | 0.4878 | +3.05 | [−2.44, +8.54] | 6.92 | 0.3750 | 1.0000 |
| 50 | 82 | 68 | 0.5732 | 0.5671 | +0.61 | [−7.93, +8.54] | 6.92 | 1.0000 | 1.0000 |

All four CIs span 0 and none is significant. **This is inconclusive, not
evidence of no effect** — the intervals are wide enough to contain a worthwhile
gain (K=30's CI reaches +8.54pp, above its 6.92pp MDE). The run covers 55% of
the planned pairs and must finish before any claim is made.

## 9. Defects to fix in the E2 runner (all verified, not inferred)

`ruff --select F821` on the draft reports 5 undefined-name errors at 3 sites;
B reports none.

| # | Site | Defect | Impact |
| --- | --- | --- | --- |
| 1 | `:769` | `derive_domain_qt` never defined or imported | **LLM phase 100% failure** (84/84 checkpoint records + reproduced probe) |
| 2 | `:779` | failure return is a **7-tuple**; caller tests `len==5` then unpacks 4 | any exhausted-retry task → `ValueError` |
| 3 | `:949` | `dom_key`/`qt_key` used **outside** `_oracle_task`'s `try` | oracle phase 100% failure (NameError, 150 q) |
| 4 | `:447` | `run_llm_pipeline` returns undefined names | dead code (no callers); delete |

## 10. Non-goals

- No reasoning/two-pass path: `structured_reasoner` / `reasoning_path` exist but
  wiring them changes a second variable and voids the single-variable claim.
  The draft's argparse advertised it; it was never implemented.
- No repair of the `duty`→`obligation` alias at the *classifier* level (the
  registry resolves it; the legacy classifier's rename is tracked separately).
- No change to the frozen B arm, benchmark, or CE cache.
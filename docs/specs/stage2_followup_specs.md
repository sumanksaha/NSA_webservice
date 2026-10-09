# Stage-2 A/B Follow-up Specs (n=20 paired, 22 recovered qids)

Evidence base: `evaluation/out/ceiling_v5/stage2_state.json` + `targeted_retry_answers_ab.json`
(summary n=20 paired; Q146 retry arm and Q149 both arms missing — 3/44 slots rate-limited).

Headline results driving these specs:
- binary_correct 0.15 → 0.15 (flips: up Q003, down Q132)
- answer_correctness 0.334 → 0.382 (+0.049)
- citation_recall 0.00 → 0.76, citation_precision 0.00 → 0.37
- groundedness 0.80 → 0.90, hallucinations 0.20 → 0.10, latency +1.9s

---

## SPEC-1 — Baseline citation enforcement (highest leverage)

**Problem.** Baseline citation_recall = 0.00 across all 20 pairs while the retry arm
reaches 0.76 on the same model with the same generation stack. The citation contract
in `app/rag/generation/prompt_template.py` (`GROUND_QA_SYSTEM_PROMPT` + `GROUND_QA_USER_TEMPLATE`)
works when the window is rich (retry: baseline union plan lexical matches, cap 20 —
see `_retry_chunks` in `evaluation/ab_targeted_retry_answers_fast.py`), but the narrow
baseline window (top-10) yields uncited answers. The prompt's "Step 1 — quote" is
treated by the model as optional when few chunks look relevant.

**Goal.** Baseline citation_recall >= 0.50 with no retrieval change (prompt-only fix),
verified on the 20 paired qids (re-run) plus a >=50-qid held-out slice.

**Changes.**
1. `prompt_template.py` — strengthen the user template:
   - Require >=1 verbatim quote from the shown context before any answer text, even when
     context is thin; the Q003-style abstention ("I cannot find the answer…") is allowed
     ONLY when zero quotes can be produced.
   - Require every material claim to carry an `[n]` marker mapping to a shown source;
     restate the existing no-fabrication rule.
2. Apply the same contract wording to all `DOMAIN_SYSTEM_PROMPTS` (env / commercial /
   animal / wb_state / bns / general), not just the `fssai` default — the non-FSSAI
   prompts currently only say "Cite sources using [n] markers" without the quote-first
   structure. Every domain prompt must lead with the two-step (quote, then answer)
   instruction.
3. Add `tests/test_baseline_citation_enforcement.py`:
   - unit: rendered prompt contains the quote-first requirement and `[n]` contract for
     every domain key;
   - integration (stub-LLM or recorded): answers on thin (<=3-chunk) windows still emit
     >=1 verbatim quote + >=1 valid `[n]` when claims are made, or abstain with no claims.

**Acceptance.**
- Re-run Stage-2 harness on the 20 paired qids: baseline citation_recall >= 0.50,
  citation_precision >= 0.25, and Q003-style false abstentions (abstains despite
  quote-able text in window) = 0.
- No regression on baseline binary_correct (stays >= 0.15 on the same 20).

**Non-goals.** Retrieval changes; verifier changes. Prompt-only.

---

## SPEC-2 — Retry targeting accuracy (make recovery fetch the missing provision class)

**Problem.** Consistent with `docs/RAG_AUTORESEARCH_RESEARCH.md` section 5.6
("targeted retries are often placeholders"): `TargetedRetryPlanner.target_plan()` in
`app/rag/planning/targeted_retry.py` classifies failures, but two paths degrade to the
original query — (a) `strategy == "collection_reroute"` is remapped to
`identifier_search` (code comment notes `_target_collection` was an identity no-op),
and (b) unknown strategies fall through to `arm=HYBRID` with the unmodified query.
Stage-2 shows recovery improves evidence *volume* (citation_recall 0.76) but binary
only ties (1 up / 1 down): retrieval recovers *more* evidence, not reliably the
*missing provision class*.

**Goal.** For each failure class the retry query must be observably different from the
baseline query, and per-arm recovery rate (gold-bearing chunk present in retry window
but absent in baseline window) must be measurable per arm.

**Changes.**
1. `targeted_retry.py`:
   - Replace the `collection_reroute -> identifier_search` identity fallback with a real
     collection-targeted query (collection-scoped terms + section/identifier tokens from
     the failure payload), or route to abstain when no targeting tokens exist.
   - Make the fallthrough explicit: unknown strategy yields a no-op plan tagged
     `strategy="unmapped"` (logged + counted), never a silent query echo.
   - Add a plan-time invariant hook: a retry plan with failures=() may echo the input
     query; a retry plan with non-empty failures must not (`plan_query != query`
     unless `failures == ()`).
2. `evaluation/ab_targeted_retry.py::_plan_match_ids` — log per-arm recovery counts
   (gold chunk in retry window only) into `targeted_retry_ab.json` so Stage-1 reports
   recovery rate per arm (sparse_identifier / definition / hierarchy / kg / hybrid).
3. Extend coverage (see `tests/test_targeted_retry_v2.py` for the existing pattern):
   - every strategy produces a query different from the input query;
   - each plan query contains a class-specific token (identifier token, definition
     phrase, hierarchy/section marker, KG entity) for its failure class;
   - one fixture per strategy including `collection_reroute` and unknown-strategy.

**Acceptance.**
- Tests: 100% of non-empty-failure plans emit query != input; per-strategy fixtures pass.
- Eval: Stage-1 recovery-rate-per-arm report exists; recovered-qid count does not regress
  vs the current 22; Stage-2 net flips >= 0 on the same 20 after this change alone
  (before SPEC-3).

---

## SPEC-3 — Verifier-gated retry replacement (guard against flip-downs like Q132)

**Problem.** Q132 flipped baseline-correct to retry-wrong (0.400 → 0.398, within
scorer jitter) while Q003 flipped up 0.267 → 0.617. The pipeline currently adopts the
retry answer unconditionally. With binary tied 1–1, the safe policy is
retry-as-fallback, not retry-as-replacement.

**Goal.** The retry answer is adopted only when measurably better; otherwise keep
baseline. Net flips must be >= 0 by construction on any eval slice.

**Changes.**
1. New selector on the verify/finalize path (production candidate:
   `app/rag/agent/nodes/common.py` or finalize; eval mirror in
   `evaluation/ab_targeted_retry_answers_fast.py` report section):
   - Inputs per qid: baseline scorecard B and retry scorecard R from the same scorer
     (`evaluation/answer_scoring.score_answer`).
   - Rule (defaults, tunable): adopt retry iff `R.binary_correct > B.binary_correct`,
     OR (`R.answer_correctness - B.answer_correctness >= 0.05`
         AND `R.citation_precision >= B.citation_precision`
         AND `R.groundedness_score >= B.groundedness_score - 0.10`);
     otherwise keep baseline. Ties and regressions keep baseline.
   - Emit `selected_arm` in {baseline, retry} plus the deciding deltas per qid.
2. Threshold margins (`0.05`, `0.10`, precision non-regression) live in
   `app/rag/agent/thresholds.py` next to the existing gates
   (`GROUNDEDNESS_RETRY_BELOW=0.7`, `CLAIM_GROUNDEDNESS_RETRY_BELOW=0.5`) — e.g.
   `RETRY_ADOPT_SOFT_DELTA_AT_LEAST=0.05`, `RETRY_ADOPT_GROUNDEDNESS_SLACK=0.10`.
3. New `tests/test_retry_replacement_guard.py` (see `tests/test_retry_recovery.py`
   for the existing retry-test pattern):
   - Q003-shaped fixture selects retry; Q132-shaped fixture keeps baseline;
   - exact-tie fixture keeps baseline; missing-scorecard fixture keeps baseline
     (fail-safe);
   - property: selected binary_correct >= max(B, R) binary over the Stage-2 20-pair
     fixture set — never worse than the best arm.

**Acceptance.**
- Selector unit + property tests pass.
- Recomputed Stage-2 (same 20 pairs + guard applied): flips_down = 0, flips_up >= 1,
  and selected-arm citation_recall >= retry-only value (guard never discards
  Q003-type wins).
- Shadow logging: `RAG_VERIFIER_SHADOW` records selected_arm + deltas without changing
  served output until SPEC-4 calibration lands.

---

## SPEC-4 — Evaluation hygiene + RAG_VERIFIER_SHADOW threshold calibration

**Problem.** (a) Numbers rest on n=20 paired with 3/44 slots missing (Q146 retry,
Q149 both) and heavy free-tier 429 noise (~50% first-attempt slot failure); the Q132
flip is within scorer jitter. (b) Shadow verifier thresholds in
`app/rag/agent/thresholds.py` predate any flip evidence and must be calibrated
against the Stage-2 deltas.

**Goal.** (i) A complete, stable 44-slot report; (ii) shadow thresholds justified by
measured separation, recorded in an ADR.

**Changes.**
1. Complete coverage: keep running `python -m evaluation.run_stage2_accumulator`
   (one qid per invocation, typically 3-5 invocations per stubborn qid) until
   `stage2_state.json` holds 44 slots. Q146/Q149 are the remainder. Do not overwrite
   first good answers (existing `merge_run` semantics stay).
2. Flip-stability check: for each flipped qid (Q003, Q132, plus any new flips in the
   full 44), run the Stage-2 harness 3x and report persistence (3/3, 2/3, 1/3).
   Flips at 1/3 with |delta answer_correctness| < 0.05 are labeled jitter, not signal.
3. Shadow-threshold calibration — new ADR under `docs/adr/`:
   - Inputs: per-qid deltas from the full-44 report (n=20 reference: citation_recall
     delta +0.76, precision +0.37, groundedness +0.10, hallucination -0.10).
   - Propose initial shadow-fire rules, e.g. fire when `citation_recall == 0` on a
     claims-bearing answer (baseline never cites — strongest separator), or when
     `groundedness < 0.7` (aligns with existing `GROUNDEDNESS_RETRY_BELOW=0.7` and the
     observed retry lift 0.80 to 0.90).
   - Justify keeping vs moving `CLAIM_GROUNDEDNESS_RETRY_BELOW=0.5`: measure what
     fraction of baseline answers below 0.5 actually flipped up on retry — if ~0
     (as in n=20), the gate fires but does not convert, which points back to SPEC-2
     targeting quality rather than the threshold value.
4. Persist the n=20 report as an explicit artifact (until 44 completes):
   `evaluation/out/ceiling_v5/stage2_ab_n20_report.json` with paired aggregates,
   flips, missing-slot list, and the harness limitations note
   ("Live single-model run; soft deltas are noisy…").

**Acceptance.**
- `stage2_state.json` = 44 slots; final `targeted_retry_answers_ab.json` reports n=22
  with flips_up/flips_down plus a flip-stability table per flip.
- ADR merged with chosen shadow thresholds + the measured separation table justifying
  them; no production threshold value changes without the ADR.
- `stage2_ab_n20_report.json` committed as the interim record so the n=20 numbers
  quoted in SPEC-1..3 are reproducible.

**Order of work.** SPEC-4.1/4.4 (finish + record evidence) → SPEC-1 → SPEC-3 →
SPEC-2 → SPEC-4.2/4.3 (stability + calibration). SPEC-3's guard makes SPEC-2's
experimentation flip-safe; SPEC-1 is independent and highest-ROI, so it can go first
in parallel.



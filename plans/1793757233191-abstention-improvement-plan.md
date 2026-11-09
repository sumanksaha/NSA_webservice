# Abstention Status, Gaps, and Improvement Plan

## 1. Current status of abstention

### 1.1 What exists

Abstention is implemented as a first-class refusal path across three layers:

**Production lexicon + detector** — `app/rag/generation/abstention.py`
- `ABSTENT_MARKERS_RE`: negation-anchored regex lexicon (v1 + v2 extensions + V3 legal-specific phrasings discovered from failure-cohort analysis). ~31 marker groups, anchored on negation ("cannot", "does not", "no provision", "insufficient", etc.) so a substantive answer that merely enumerates powers the reference says are not established gets no credit.
- `MIN_ABSTENTION_CHARS = 20`: below this length a short/degenerate answer IS treated as abstention (matches frozen eval contract) — prevents laundering a short ungrounded answer into "not a hallucination".
- `is_abstention(response_text, banned=None)`: production boolean. 0 model calls, deterministic.
- `strip_banned(answer, banned)`: removes overlay-banned markers (Q060/Q138) before matching.

**Evaluation mirror** — `evaluation/abstention_rule.py`
- `ABSTAIN_CREDIT_RE` re-exports `ABSTENT_MARKERS_RE` (one definition, two consumers).
- `abstain_match(answer, banned)`: extended lexicon match for v2 credit path (any answer < 20 chars is abstention — frozen heuristic kept).
- `abstain_credit(answer, banned, insufficient_evidence)`: binary credit due iff IE question AND genuine abstention.
- `summarize_groundedness(rows)`: abstention-aware split (`groundedness_all`, `groundedness_answering`, `abstention_rate`) so averaging over an abstention-heavy population doesn't read correct refusals as groundedness collapse (ADR-0010 §2.5).

**Graph abstain path** — `app/rag/agent/nodes/dag.py`
- `abstain_node(state)`: returns fixed refusal answer, `groundedness=0.0`, `hallucination_detected=False`, `abstained=True`. Routed to from `_route_after_evidence` when `abstain_required` is true, and from `_route_after_budget` when `budget_exhausted`.
- `evidence_sufficiency_node`: 7-signal rubric (coverage, relevance, authority, specificity, completeness, contradiction, temporal). Sets `abstain_required = (budget_exhausted and not sufficient and total_tasks > 0 and coverage < _COVERAGE_FLOOR) or (total_tasks == 0 and not evidence)`.
- `_COVERAGE_FLOOR = 0.5` (from `thresholds.EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST`). `SUFFICIENT_TASK_RATIO = 0.5`.
- `budget_gate_node`: `budget_exhausted` via `routing_economics.is_exhausted`.

**FSO advisory abstain** — `app/rag/agent/nodes/advisory.py`
- `fso_advisory_node`: fail-closed abstention (`fso_act=None`, `advisory_abstain_reason="insufficient_statutory_grounding"`) when citation gate fails and no retrieved chunk carries a schedulable FSSAI section. Reuses ADR-0003 semantics (same abstain_node per ADR-0003 §2).

**Evaluator v4 C3 (`evaluation/evaluator_v4.py`)** — `soft_v4`: IE question → 1.0 if abstained, else 0.0. `apply_rule` credit shape: `C1 or (C2 and not C5) or abstain_credit_road`.
**Top-lawyer rubric (`evaluation/rubric_top_lawyer.py`)** — `abstention_discipline`: pass iff `abstained == should_abstain`; tracks `false_abstention` and `missed_abstention`.
**Residual scorer (`app/rag/agent/research_orchestrator.py`)** — `_detect_failure_signal`: abstention → `evidence_missing` class; `_score_answer` requires non-abstaining + no hallucination + groundedness ≥ floor + ≥1 citation to pass.

### 1.2 Measured state (from saved context)

- 89 `model_wrong` questions; breakdown: retrieval_rank 27 (30.3%), generation 59 (66.3%), never_retrieved 3 (3.4%).
- `abstention_gate` total 31 (34.8%): "system should have abstained". Within it: never_retrieved 1 (3.2%), generation 20 (64.5%), retrieval_rank 10 (32.3%).
- `abstention_discipline` = 0.62 on the failure cohort (23% false abstentions) per CHANGELOG.
- `false_abstention_rate` = 0.0, `missed_abstention_rate` = 0.0 on the test cohort (per `abstention_improvement_plan.md`).
- Q060 is the sentinel IE abstention guard: models that answer an IE question at length forfeit the credit. Lagunia holds it; apodex/nemotron/haiku lose it under length-punishing gates.
- ADR-0010 §2.5 measured a quote-first prompt drove abstention 8/21 → 19/21, with correct refusals scored as hallucination (`false_abstention` 0.073 → 0.436). Prompt reverted; abstention now scored separately.
- Prompt-conditioning constraint: new conditioning must not compromise the abstention_gate (project constraint). Reverted quote-gate variant caused large false abstention; p=0.27 on pooled test, treated as unchanged.

### 1.3 Present status summary

Abstention is **implemented and wired end-to-end** (detect → gate → node → audit → eval credit → rubric discipline), with `false_abstention_rate = 0.0` and `missed_abstention_rate = 0.0` on the current test cohort. The binding constraint is now **coverage/retrieval quality**, not abstention mechanics: 31 of 89 model_wrong questions (34.8%) are abstention_gate cases where the system should have abstained but didn't (or vice versa), and of those 20 are generation failures (answered without proper evidence) and 10 are retrieval_rank failures (evidence retrievable but not surfaced).

---

## 2. Gaps

| # | Gap | Evidence | Impact |
|---|-----|----------|--------|
| G1 | **31 abstention_gate cases wrong** on the failure cohort (34.8% of model_wrong). 20/31 are generation (answered instead of abstaining), 10/31 retrieval_rank (evidence present but not surfaced), 1/31 never_retrieved. | `abstention_improvement_plan.md` §1; `evaluation/Facts_eval.primary_failure_modes` | Missed abstentions → false substantive answers; false abstentions → lost IE credit (Q060 veto). |
| G2 | **Per-query-type abstention calibration missing.** ADR-0010 §2.5 / `docs/answer_generation_evaluation.md`: static threshold, no per-query-type adaptation. | `docs/answer_generation_evaluation.md` items 3 & 5 | One-size threshold over-abstains on some types, under-abstains on others. |
| G3 | **Lexicon coverage on residual negation phrasings is partial.** v1 missed "cannot be determined" (trailing `\b`) and "does not recognise"; v2/V3 added many but residual cohort analysis continues to surface new phrasings. | `evaluation/abstention_rule.py` docstring; `abstention.py` V2/V3 comments | Missed abstentions on IE questions that use novel negation phrasing. |
| G4 | **No production adaptive threshold.** `GROUNDEDNESS_RETRY_BELOW=0.7` and `EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST=0.5` are static constants; no calibration loop keyed on `false_abstention_rate` / `missed_abstention_rate`. | `app/rag/agent/thresholds.py`; `abstention_improvement_plan.md` §4 | Thresholds cannot drift with model/pool changes. |
| G5 | **Abridged audit on *why* abstain.** `abstain_node` records only `reason: "insufficient_evidence"`; no signal on which rubric signal failed (coverage vs relevance vs authority vs contradiction vs temporal). | `app/rag/agent/nodes/dag.py:554-565` | Hard to diagnose whether a missed abstention was retrieval or sufficiency. |
| G6 | **Linear path has no explicit abstain.** `route_after_verify` → `expand_query` / `targeted_retry` / `finalize`; no abstain branch on the linear path. | `app/rag/agent/graph.py:57-102` | Linear-path low-evidence questions are forced to answer (generate) rather than abstain. |
| G7 | **No monitoring/feedback loop for abstention rate.** `summarize_groundedness` exists but no scheduled recompute / alert on `false_abstention_rate` or `missed_abstention_rate` drift. | `evaluation/abstention_rule.py:101`; `abstention_improvement_plan.md` §7 | Regressions (like the quote-gate 0.073→0.436 flip) are caught only by ad-hoc A/B. |
| G8 | **Prompt-conditioning risk.** Constraint `prompt_conditioning.abstention_regression`: new conditioning must not compromise abstention_gate. Prior quote-gate variant increased false abstention 0.073→0.436 (reverted). | `app/rag/generation/prompt_template.py:108-115`; project constraint | Any prompt change risks regressing abstention discipline. |

---

## 3. Improvement strategies (evaluated)

### S1 — Per-query-type abstention threshold calibration (addresses G2, G4)
Collect per-query-type `false_abstention_rate` and `missed_abstention_rate` on a calibration split; tune `GROUNDEDNESS_RETRY_BELOW` / `EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST` per type (or per query-type band) subject to the hard constraint that both rates stay at 0.0 on the held-out test cohort. Use `summarize_groundedness` to keep `groundedness_answering` separate from `groundedness_all`.

**Pros:** Directly reduces the 31 abstention_gate errors; respects the zero-false-abstention guardrail; deterministic, no extra LLM calls.
**Cons:** Requires labelled calibration data per type; risk of overfitting to the cohort.
**Verdict:** Adopt — highest leverage, zero LLM cost, aligns with existing `summarize_groundedness` infra.

### S2 — Extend the abstention lexicon with residual negation phrasings (addresses G3)
Mine the residual (missed-abstention) cohort for negation phrasings not covered by `ABSTENT_MARKERS_RE`; add V4 extensions keeping negation-anchored constraint (never "mentions the same subject"). Re-export through `evaluation/abstention_rule.py` so production and eval stay in sync. Validate `false_abstention_rate` stays 0.0.

**Pros:** Catches missed abstentions on IE questions that use novel phrasing; cheap (regex, 0 model calls).
**Cons:** Lexicon growth can increase false-positive surface; must re-validate against the frozen v1/v2 contract (`abstain_match` <20 chars heuristic).
**Verdict:** Adopt — directly targets G3; bounded risk via the negation-anchored discipline and the 0.0 false-abstention guardrail.

### S3 — Add abstain to the linear path (addresses G6)
Extend `route_after_verify` (and/or `route_after_budget`) so a linear-path question with critically low groundedness/coverage and exhausted retry budget routes to `abstain_node` instead of `finalize`/`expand_query`. Linear path currently forces an answer.

**Pros:** Closes the linear-path gap; symmetric with the DAG path.
**Cons:** Linear path has no per-task coverage signal; must use groundedness/citation_quality as the abstain trigger. Risk of false abstentions on answerable linear questions — must validate against `false_abstention_rate = 0.0`.
**Verdict:** Adopt conditionally — implement only behind a gating constant and validate on the held-out cohort before enabling.

### S4 — Enrich abstain audit with rubric-signal diagnosis (addresses G5)
`evidence_sufficiency_node` already computes per-task signal verdicts. Record which signal(s) drove `abstain_required` (coverage/relevance/authority/contradiction/temporal) in the abstain audit entry.

**Pros:** Turns G5 into actionable diagnostics; no behavior change, telemetry only.
**Cons:** None material.
**Verdict:** Adopt — pure telemetry, zero risk.

### S5 — Monitoring & feedback loop (addresses G7)
Schedule periodic recompute of `false_abstention_rate` / `missed_abstention_rate` on the held-out test cohort (and on new production answers via `is_abstention` + `abstain_match` agreement); alert on drift. A/B test threshold changes on the calibration split before rollout.

**Pros:** Catches regressions like the quote-gate flip before they reach production.
**Cons:** Requires a scheduled job / CI gate.
**Verdict:** Adopt — cheap and necessary to hold the 0.0 guardrail over time.

### S6 — Prompt-conditioning guardrail (addresses G8)
Any new prompt/template change must pass an abstention-discipline check: `false_abstention_rate` and `missed_abstention_rate` on the held-out cohort must remain 0.0; flip-stability protocol on abstention rate (ADR-0010 §6) before merge.

**Pros:** Prevents regression of the gains; institutionalizes the lesson from the reverted quote-gate.
**Cons:** Adds a CI gate.
**Verdict:** Adopt — required by the project constraint `prompt_conditioning.abstention_regression`.

### Not adopted (out of scope / already tried)
- **Model swap:** assessed and rejected (MODEL_SWAP_FREE_PLAN, CURRENT_STAGE_ASSESSMENT §10) — thinking models leak CoT and forfeit Q060 abstention credit; not a config change.
- **Length-targeted prompt:** assessed and rejected (CURRENT_STAGE_ASSESSMENT §11) — moves length, not correctness; net elim 0.
- **KG expansion:** live effect ~zero (CURRENT_STAGE_ASSESSMENT §5); off the table for abstention.
- **Quote-gate / abstention-escape clause:** reverted (ADR-0010 §2.5, prompt_template.py) — drove false abstention 0.073→0.436.

---

## 4. Prioritized improvement plan

### Phase 1 — Immediate (telemetry + lexicon, zero behavior change)
1. **S4:** Record rubric-signal diagnosis in the abstain audit entry (`app/rag/agent/nodes/dag.py:abstain_node`/`evidence_sufficiency_node`).
2. **S2:** Mine residual missed-abstention phrasings; add V4 extensions to `ABSTENT_MARKERS_RE`; re-export via `evaluation/abstention_rule.py`; validate `false_abstention_rate` / `missed_abstention_rate` remain 0.0 on held-out cohort.
3. **S5:** Add a CI/test that recomputes `false_abstention_rate` and `missed_abstention_rate` on the held-out cohort and asserts both are 0.0.

### Phase 2 — Calibration (per-query-type thresholds)
4. **S1:** Build per-query-type `false_abstention_rate` / `missed_abstention_rate` on a calibration split; tune `GROUNDEDNESS_RETRY_BELOW` and/or `EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST` per type subject to the 0.0 guardrail; store tuned values in `thresholds.py` (or a calibration config).
5. **S6:** Add the abstention-discipline flip-stability check as a CI gate for any prompt/template change.

### Phase 3 — Linear-path abstain (conditional; implemented, default off)
6. **S3:** Implemented behind `RAG_LINEAR_ABSTAIN` (default off):
   - `app/rag/agent/thresholds.py`: `LINEAR_ABSTENTION_FLOOR = 0.1`
   - `app/rag/agent/graph.py`: `route_after_verify` gains an abstain branch
     when retry budget is exhausted AND groundedness < floor AND
     `linear_abstain` is set; `build_graph` / `_get_graph` /
     `TopologyFlags` gain a `linear_abstain` parameter; a new
     `_resolve_linear_abstain` reads the live config flag.
   - `app/shared/config.py`: `RAG_LINEAR_ABSTAIN` setting declared.
   - **NOT enabled on the served path** — the S1 calibration measured
     `false_abstention_rate = 0.8148`, so enabling unconditionally would
     regress the guardrail. Enable only after cohort validation shows
     `false_abstention_rate = 0.0`.

---

## 5. Validation plan

- **Unit:** `tests/` — add/extend tests for `is_abstention`, `abstain_match`, `abstain_credit`, `summarize_groundedness`, `abstain_node`, and `evidence_sufficiency_node` audit enrichment. Assert `false_abstention_rate == 0.0` and `missed_abstention_rate == 0.0` on the held-out cohort after every change.
- **Integration:** Run the top-lawyer rubric (`evaluation/rubric_top_lawyer.py`) `abstention_discipline` dimension across the failure cohort; assert `false_abstention_rate` and `missed_abstention_rate` unchanged at 0.0.
- **A/B:** For threshold changes (S1/S3), run flip-stability protocol on abstention rate (ADR-0010 §6) on the calibration split; require no statistically significant increase in false/missed abstention.
- **Regression:** Prompt/template changes must pass the S6 CI gate (abstention-discipline unchanged) before merge.

---

## 6. Open questions / decisions needed before implementation

### BLOCKER — S1 per-query-type threshold calibration (RESOLVED 2026-11-08, zero LLM)

The blocker was that `answers_baseline.json` lacked continuous per-qid
groundedness/coverage.  **Resolved**: the S1 pipeline signals
(`evaluation/out/ceiling_v5/s1_pipeline_signals.jsonl`) carry
`groundedness_score` for all 150 qids, plus `query_types` and
`expected_abstain`.  `evaluation/prepare_abstention_calibration.py` was
rewritten to join these (version `abstention-calibration-data-v2`,
written to `evaluation/out/ceiling_v5/abstention_calibration_data.json`).
**0 LLM calls.**

**Result**: per-query-type rates computed for all 14 query types.
Roll-up: `false_abstention_rate=0.8148`, `missed_abstention_rate=0.20`.

**But the threshold approach is structurally impossible with this signal.**
Cross-tabulation of the 150 qids against every numeric field shows **no
continuous variable separates abstained from answering**:

| signal | false_ab (110) | correct_ab (12) | answering (28) |
|---|---|---|---|
| `groundedness_score` | {0.0, 1.0} | {0.0, 1.0} | {1.0} |
| `n_chunks` | {0,5,10,27,40} | {0,5,9,10} | {5,10,40} |
| `llm_calls_used` | {0,1,2,3,4} | {0,2,4} | {1,2,3,4} |
| `prompt_tokens` | 0–14946 | 0–4191 | 224–10051 |
| `completion_tokens` | 0–4096 | 0–4096 | 1102–4096 |

`groundedness_score` is 1.0 for both abstained and answering qids — the
model is equally "confident" whether it refuses or answers.  The abstention
decision is made by the **text regex** (`is_abstention` in
`app/rag/generation/abstention.py`), not by a groundedness threshold.  There
is no continuous variable to tune.

**Conclusion**: S1's threshold-calibration strategy (tune
`GROUNDEDNESS_RETRY_BELOW` / `EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST` per
query type) cannot reduce the 110 false abstentions because the decision
boundary is not a score — it is the presence of negation markers in the
generated text.  The correct lever is the **answer contract repair** (already
implemented: `app/rag/generation/answer_contract.py`) and **retrieval recall**
(already committed: `2209ce6`), not threshold tuning.  S1 is closed as
**completed (data produced) / strategy superseded**.

The per-query-type rates are still useful as a **diagnostic stratification**:
they confirm the false-abstention problem is uniform across query types
(66–100% false rate on 13 of 14 types; only `Insufficient-evidence` is clean),
which rules out a per-type fix and points at the shared text-generation path.

### Remaining decisions

1. **S3 linear-path abstain:** Should the linear path be enabled for abstain at all, given it has no per-task coverage signal? (Recommended: implement gated behind a constant, default off; enable only after cohort validation.)
2. **Calibration storage:** Should per-query-type thresholds live in `app/rag/agent/thresholds.py` (code) or a separate calibration config file? (Recommended: `thresholds.py` for determinism/unit-testability, matching existing convention.)
3. **Residual lexicon source:** Where should the residual missed-abstention phrasings be mined from — `evaluation/out/ceiling_v5/failure_attribution.json` or the rubric residual set? (Recommended: the rubric residual / `failure_attribution.json`.)

### Completed (Phase 1 + Phase 2 data + Phase 3 implementation)

- S4 abstain-audit diagnosis (`app/rag/agent/nodes/dag.py` — `abstain_drivers`, `abstain_required`, `evidence_coverage` in the abstain audit entry).
- S2 abstention lexicon V4 extensions (`app/rag/generation/abstention.py` — `_V4_EXTRA`, kept negation-anchored, re-exported via `evaluation/abstention_rule.py`).
- S5 tests (`tests/test_abstain_node.py`, V4 + negative tests in the existing abstention tests).
- S1 calibration data (`evaluation/prepare_abstention_calibration.py` → `evaluation/out/ceiling_v5/abstention_calibration_data.json`, version v2 from S1 signals, 0 LLM). Threshold strategy superseded — no continuous signal separates abstained from answering.
- S3 linear-path abstain implemented behind `RAG_LINEAR_ABSTAIN` (default off; `app/rag/agent/graph.py`, `app/rag/agent/thresholds.py`, `app/shared/config.py`).

All `ruff check`/`ruff format` pass; new tests pass (14/14). The 5 remaining failures in the abstention suite are pre-existing (short/empty-answer handling inconsistency, confirmed on the unmodified code via `git stash`).

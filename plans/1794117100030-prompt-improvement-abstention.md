# Prompt Improvement Plan: Reduce False Abstentions

## Goal

Reduce false abstentions (model refuses to answer when the governing provision is in the retrieved pool) by improving the answer-contract prompt and repair directive. Target: drive false-abstention rate toward 0.0 on hit qids without regressing missed-abstention rate above 0.0 on IE questions.

## Current State

### Signal snapshot (45/150 qids re-run live with fixes applied)

| Metric | Value |
|--------|-------|
| Overall abstention rate | 28.9% (was 83.3% baseline) |
| False abstention rate | 26.7% (12/45) |
| Correct answers | 66.7% (30/45) |
| Correct abstentions | 2.2% (1/45) |
| False answers | 4.4% (2/45) |

### Remaining false abstentions (4 hit qids confirmed)

| Qid | Answer length | Citation markers | Why still abstaining |
|-----|--------------|------------------|---------------------|
| Q005 | 1673 | Yes | Corpus gap — Section 22 operative text not indexed (not fixable by prompt) |
| Q076 | 796 | No | Short uncited refusal |
| Q091 | 711 | No | Short uncited refusal |
| Q094 | 1329 | No | Short answer with abstention markers |

### Root-cause analysis

The abstention decision is made by `is_abstention()` regex on generated text, not by a groundedness threshold. The lightweight model (`nemotron-3.5-lightning:free`) writes short uncited refusals even when the governing provision is in context. The answer contract repair fires but the model does not reliably follow "NEVER abstain" directives.

### Constraint (from existing plan)

Project constraint `prompt_conditioning.abstention_regression`: new conditioning must not compromise the `abstention_gate`. Prior quote-gate variant increased false abstention 0.073→0.436 (reverted). Any prompt change must keep `false_abstention_rate = 0.0` and `missed_abstention_rate = 0.0` on the held-out test cohort.

## Improvement Strategies (to evaluate)

### P1 — Citation-mandatory answer contract repair

Add an explicit requirement: if the governing provision is in context, the answer MUST cite it with [n] markers. A short answer with citations is substantive (per the `is_abstention` citation override just committed in `02a6f96`).

**Mechanism:** In `app/rag/generation/prompt_template.py`, add to the repair user prompt:
> "You MUST cite the governing provision with [n] markers. An answer without citations is incomplete."

**Pros:** Directly targets Q076/Q091 (short uncited refusals). Leverages the citation-aware `is_abstention` fix.
**Cons:** May not help Q094/Q005 if they already cite but still contain refusal phrases.

### P2 — Mandatory conclusion section in answer format

Require the answer to end with a "Conclusion:" section that states the answer directly. The `is_abstention` regex matches refusal phrases anywhere in the text; a mandated conclusion section makes the refusal phrase structurally isolated.

**Mechanism:** Add to both initial and repair prompts:
> "Your answer MUST end with a 'Conclusion:' section that states the direct answer."

**Pros:** Gives the model a structured place to commit; reduces the chance that a qualification phrase in the body triggers the abstention regex.
**Cons:** Does not prevent the model from writing "I cannot answer" in the conclusion itself.

### P3 — Forceful "NEVER abstain" directive with worked examples

Replace the current "DO NOT give a blanket refusal" language with a stronger directive plus worked examples of correct vs incorrect answers.

**Mechanism:** In `app/rag/generation/prompt_template.py` repair user prompt:
> "CRITICAL: You MUST answer this question. Do NOT say 'the evidence does not establish', 'I cannot determine', 'the context does not specify', or any equivalent refusal. If the governing provision is in the context, answer it directly with citations."
>
> Example:
> Q: What is the penalty under Section 7?
> A: Section 7 carries a penalty of ₹1 lakh to ₹15 lakh under Section 14A [1][2].

**Pros:** Directly addresses model reluctance. Examples are more effective than abstract directives.
**Cons:** Longer prompt increases token cost and latency; must validate no abstention_gate regression.

### P4 — Temperature / top_p reduction for answer determinism

Reduce sampling randomness so the model is more likely to follow the "NEVER abstain" directive consistently.

**Mechanism:** In `app/shared/config.py` or `app/rag/generation/llm_client.py`, lower `temperature` for the repair pass (or all passes).

**Pros:** Simple config change; more deterministic compliance with directives.
**Cons:** May reduce answer diversity/quality; the lightweight model may already be near-deterministic at default temperature.

### P5 — Two-pass answer: first draft, then "strip refusals" repair

If the initial answer matches an abstention marker, run a second pass whose ONLY instruction is to remove refusal phrases and restate the answer positively.

**Mechanism:** In `app/rag/generation/grounded_service.py`, add a second repair pass after the current answer contract repair:
> "Remove all refusal phrases ('I cannot', 'the evidence does not establish', 'does not specify') from the previous answer. Restate what the governing provision says, with citations. If you cannot answer, say ONLY 'ABSTAIN'."

**Pros:** Targets the exact failure mode (refusal phrases in otherwise-good answers).
**Cons:** Adds an extra LLM call; the model may still refuse on the second pass.

## Evaluation Criteria

A strategy passes only if, on the held-out test cohort:
- `false_abstention_rate == 0.0` (no answering qid wrongly abstains)
- `missed_abstention_rate == 0.0` (no IE qid wrongly answers)
- Flip-stability protocol per ADR-0010 §6 (no statistically significant change)

## Rollout Plan

1. Implement P1 (citation-mandatory) as the primary change — leverages the citation-aware `is_abstention` fix already in production.
2. If P1 does not reduce false abstentions below target, layer P2 (mandatory conclusion).
3. P3 (worked examples) as a third option if P1+P2 are insufficient.
4. P4/P5 as fallbacks only if the first three fail.
5. Validate each change against the full 150-qid S1 signals pipeline and the held-out test cohort before enabling.

## Open Questions

- Should P1 apply to the initial prompt as well as the repair prompt, or repair only? (Recommended: repair only — initial prompt already works for the 66.7% that answer.)
- Should P2's conclusion section be mandatory for all answers or only repair passes? (Recommended: all answers — consistency.)
- What citation format should P1 require: `[n]` (existing) or `[section:chunk]`? (Recommended: `[n]` — matches existing context builder output.)

## Decisions Needed

1. Which strategy (P1-P5) to implement first?
2. Should the plan target Q076/Q091 specifically, or all 4 remaining false abstentions including corpus-gap cases Q005/Q094?
3. Confirm the validation cohort: use the held-out test cohort from `evaluation/out/ceiling_v5/abstention_calibration_data.json`?

Recommended: implement P1 first, targeting Q076/Q091 (short uncited refusals), validating against the full 150-qid signals after the background run completes.

# Abstention Way Forward: Drive False + Missed Abstention to 0.0

## Goal

Reduce both false abstention rate and missed abstention rate to 0.0 on the held-out test cohort. Current state after full 150-qid S1 pipeline run:

| Metric | Value | Target |
|--------|-------|--------|
| False abstention rate | 0.1852 (25/135) | 0.0 |
| True abstention rate | 0.4000 (6/15) | — |
| Missed abstention rate | 0.6000 (9/15) | 0.0 |
| LLM calls | 466/800 | — |

## Root Cause Analysis

Four gaps identified from code inspection and the completed run:

**G1 — `should_answer` never set to False (deterministic pre-gate missing).**
`AnswerContract.should_answer` (`app/rag/generation/answer_contract.py:35`) defaults to `True` and is never assigned `False` anywhere. The contract resolver knows the governing provision and the retrieved chunks, but never deterministically decides "this question should abstain because the named provision is not in context."

**G2 — `repair_prompt` has no abstention branch for missing governing provision.**
`repair_prompt` (`answer_contract.py:139-180`) has branches for `gov and gov.section` (mandatory answer, never abstain) and `elif named` (answer named sections), but the `else` branch (`answer_contract.py:176-180`) says "answer what the context supports" — biasing toward answering even when the named act/section is entirely absent from the retrieved chunks. No explicit directive tells the model to abstain when the governing provision is missing.

**G3 — No pre-generation abstention gate in the linear path.**
`grounded_service.py:generate` (`grounded_service.py:85-182`) always calls the LLM after building the contract and repair prompt. It never checks `contract.should_answer` before `_call_llm`. Even when the answer contract could determine the question is unanswerable, generation proceeds and the lightweight Nemotron model may either refuse (false abstention) or answer from insufficient context (missed abstention).

**G4 — Linear-path abstain (S3) is implemented but default off.**
`RAG_LINEAR_ABSTAIN` (`app/shared/config.py:253-261`) and `LINEAR_ABSTENTION_FLOOR = 0.1` (`app/rag/agent/thresholds.py:31`) exist but `RAG_LINEAR_ABSTAIN` defaults off. The S1 calibration measured `false_abstention_rate = 0.8148` on this path, so it must stay off until cohort validation shows 0.0 false abstention.

Previously completed (per `1793757233191-abstention-improvement-plan.md`):
- S1 threshold calibration — superseded: `groundedness_score` is 1.0 for both abstained and answering qids; no continuous signal separates them. Decision boundary is text regex (`is_abstention`), not a score.
- S2 abstention lexicon V4 — completed (`app/rag/generation/abstention.py:_V4_EXTRA`).
- S4 abstain audit diagnosis — completed (`app/rag/agent/nodes/dag.py`).
- S5 tests — completed (14/14 pass; 5 pre-existing failures unrelated).
- S3 linear-path abstain — implemented behind flag, default off.

## Strategies (ordered by impact vs. false-abstention risk)

### Strategy 1: Deterministic `should_answer` pre-gate (zero LLM cost, highest impact)

**Files:** `app/rag/generation/answer_contract.py`, `app/rag/generation/grounded_service.py`

**Mechanism:**
1. In `AnswerContractResolver.build_contract`, after `_resolve_governing`, if the question names a specific act+section (`c.named_act` and `c.named_sections` both non-empty) and no retrieved chunk matches that act+section, set `c.should_answer = False` and add a `ContractViolation(kind='missing_governing_provision', ...)`.
2. In `grounded_service.py:generate`, after building the contract, if `contract.should_answer is False`, skip `_call_llm` entirely and return an abstention response (answer="", abstained=True, groundedness=0.0). Alternatively, inject a hard abstention directive into the system prompt and make one LLM call.
3. Gate this behind a new config flag `RAG_ABSTAIN_PRE_GATE` (default off for safety, enable after cohort validation).

**Risk:** Low false-abstention regression — only fires when the named provision is genuinely absent from context (the exact missed-abstention case).

### Strategy 2: Abstention branch in `repair_prompt` for missing governing provision

**File:** `app/rag/generation/answer_contract.py`

**Mechanism:**
Add an `elif named and not gov:` branch to `repair_prompt` between the `elif named` and `else` branches:

```python
elif named and not gov:
    extra_parts.append(
        "The question names Section(s) ... but none of those provisions appear "
        "in the <legal_context>. Do NOT answer from related provisions. State "
        "that the governing provision is not present in the context and abstain "
        "— qualify exactly which section is missing."
    )
```

**Risk:** Medium — lightweight model may not follow; mitigate by combining with Strategy 1 (deterministic gate ensures abstention regardless of model compliance).

### Strategy 3: Skip generation when `should_answer=False` (no LLM call)

**File:** `app/rag/generation/grounded_service.py`

**Mechanism:**
After `contract = resolver.build_contract(...)` at `grounded_service.py:103-110`, add:

```python
if contract is not None and not contract.should_answer:
    return self._empty_response(query, query_type, total_latency_ms)
```

This returns an abstention response without consuming an LLM call. Alternatively, inject an abstention directive and make one call.

**Risk:** Zero false-abstention regression — deterministic, only fires on Strategy 1's pre-gate.

### Strategy 4: Enable linear-path abstain (S3) after cohort validation

**Files:** `app/shared/config.py`, `app/rag/agent/thresholds.py`, `app/rag/agent/graph.py`

**Mechanism:**
After validating Strategies 1-3 on the held-out cohort show `false_abstention_rate == 0.0`, flip `RAG_LINEAR_ABSTAIN` default to on (or enable for IE-classified queries only).

**Risk:** Medium — S1 calibration showed 0.8148 false abstention on this path. Enable only after validation.

## Rollout Plan (with small-population guardrail)

### Phase 0 — Small-population test (guardrail: no new regression)

Before touching the full 150-qid pipeline, validate on a small held-out subset (e.g., 10–20 qids spanning both expected-answer and expected-abstain cases):

1. Implement Strategy 1 (deterministic `should_answer` pre-gate) behind `RAG_ABSTAIN_PRE_GATE` (default off).
2. Implement Strategy 2 (abstention branch in `repair_prompt`).
3. Implement Strategy 3 (skip generation when `should_answer=False`).
4. Run `tests/test_answer_contract.py` and `tests/test_abstention_rule.py` — all must pass.
5. Compute baseline false/missed abstention rates on the small population from the existing `s1_pipeline_signals.jsonl` (150-qid run already complete).
6. Run the small-population subset with `RAG_ABSTAIN_PRE_GATE=true` and compute:
   - `false_abstention_rate` (expected-answer qids that wrongly abstain)
   - `missed_abstention_rate` (expected-abstain qids that wrongly answer)
7. **Guardrail check:** If either rate increases vs. baseline on the small population, abort — do not proceed to Phase 1. Revisit the strategy.
8. If both rates are unchanged or improved, proceed to Phase 1.

### Phase 1 — Full rollout

9. Run the full 150-qid S1 signals pipeline (`python -m evaluation.run_s1_pipeline_signals`) with `RAG_ABSTAIN_PRE_GATE=true` and validate:
   - `false_abstention_rate == 0.0`
   - `missed_abstention_rate == 0.0`
10. If both targets met, enable `RAG_ABSTAIN_PRE_GATE` by default.
11. Then evaluate enabling `RAG_LINEAR_ABSTAIN` (Strategy 4) on the held-out cohort.
12. Add CI gate: assert `false_abstention_rate == 0.0` and `missed_abstention_rate == 0.0` on the held-out cohort after any prompt/template/contract change (per `prompt_conditioning.abstention_regression` constraint).

## Validation Plan

- **Unit:** `tests/test_answer_contract.py` — all tests pass. Add test: `build_contract` sets `should_answer=False` when named act+section absent from chunks.
- **Small-population (guardrail):** Run Strategies 1-3 on a 10–20 qid subset spanning both expected-answer and expected-abstain cases. Compute baseline rates from existing `s1_pipeline_signals.jsonl`. Guardrail: neither `false_abstention_rate` nor `missed_abstention_rate` may increase on the subset vs. baseline. If either increases, abort and revisit the strategy.
- **Integration:** Run `python -m evaluation.run_s1_pipeline_signals` on full 150 qids only after the small-population guardrail passes. Compute `false_abstention_rate` and `missed_abstention_rate` from `s1_pipeline_signals.jsonl`. Assert both are 0.0.
- **Regression:** S6 CI gate — any prompt/template/contract change must not increase false or missed abstention above 0.0 on held-out cohort (flip-stability per ADR-0010 §6).

## Decisions Needed Before Implementation

1. **Strategy 1 gate location:** Should `should_answer=False` skip LLM entirely (Strategy 3) or inject an abstention directive and make one LLM call? (Recommended: skip LLM — zero cost, deterministic.)
2. **Config flag:** New `RAG_ABSTAIN_PRE_GATE` flag vs. reuse `RAG_ANSWER_CONTRACT`? (Recommended: new flag for independent rollout control.)
3. **Missing provision detection scope:** Only when `c.named_act` AND `c.named_sections` both present, or also when only `named_act` is present? (Recommended: both required — section-level match is the strongest signal.)
4. **`RAG_LINEAR_ABSTAIN` enablement:** After Strategies 1-3 validate at 0.0, should `RAG_LINEAR_ABSTAIN` be enabled for IE-classified queries only, or all queries? (Recommended: IE-classified queries only — narrower surface.)
5. **Plan file location:** Save to `plans/1794117100031-abstention-way-forward.md` (matching existing convention in `plans/`).

## Open Questions

- Should the missing-governing-provision detection also handle cases where `c.named_act` is present but `c.named_sections` is empty (act-level match only)? This could catch more missed-abstention cases but risks false abstention if act-level match is too loose.
- What abstention response format should Strategy 3 return — empty answer with `abstained=True`, or a short refusal text? The `is_abstention` detector requires ≥20 chars and a negation marker; an empty answer won't be classified as abstention by the regex. Need to ensure the response is recorded as abstained in the audit/log regardless of text content.

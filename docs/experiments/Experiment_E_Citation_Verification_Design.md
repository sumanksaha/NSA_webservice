# EXPERIMENT E — EVIDENCE-BINDING / CITATION VERIFICATION
### (Next experiment after D, per spec §25 — selected from the disambiguated D error taxonomy)

---

## 0. EMPIRICAL BASIS (measured on Experiment D artifacts, 0 LLM calls)

Experiment D's coarse taxonomy was dominated by catch-alls (`other` 442, `unsupported_claim` 229). Before selecting E, the D3 auditor defect texts (649 defects on the 126 still-incorrect III questions) were re-clustered with fine-grained regex patterns:

| Fine-grained cluster | Defects (all 146 q) | Notes |
|---|---:|---|
| **Provision/section/act misattribution** | **163 (25%)** | "cites §42(5) but evidence shows §42(5) deals with…"; §68(1) attributed to the wrong Act; Amendment-vs-Principal-Act confusion (Amendment Act 3-2023) |
| Prohibition/obligation/permission confusion | 109 | modality errors: shall/may/shall-not |
| Hierarchy/conflict (Principal Act vs Amendment) | 129 | overlaps misattribution family |
| Overqualification / hedging added | 132 | auditor-style failure — E removes the auditor layer |
| Unsupported claims (no matching evidence chunk) | 66 | |
| Missing condition / exception / definition | 51 | much smaller than hypothesized |

Key population facts: 130 III questions; 126 still incorrect under D2; **52/126 (41%) carry ≥1 misattribution defect** flagged by the auditor.

**Selection rationale**: misattribution is (a) the largest coherent family, (b) mechanically checkable — the O3 evidence is *closed-world*: every claim's cited chunk is in the prompt, so whether a chunk says what the claim attributes to it is verifiable without external knowledge; (c) not addressed by D2/D3 (the auditor detects it but its free-text corrections drift from the reference conclusion — D's ablation showed replacement lowers scores).

**Why NOT the runner-up categories**: modality confusion (109) and condition/exception omission (51) require genuine legal judgment (LLM-bound, riskier); E's verification layer is deliberately deterministic-first. Missing-condition/exception work is deferred to F if E's residual taxonomy still shows it.

---

## 1. EXPERIMENT OBJECTIVE

Primary question: **"When the same validated O3 evidence is supplied, does verifying that every legal claim is actually supported by the chunk it cites — and repairing only unsupported citations before answering — improve answer correctness over the D2 structured-reasoning baseline?"**

Secondary: does the verification layer improve citation precision/recall and groundedness without the correctness regressions the D3 auditor caused?

## 2. HYPOTHESES

- H1 (primary): E1 > D2 soft correctness. Expected effect size: modest (+0.01–0.03), since misattribution affects ~41% of the hard population but repair is constrained.
- H2: E1 citation precision > D2 citation precision (misattributed citations removed/repaired).
- H3: E1 does not increase abstention or regressions beyond D2's (the D3 failure mode).
- H4 (measurement): the repair loop materially reduces *auditor-detectable* misattribution defects (measured by re-running the D3 auditor prompt offline — free, no new generation budget if scored separately).

## 3. CONDITIONS (fixed; no auditor layer)

| Condition | Architecture | New calls |
|---|---|---|
| D2 | reused from Experiment D verbatim (checkpoint) | 0 |
| **E1** | O3 evidence → structured analysis (D2 prompt, unchanged) → **deterministic citation-check** → **verify-and-repair call** → final answer | 1 per question |
| **E2** | E1 minus the repair call: deterministic check only — unsupported citations *removed*, analysis answered as-is | 0 additional |

- E1 is the intervention. **E2 is free and scientifically necessary**: it isolates how much of the gain comes from the LLM repair vs. simply dropping bad citations.
- **No auditor, no critic, no revision cycle** (explicitly forbidden by the user + §24 outcome of D). Max architecture: Reason → Verify → Repair → Answer.

## 4. THE DETERMINISTIC CITATION CHECK (the core new artifact)

After the D2-style structured analysis, for each claim with a `[n]` source reference:

1. Resolve `[n]` → the exact chunk in the (unchanged, hash-verified) O3 context.
2. Extract the claim text and the cited chunk text.
3. Score support with **string/regex heuristics first** (definition re-use, section-number match: does the chunk contain the section number the claim attributes to it, keyword overlap) — no LLM.
4. Label: `supported | misattributed | unverifiable-by-heuristics`.

The section-number cross-check is the highest-value rule given the taxonomy: if the analysis says "Section 42(5) provides X" but chunk [n] does not contain "42(5)" (or contains "42(5)" with different content), flag as `misattributed`. Amendment-vs-principal-act confusion is caught by Act-name mismatch between claim and chunk.

## 5. VERIFY-AND-REPAIR CALL (E1 only)

Single call per question, fixed prompt, temperature 0.1, same model (`poolside/laguna-s-2.1:free`):

- **Input**: question + O3 evidence + the structured analysis with each flagged claim annotated (`misattributed: claim says §42(5)=X; chunk [n] actually says §42(5)=Y`).
- **Task**: re-bind each flagged claim to the correct citation from the supplied evidence, or delete the claim; **conclusion sentence must be regenerated only if a flagged claim was its sole support**. Explicitly forbidden: introducing new legal positions (the D3 failure mode — corrections must not drift from the analysis).
- **Output**: strict JSON `{repaired_claims: [...], removed_claims: [...], conclusion_unchanged: bool, final_answer: "..."}`.
- Output cap 4096 tokens (the D pathology was 8K+ char rambling audits; repair is a smaller task) with the same truncated-JSON salvage used in D.

## 6. FIXED-CALL BUDGET (§8-style cap)

- **E1: 150 calls max.** E2: 0. **Total cap: 150 new generations** — half of D's budget.
- The D2 analysis is **reused, not regenerated** — E1/E2 consume the stored D2 checkpoint. This is what makes the budget achievable and the comparison clean.
- Accounting identical to D (§9): per-call records with success/transport-backoff distinction, planned vs actual vs failed, reported even if under budget.
- If a question's repair call hard-fails 3 attempts (truncation pathology), record `not_run` for that qid — never manufactured (§16).
- **STOP rule**: if projected calls exceed the cap, stop and report before full execution.

## 7. CONTROLLED VARIABLES (unchanged from D)

Benchmark, questions, question order, O3 evidence (context-hash verification per qid, same as D §11), gold identity, context builder, retrieval, RRF, CE, model, temperature 0.1, prompts (D2 reasoning/answer prompts byte-identical; one new fixed repair prompt), evaluator (`compute_metrics`), leakage protection (§12 — the verifier/repairer sees question + evidence + analysis only; never reference answers).

## 8. PRIMARY METRICS + NEW MEASUREMENTS

All D metrics (soft correctness, binary correct-rate, citation R/P, groundedness, abstention, latency, calls/q). Same evaluator, same code path.

New:
1. **Misattribution repair rate**: of deterministic-flagged claims, fraction repaired to a supported citation (E1) — measured against stored D3 auditor defects as a partial ground truth (the D auditor flagged these independently).
2. **Repair regression rate**: of D2-correct answers, fraction that E1 made incorrect (the D3 lesson — must stay ≈ 0).
3. **E2-vs-E1 gap**: correctness attributable to repair vs mere removal.
4. Defect re-cluster (the D §-0 regex taxonomy) run on E1 outputs to measure misattribution elimination directly.

## 9. ANALYSIS PLAN

- Three-way table: D2 (reused) vs E1 vs E2, full + fair same-subset rows. NO C-O3 rerun; C-O3 rows reused verbatim.
- Per-question transitions D2→E1 and D2→E2 with the D §17 definitions.
- III population (§18): recovery rate on the 130, and specifically on the **52 with auditor-flagged misattribution** (the targeted subgroup — E1's improvement should concentrate there; if it doesn't, the hypothesis is wrong).
- Error taxonomy re-cluster (fine-grained patterns from §0) on E1/E2 — the D3 auditor is *not* re-run for scoring; if re-run, it's measurement-only and labeled as such.
- §24-style decision rule: integrate only if soft correctness improves meaningfully with zero repair-regressions; if E2≈E1, ship the deterministic checker alone (0 extra calls in production).

## 10. DECISION RULE

- E1 improves substantially, E2 doesn't → verification+repair layer is worth integrating.
- E2 ≈ E1 → integrate the deterministic checker only (free, no latency).
- Neither improves → the misattribution family is detectable but not repairable by this model; next experiment targets the runner-up family (modality confusion) via prompt representation, not agents.

## 11. OUTPUTS (§22-style)

`experiment_E_summary.md`, `experiment_E_results.json`, `experiment_E_per_question.jsonl`, `experiment_E_call_accounting.json`, `experiment_E_verification.jsonl` (deterministic check details per claim), `experiment_E_repair.jsonl`, re-clustered `experiment_E_error_taxonomy.json`, transition matrix, 4 plots.

## 12. REQUIRED REPORT QUESTIONS

1. Did claim-level citation verification improve soft/binary correctness over D2?
2. How much came from repair vs removal (E1 vs E2)?
3. Did misattribution defects actually decrease (re-clustered taxonomy)?
4. Was improvement concentrated in the 52 targeted misattribution questions?
5. Repair regressions (must be ~0)?
6. Calls/tokens actually used vs the 150 cap?
7. Integrate into production — full layer, checker-only, or neither?
8. What should Experiment F test?

## 13. PRINCIPLE

D proved that *detecting* errors (auditor) without *constrained repair* regresses answers. E tests the complementary half: **constrained, evidence-bound repair of the single largest mechanically-verifiable defect family**, at half the budget, with no autonomous agents.

---

*Empirical basis: `evaluation/out/ceiling_v5/experiment_D_*` artifacts (D final run 2026-09-23). Taxonomy re-cluster code: this design's §0; reproduce from `experiment_D_d3_checkpoint.jsonl` + `experiment_D_per_question.jsonl`.*

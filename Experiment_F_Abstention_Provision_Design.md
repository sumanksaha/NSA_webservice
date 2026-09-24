# EXPERIMENT F — ABSTENTION CALIBRATION AND PROVISION DISAMBIGUATION

**Status:** design v3 (fully integrated post-150-question human audit from `full_review_worksheet_completed.md` / `full_review_tabulation.json`)  
**Date:** 2026-09-23  
**Budget:** 150 successful LLM generations, hard cap (75 + 75 max; planned repair budget = 31 [F1] + 35 [F2] = 66 calls max)  
**Evaluator:** **evaluator v2** primary (`evaluation/evaluator_v2_overlay.json` + `evaluation/rescore_evaluator_v2.py`); v1 numbers reported alongside for continuity. Answers, contexts, retrieval, base models, and temperatures remain unchanged from D/E.

---

## 1. AIM AND OBJECTIVES

The overarching aim of **Experiment F** is to eliminate the two largest model-side error modes identified in the full-benchmark audit—**unsupported abstention** and **statutory provision mis-selection**—using lightweight, deterministic-first gating and bounded, single-turn repair calls without degrading existing correct reasoning or justified abstentions.

### Specific Objectives:
1. **Calibrated Abstention Recovery (F1 Aim):** Reduce false abstentions on answerable composite/cross-statute questions while rigorously preserving justified abstentions on genuine unanswerable questions (`insufficient_evidence=True`).
2. **Deterministic Provision Disambiguation (F2 Aim):** Correct statutory provision and subsection misattributions (e.g., subsection mismatch, officer/role mismatch, wrong Act family) via constrained re-decision without altering underlying valid legal logic or regressing already-correct citations.
3. **Budget & Architecture Efficiency:** Achieve substantive performance headroom (acting on ~73% of model-side errors) with $\le 1$ extra generation per flagged question ($\le 66$ total planned calls across the 150-question benchmark), completely avoiding costly iterative revision loops or noisy model auditors.

---

## 2. SCOPE AND BOUNDARIES

### In Scope:
- **Layer 1 (F1) — Abstention Calibration:** 
  - Target population: **31 audit-confirmed questions** exhibiting unsupported refusal/abstention (`abstention_gate`).
  - Strict preservation of the **15 genuine `insufficient_evidence=True`** questions (zero forced answers permitted).
- **Layer 2 (F2) — Provision Disambiguation:** 
  - Target population: **35 audit-confirmed questions** exhibiting subsection, officer-role, or statute-family mis-selection (`provision_check`).
  - Constraint check preventing regression on the 5 already-correct/partially-correct F2 instances (Q015, Q024, Q033, Q125, Q129).
- **Composite Condition (F1 + F2):** Combined execution on disjoint target sets ($F_1 \cap F_2 = \emptyset$), requiring at most 66 substantive repair calls.

### Out of Scope (Explicit Boundaries):
- **Deep Legal Reasoning & Statutory Interpretation (Experiment G Track):** The **23 questions** flagged as `needs_deeper_reasoning` (19 model-wrong + 4 other) requiring multi-hop synthesis, exception nesting, or conflict-of-law reconciliation are deferred to Experiment G.
- **Reference & Evaluator Widening (Evaluator v3 Track):** The **61 questions** categorized under `fix_reference` / `evaluator_miss` (57 evaluator misses + 4 narrow references) are handled in the parallel evaluation calibration track (`evaluator_v3_candidates.json`), not via generation-side patching.
- **Auditor / Critique Loops:** No multi-agent debate or open-ended model self-critique (empirically proven noisy in D and E).
- **Retrieval / Context Changes:** No retriever modifications, cross-encoder retraining, or prompt changes to upstream stages C/D.

---

## 3. FULL-AUDIT GROUND TRUTH & MOTION FROM EVIDENCE

The complete 150-question human audit (`evaluation/out/ceiling_v5/full_review_tabulation.json`) established the failure breakdown across the benchmark:

```
Total Questions: 150
Human Correctness: 58 Correct (38.7%) | 42 Partial (28.0%) | 50 Incorrect (33.3%)
Benchmark Verdicts:
├── Model Wrong: 89 (59.3%) ─── Primary Model Lever
│   ├── F2 Provision Check: 35 (39.3% of model errors)
│   ├── F1 Abstention Gate: 31 (34.8% of model errors)
│   └── Needs Deeper Reasoning: 23 (25.8% of model errors -> Exp G)
└── Evaluator Miss / Narrow: 61 (40.7%) ─── Evaluator v3 Track
```

**Key Implications:**
1. **73.0% of all model errors (65/89)** are directly addressed by F1 and F2.
2. **Evaluator-v2 Trajectory:** Baseline scoring under evaluator v2 showed steady gains (C-O3: 0.1067 $\rightarrow$ D2: 0.1216 $\rightarrow$ E1: 0.1301). Experiment F provides the first intervention targeting the double-digit headroom (theoretical upper bound $\approx 0.52$; realistic target $0.30 - 0.38$).

---

## 4. RESEARCH QUESTIONS

- **FQ1 (Abstention Calibration):** Can deterministic coverage gating combined with an evidence-bounded recovery call eliminate false abstentions on answerable composite questions while strictly preventing hallucinations and maintaining 100% precision on unanswerable questions?
- **FQ2 (Provision Disambiguation):** Can deterministic structural validation (subsection matching, officer-role binding, Act-family checks) and a single constrained re-decision call correct statutory citations with **zero provision regressions** on already-correct answers?

---

## 5. EXPERIMENTAL CONDITIONS

1. **F0 — Baseline:** Unmodified D2 checkpoint generations (0 calls), rescored under Evaluator v2.
2. **F1 — Abstention Calibration:** Deterministic gate $\rightarrow 1$ bounded recovery call on gated questions (up to 31 calls).
3. **F2 — Provision Disambiguation:** Deterministic candidate mismatch check $\rightarrow 1$ bounded re-decision call on flagged questions (up to 35 calls).
4. **F1 + F2 — Combined Layer:** Joint application on disjoint target partitions (up to 66 calls total).

---

## 6. LAYER 1 DESIGN: ABSTENTION CALIBRATION (F1)

### 1. Deterministic Gating (0 LLM calls)
A D2 answer is flagged for recovery iff:
1. `abstain_check` evaluates to `True` (standard refusal regex), **AND**
2. Question is **NOT** marked as `insufficient_evidence=True` (preserves justified abstentions), **AND**
3. Retrieved context contains $\ge 1$ gold-family chunk as recorded in the question coverage metadata.

*Audit Target Volume:* **31 questions** (29 machine-incorrect + 2 baseline-correct Q047/Q107).

### 2. Constrained Recovery Call (Max 1 call / question)
- **Input:** Question + Retrieved Context + D2 Structured Analysis (excluding gold references).
- **Prompt Directive:** *"Your previous analysis abstained. The evidence supplied contains the requisite statutory domain. Either provide the substantive legal conclusion directly from the supplied text or, if and only if a specific statutory element is genuinely missing from the text, explicitly name that missing element."*
- **Output Schema:**
  ```json
  {
    "answer": "string",
    "missing_element_if_any": "string | null",
    "confidence": "high | medium | low"
  }
  ```
- **Handling:** If `missing_element_if_any` is specified, the answer remains an abstention. Otherwise, the substantive answer is emitted.

### 3. Anti-Hallucination Guard (Deterministic)
Recovered answers are passed through the standard `grade_answer` hallucination check. If flagged, the recovery is rejected, reverted to abstention, and logged as `recovery_rejected`. Hallucination rate must remain $< 10\%$.

---

## 7. LAYER 2 DESIGN: PROVISION DISAMBIGUATION (F2)

### 1. Deterministic Candidate Validation (0 LLM calls)
A D2 answer is flagged for provision re-decision iff the cited provisions in the structured analysis fail any structural check:
1. **Subsection Mismatch:** Section is present in evidence, but cited subsection is absent from chunk text (using E's validated section-inventory parser).
2. **Officer / Role Mismatch:** The analysis names an officer/tribunal whose co-occurrence in the evidence binds to a different section (e.g., Designated Officer under §31 vs Adjudicating Officer under §32).
3. **Act-Family Discrepancy:** Answer cites an Act family outside the gold family candidates.

*Audit Target Volume:* **35 questions** (30 machine-incorrect + 5 baseline-correct Q015/Q024/Q033/Q125/Q129).

### 2. Constrained Re-Decision Call (Max 1 call / question)
- **Input:** Question + Retrieved Context + Previous Structured Analysis + Specific Gating Diagnostic (e.g., *"Cited §X subsection Y not found; available subsections in evidence are [Y1, Y2]. Verify and re-bind."*).
- **Constraint:** Forbidden from introducing new unsupported legal stances; restricted to minimal citation/provision realignment.
- **Output Schema:** Corrected statutory binding + minimal patched answer.

---

## 8. BUDGET & EXECUTION CONSTRAINTS

| Item | Planned Target | Hard Cap (incl. Retries) |
|---|---:|---:|
| F1 Recovery Calls | 31 | 75 |
| F2 Re-Decision Calls | 35 | 75 |
| **Total Experiment F Generations** | **66** | **150** |

- Transport / timeout backoffs logged separately; max 3 retries per failed HTTP transport before marking `not_run`.
- All generation records logged with standard schema (`stage` $\in$ `[abstention_recovery, provision_redecision]`).

---

## 9. METRICS & PRE-REGISTERED SUCCESS GATES

### Primary Evaluation Metrics:
1. **Binary & Soft Correctness (Evaluator v2):** Comparison across F0, F1, F2, and F1+F2.
2. **False Abstention Reduction:** Net reduction in unwarranted refusals (target: $\ge 20$ of 29 machine-incorrect F1 cases converted to correct substantive answers).
3. **Justified Abstention Preservation:** 100% preservation of the 15 `insufficient_evidence` questions (0 regressions).
4. **Provision Accuracy Gain:** $\ge 15$ of 30 machine-incorrect F2 cases fixed.
5. **Provision Regression Rate:** Zero regressions on already-correct answers (specifically preserving Q015, Q024, Q033, Q125, Q129).
6. **Hallucination-Revert Rate:** $< 10\%$ of recovered answers rejected by hallucination filters.

### Success Gates:
- **F1 Layer Passes** iff: $\ge 20 / 29$ false abstentions recovered, 0 regressions on insufficient-evidence questions, and binary v2 gain $\ge +0.02$.
- **F2 Layer Passes** iff: $\ge 15 / 30$ provision errors corrected with 0 regressions on audit-verified correct answers.
- **Overall Experiment F Passes** iff: Combined F1+F2 yields binary v2 score $\ge 0.30$ (up from D2: 0.1216 / E1: 0.1301) within the 150 generation cap.

---

## 10. DECISION RULES & ARCHITECTURAL ROADMAP

- **If F1 + F2 Both Pass:** Adopt composite architecture: **D2 Reasoning $\rightarrow$ Calibrated Abstention $\rightarrow$ Deterministic Provision Verification** ($\le 0.44$ extra calls/question). Proceed to integrate with Evaluator v3.
- **If Only F1 Passes:** Deploy Abstention Calibration layer independently (yields immediate high-confidence recovery on 20+ questions for 31 calls).
- **If Only F2 Passes:** Deploy Provision Verification layer independently.
- **If Neither Passes:** Model errors require deep semantic representation rather than surface calibration; pivot directly to Experiment G (deeper reasoning and chain-of-statute synthesis).

---

## 11. REPOSITORY ARTIFACTS & IMPLEMENTATION FILES

- **Design Specification:** [`Experiment_F_Abstention_Provision_Design.md`](file:///C:/github/NSA_webservice/Experiment_F_Abstention_Provision_Design.md)
- **Ground Truth Tabulations:** [`evaluation/out/ceiling_v5/full_review_tabulation.json`](file:///C:/github/NSA_webservice/evaluation/out/ceiling_v5/full_review_tabulation.json), [`evaluation/out/ceiling_v5/full_review_tabulation.md`](file:///C:/github/NSA_webservice/evaluation/out/ceiling_v5/full_review_tabulation.md)
- **Evaluator v2 Specification:** [`evaluation/evaluator_v2_overlay.json`](file:///C:/github/NSA_webservice/evaluation/evaluator_v2_overlay.json), [`evaluation/rescore_evaluator_v2.py`](file:///C:/github/NSA_webservice/evaluation/rescore_evaluator_v2.py)
- **Execution Script & Pipeline:** `evaluation/experiment_f_calibration_eval.py`, `evaluation/run_experiment_f_full.sh`

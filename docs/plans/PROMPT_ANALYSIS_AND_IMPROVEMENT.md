# Prompt Analysis and Improvement Assessment

**Date:** 2026-11-14  
**Context:** Grounded QA Prompt Template (`app/rag/generation/prompt_template.py`), Evaluation Harness (`evaluation/rerun_gold_in_prompt.py`), and Preregistration Gates (`docs/plans/PROMPT_SOFT_BINARY_LIFT.md`).

---

## 1. Executive Summary

An evaluation of `GROUND_QA_SYSTEM_PROMPT` and `GROUND_QA_USER_TEMPLATE` against the benchmark dataset and historical prompt runs reveals a fundamental tradeoff between **answer coverage** and **answer length (Jaccard penalty)**.

The evaluation metric is defined as:
$$\text{soft} = \frac{\text{jaccard} + \text{coverage}}{2}$$
where binary correctness requires $\text{soft} > 0.50$.

Because Jaccard's denominator grows with total response token length, verbose answers dilute Jaccard faster than they gain coverage. However, aggressive brevity risks sacrificing essential statutory references. Improving performance requires **answer shaping**—retaining operative legal tokens while trimming structural scaffolding, meta-narration, and quote bloat.

---

## 2. Root Cause Analysis & Empirical Dynamics

### 2.1 The Token Value Asymmetry
Across benchmark questions ($N \approx 176$ average output tokens, $m \approx 19.3$ average reference tokens):
* **Coverage sensitivity:** $\frac{\partial(\text{coverage})}{\partial(\text{token})} = \frac{1}{29} \approx 0.0345$
* **Jaccard sensitivity:** $\frac{\partial(\text{jaccard})}{\partial(\text{token})} = -\frac{m}{(N - m)^2} \approx -0.00038$
* **Exchange rate:** **1 reference token is worth $\approx 90$ non-reference filler tokens**.
* Content must never be traded for length: shedding reference tokens to save words destroys soft score faster than the reduced denominator helps.

### 2.2 The Step 1 Quote Scaffold Dilemma
* The shipped two-step prompt (`Step 1 — Quotes` $\to$ `Step 2 — Answer`) incurs massive volume overhead: Step 1 quotes constitute **$51\%$ of total answer volume** (median 259 quote tokens vs. 244 prose tokens).
* The Step 1 quote block contributes only **1.32 unique reference tokens** (114 unique tokens total), while Step 2 prose contains **18.0 of the 19.3 reference tokens ($93\%$)**.
* However, eliminating quotes completely (`onestep` variant) caused a catastrophic coverage drop ($\Delta \text{coverage} = -0.229$), demonstrating that models rely on the extraction step as an anchor for accurate reasoning.

### 2.3 The Real Lexical Gap (`soft_token_gap.py`)
Token breakdown of missing reference tokens on $n=150$ runs:
* **Ordinary operative prose (`other`):** **$81.1\%$** (1,182 tokens)
* **Concepts / defined terms:** $9.9\%$ (145 tokens)
* **Section references:** $5.7\%$ (83 tokens)
* **Penalty figures / amounts:** $1.8\%$ (26 tokens)
* **Authorities:** $1.5\%$ (22 tokens)

**Conclusion:** Prompt interventions focused solely on exact section numbers or penalty figures address $< 10\%$ of the gap. The primary deficit is paraphrasing operative legal terms into conversational English rather than using statutory phrasing directly.

---

## 3. Key Areas of Improvement

### 3.1 Step 1 Quote Budgeting
* **Current Issue:** Prompts instruct the model to quote bearing passages without length bounds, encouraging extraction of complete sub-sections and boilerplate definitions.
* **Target Improvement:** Constrain Step 1 to single-line micro-quotes:
  * Maximum 15–20 operative words per source.
  * Enforce format: `[n] <operative clause only>`.
  * Explicitly forbid reproducing entire sections or schedule lists.

### 3.2 Exact Statutory Phrasing vs. Paraphrasing
* **Current Issue:** The model paraphrases specific statutory vocabulary (e.g., writing *"officer appointed by the commissioner"* instead of *"Designated Officer"*, or *"food not meeting standards"* instead of *"sub-standard food"*), forfeiting high-leverage reference tokens.
* **Target Improvement:**
  * Require literal copying of defined roles, statutory thresholds, and legal consequences directly from the operative clause.
  * Direct the model to avoid synonyms or explanatory restatements.

### 3.3 Zero Meta-Preamble & Structural Overhead
* **Current Issue:** LLMs frequently prepend reasoning plans, disclaimers, or restatements of context instructions (costing 30–60 non-reference tokens).
* **Target Improvement:**
  * Enforce immediate output starting with the explicit heading `Step 1 — Quotes`.
  * Add strict negative constraints: *"Do not describe your plan, reasoning, or restate instructions; write nothing before the first heading."*

### 3.4 Abstention Boundary Calibration
* **Current Issue:** Past iterations of hard quote-gating led to severe false abstentions ($0.07 \to 0.44$). The blanket-refusal instructions are wordy and repetitive.
* **Target Improvement:**
  * Streamline refusal guidance: answer all covered sub-parts; for missing elements, name the specific unaddressed issue and citations checked rather than issuing a blanket refusal for the entire query.

---

## 4. Verification & Evaluation Protocol

All prompt modifications must be verified against the **Gate Set v2** preregistration in `docs/plans/PROMPT_SOFT_BINARY_LIFT.md`:

| Metric Gate | Requirement | Purpose |
|---|---|---|
| **Soft $\Delta$ ($n=150$)** | $\ge +0.02$ | Genuine overall accuracy improvement |
| **Coverage $\Delta$ ($n=150$)** | $\ge +0.02$ | Confirms gains stem from content, not length deflation |
| **Median Answer Length $\Delta$** | $\le +10\%$ vs control | Prevents answer bloat and Jaccard dilution |
| **Binary Correctness Net** | $> 4$ | Confirms clearance of the $\text{soft} > 0.50$ threshold |
| **Abstention Rate** | Non-increasing | Prevents silent refusals on answerable questions |
| **Citation Precision / Recall** | Recall not degraded; precision $\ge 0.25$ | Ensures citation grounding integrity |

### Execution Commands
```powershell
# 1. Run evaluation on candidate prompt variant (e.g. armshape_v2)
python -m evaluation.rerun_gold_in_prompt --template-variant armshape_v2 --tag armshape_v2_eval

# 2. Compare against baseline shipped prompt under Gate Set v2
python -m evaluation.compare_rerun_arms --ctrl shipped --test armshape_v2_eval
```

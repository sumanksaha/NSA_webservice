# Experiment E — Evidence-Binding / Citation Verification

Stub validation: **False**

## Headline (design sec 8)

| System | n | Soft Correctness | Binary Correct | Cit R | Cit P | Groundedness | Abstain | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| D2 | 148 | 0.3835 | 0.0946 | 0.7202 | 0.7442 | 0.8716 | 0.1081 | 76537 |
| E1 | 146 | 0.3844 | 0.1027 | 0.7266 | 0.7516 | 0.8767 | 0.1027 | 0 |
| E2 | 148 | 0.3834 | 0.0946 | 0.7247 | 0.7499 | 0.8851 | 0.1081 | 0 |

## Repair safety (design sec 8 — must be ~0)
- D2-correct answers: 14 | E1 repair regressions: **0** | E1 gains: 1
- E2 removal regressions: **0** | E2 gains: 0
- Flagged questions: 18 | E1 improved on flagged: 1 | E1 regressed on flagged: 0

## Transitions
- D2 -> E1: {'unchanged': 145, 'not_run': 4, 'improved': 1}
- D2 -> E2: {'unchanged': 148, 'not_run': 2}

## Call accounting (design sec 6)
- Cap: 150 | successful: 18 | failed attempts: 27 | budget respected: True
- Tokens: in=482455 out=265922
- Repair calls skipped (no flags): 128

## Decision rule (design sec 10)
- E1 > D2 meaningfully + E2 < E1 -> integrate verification+repair.
- E2 ~ E1 -> integrate the deterministic checker only (0 production calls).
- Neither -> misattribution detectable but not repairable; next target: modality confusion via representation, not agents.

## Post-run analysis (final, 146/148 E1; Q016/Q052 not_run — pathological JSON)

### The decisive mechanistic finding: adjudication stats
Of 34 flagged claims that reached the repair call: **repaired = 1, removed = 1, kept (false alarms) = 32**; `conclusion_unchanged` on 18/18. The repair LLM — reading the full cited chunk — judged **94% of the deterministic checker's flags to be false alarms**. Claim-level misattribution, strictly defined, is rare (<3% of checked claims) in D2 analyses.

Three judges, three answers: the D3 auditor alleged 163 misattribution defects; the regex checker flagged 37; chunk-level adjudication sustains 2. The consistent conclusion: **the "misattribution family" in D's error taxonomy was largely a detection artifact** (both the auditor and the regex fail to match paraphrases to chunk text), not a real defect class at that prevalence.

### Verdict per the design decision rule (sec 10)
- Full benchmark: E1 +0.0012 soft / +0.008 binary (1 net gain, Q129) vs D2 — **not meaningful**.
- Flagged subgroup (n=18): E1 +0.0094 soft — small positive exactly where it intervenes; E2 (removal-only) +0.0006 — nothing.
- **Repair safety: 0 regressions, 0 abstention increase** — the D3 failure mode was fully avoided. The verify-and-adjudicate architecture is safe but not additive.
- Branch fired: *"Neither improves → don't integrate."* The checker-only (E2) branch is also rejected: removal without adjudication loses citation credit and changes nothing.

### Consequence for Experiment F (design sec 12, Q8)
1. The prevalence estimates from auditor-defect text are **unreliable as ground truth** — D's second-largest family was mostly phantom. Any F design must first establish defect prevalence via chunk-level adjudication, not auditor self-report.
2. With evidence sufficiency (C) and citation soundness (E) now effectively ruled out as bottlenecks, and structured reasoning (D) not converting, the residual failure mass sits in **legal interpretation/application** (modality, condition application) — or in the benchmark's reference-conclusion alignment itself.
3. Therefore the §20 human audit sample (30–50 questions) is now the highest-value next action: it must distinguish "model legally wrong" from "reference conclusion too narrow" before any further LLM budget is spent. Binary correct ~9.5% vs soft ~0.38 with sound citations is exactly the signature of a possible evaluator-alignment ceiling.

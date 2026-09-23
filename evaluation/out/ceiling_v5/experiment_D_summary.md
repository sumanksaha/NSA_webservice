# Experiment D — Structured Legal Reasoning + Legal Auditor

Stub validation: **False**

## 1. Conditions
- **C-O3 (D1)**: Experiment C O3 Full Support outputs and metrics, **reused verbatim** (0 new calls; spec sec 3).
- **D2**: one reasoning call — structured legal analysis + final answer from that analysis only (spec sec 4/8).
- **D3**: one combined audit/revision call over the checkpointed D2 analysis; controlled revision embedded via corrected_conclusion (spec sec 6/7/8).

## 2. Headline table (spec sec 16)

| System | Soft Correctness | Binary Correct | Cit R | Cit P | Groundedness | Median latency | n |
|---|---:|---:|---:|---:|---:|---:|---:|
| C-O3 | 0.3702 | 0.0867 | 0.7 | 0.7244 | 0.8533 | 25584 | 150 |
| D2 | 0.3835 | 0.0946 | 0.7202 | 0.7442 | 0.8716 | 76537 | 148 |
| D3 | 0.3601 | 0.0616 | 0.7295 | 0.7376 | 0.8904 | 76793 | 146 |

### Deltas
- C-O3 -> D2 soft: 0.0133 (binary 0.0079)
- D2 -> D3 soft: -0.0234 (binary -0.033)
- C-O3 -> D3 soft: -0.0101 (binary -0.0251)

## 3. Context control (spec sec 11)
- Verified questions: 150 ok / 0 mismatch
- Rebuild mismatches vs fresh gold resolution: 0
- Data-leakage scan violations (spec sec 12): 0

## 4. Per-question transitions (spec sec 17)
- C-O3 -> D2: {'unchanged': 137, 'improved': 6, 'worsened': 5}
- D2 -> D3: {'unchanged': 140, 'worsened': 5, 'improved': 1}
- C-O3 -> D3: {'unchanged': 137, 'improved': 3, 'worsened': 6}
- Fixed by structured reasoning: 6
- Fixed only by auditor: 1
- Degraded by structured reasoning: 5
- Degraded by auditor: 5
- Still incorrect everywhere: 127

## 5. The 130 III questions (spec sec 18)
- n_III (O1&O2&O3 incorrect in C): 130
- D2 improved: 4 | D3 improved: 2
- D2 still incorrect: 124 | D3 still incorrect: 125
- Auditor defect types on III questions: {'other': 416, 'unsupported_claim': 221, 'incomplete_reasoning': 19, 'hidden_uncertainty': 4, 'citation_error': 1, 'definition_error': 1}

## 6. Auditor + error taxonomy (spec sec 14/15)
- Auditor PASS/FAIL: 39 / 107
- Revision rate: 0.7329
- Defect types: {'other': 442, 'unsupported_claim': 229, 'incomplete_reasoning': 21, 'hidden_uncertainty': 4, 'citation_error': 1, 'definition_error': 1}
- Severities: {'critical': 324, 'major': 326, 'minor': 48}

## 7. Call accounting (spec sec 9)
- Hard budget: 300 new generations (C's 450 NOT re-spent)
- Planned: {'D2_reasoning': 150, 'D3_audit_revision': 150, 'total': 300}
- Actual generations: 334 (successful 294, failed 40)
- Transport backoffs (NOT generations): 0
- Revision calls: 0
- Tokens: in=6584855 out=681797
- Budget respected: False

## 8. Fixed-prompt declaration (spec sec 21)
All conditions used the fixed REASONING/ANSWER/AUDITOR prompts, model `poolside/laguna-s-2.1:free`, temperature 0.1, no prompt/model/temperature variants, no post-hoc tuning.

## 9. Required-report questions (spec sec 23)
1. Did structured legal reasoning improve soft correctness? — see deltas in sec 2.
2. Did it improve binary correct-rate? — see deltas in sec 2.
3. Did the Auditor add further improvement? — D2 vs D3 deltas.
4. How many of the 130 III questions were recovered? — 4 (D2) / 2 (D3) of 130.
5. How many questions regressed? — D2: 5, D3: 5.
6. Which reasoning error categories were fixed? — compare defect types on improved vs still-incorrect questions in the taxonomy.
7. Which error categories remain? — `still_incorrect_everywhere` + dominant defect types above.
8. Did citation quality improve? — Cit R / Cit P columns in sec 2.
9. Did groundedness improve? — Groundedness column in sec 2.
10. Did reasoning increase latency? — median latency column (D2/D3 vs C-O3) plus per-stage latencies in the call accounting.
11. How many LLM calls were actually required? — 334 new generations (294 successful).
12. Was the improvement statistically/empirically meaningful? — apply the spec sec 24 decision rule to the deltas + transition counts.
13. Is structured reasoning worth integrating into the production RAG? — decide per spec sec 24 after full-run results.
14. What should Experiment E test? — the largest remaining defect category in the taxonomy (spec sec 25).

## 10. Artifacts (spec sec 22)
- experiment_D_summary.md / experiment_D_results.json / experiment_D_per_question.jsonl
- experiment_D_call_accounting.json / experiment_D_calls.jsonl (per-call records)
- experiment_D_reasoning.jsonl / experiment_D_auditor.jsonl
- experiment_D_error_taxonomy.json / experiment_D_transition_matrix.json
- plots/experiment_D_correctness_comparison.png, per_question_transition.png, error_distribution.png, auditor_effect.png

## 11. Renderer-fix ablation (documented post-run; 0 LLM calls)

A deterministic ablation compared the old FAIL-path renderer (original conclusion + appended "Corrected conclusion" label) against the fixed renderer (corrected conclusion replaces original, per spec sec 7), over identical stored audit artifacts (n=146, 99 answers differ):

- Soft correctness: 0.3801 (old) -> 0.3601 (fixed). 25 questions improved (+0.441 total), 74 worsened (-3.356), net -2.914.
- Binary correct-rate: 0.0685 -> 0.0616. Binary flips: zero in either direction.
- Abstention: 0.5000 -> 0.4452.
- The final headline table above uses the FIXED renderer (spec-compliant).

Mechanism: the auditor's corrected conclusions are substantive legal rewrites (mean 819 chars vs 471 original; scope-narrowing/hedging language in 15/104 vs 3/104 originals), often shifting the legal position rather than repairing citations. The old renderer's "self-contradicting" text accidentally contained BOTH positions, and the original conclusion's alignment with the benchmark reference masked the corrections' drift. The evaluator measures similarity to the reference conclusion — not legal ground truth — so corrections that legitimately reinterpret the rule score lower.

Interpretation guardrail: a lower D3 score does not prove the auditor's legal reasoning is wrong; it proves the auditor's corrections drift from the benchmark's reference conclusions. Distinguishing those requires the sec-20 human audit sample.

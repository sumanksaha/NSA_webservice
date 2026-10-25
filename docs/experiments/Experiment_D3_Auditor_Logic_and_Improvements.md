# D3 Auditor: Logic and Improvements

**Date:** 2026-09-23
**Source of the logic:** `evaluation/experiment_d_reasoning_eval.py` (`AUDITOR_SYSTEM_PROMPT`, `validate_audit`, `normalize_audit`, `run_d3_question`, `build_final_answer`, `repair_citation_markers`).
**Measured outcome:** D2 soft 0.3835 / binary 0.0946 → D3 soft 0.3601 / binary 0.0616. Auditor PASS/FAIL 39/107. Renderer ablation: soft 0.3801 → 0.3601, 74 of 99 changed answers worsened.

## What the auditor actually does

`run_d3_question` sends the question, the full O3 context, and the entire D2 analysis JSON to `AUDITOR_SYSTEM_PROMPT`. There is no second revision call. `render_revision_prompts` exists and is only used in a dry-run prompt dump; the live path is audit-only.

The prompt gives the model fifteen checks and one rule: any "material" defect means `FAIL`, and `FAIL` must include `corrected_conclusion` in one or two sentences. The checks run from the governing Act and section through definitions, conditions, provisos, cross-references, fact mapping, general-versus-specific, prohibition-versus-permission, whether the conclusion follows, unsupported claims, citation attachment, hidden uncertainty, and incomplete reasoning.

`validate_audit` accepts the object if `status` is `PASS` or `FAIL`. It does not require a defect, a quote, or a corrected conclusion. `normalize_audit` then coerces every defect type outside `_DEFECT_TYPES` to `other`, and every bad severity to `minor`. That enum is not in the prompt, so the model's labels mostly collapse. The run produced 442 `other` defects, 324 marked critical and 326 major, and 107 FAILs out of 146.

`build_final_answer` is where the score moves:

- `PASS` keeps D2's `legal_conclusion`.
- `FAIL` with a non-empty `corrected_conclusion` replaces that conclusion. The original text is not kept.
- `FAIL` with an empty correction keeps the original and appends up to three "Note:" lines.
- Either way, up to three of D2's own `uncertainties` are appended as caveats onto the replacement.
- If the resulting text has no `[n]` markers, `repair_citation_markers` appends every marker found in the analysis or the audit, including defect descriptions. That repairs citation recall without the model having cited anything.

A subjective checklist item becomes a full legal rewrite, then the old caveats are glued back on, and citation credit can be restored mechanically. The replacement text averaged 819 characters against 471 for D2.

## Why that logic fails

The same model that wrote the analysis is asked to find something wrong with it, against fifteen open criteria, with "incomplete" and "hidden uncertainty" as sufficient grounds for `FAIL`. Those last two items reward hedging. Nothing in the parser checks that a defect's `evidence` is a span of a numbered source, so a fluent criticism passes. The prompt also tells it to write a new conclusion rather than name the broken sentence. Once `status == "FAIL"`, the renderer does not look at severity, defect type, or whether the new conclusion quotes the evidence. A minor, untyped, unquoted defect can replace the answer.

## What to change

Keep the auditor as a defect reporter. Stop using it as the answer.

1. **Put the closed defect enum in the prompt** and reject the audit JSON unless every defect uses one of those types. The code already has the enum; the model never sees it, which is why the taxonomy is `other`.

2. **Make `FAIL` structurally hard.** Accept `FAIL` only when there is at least one defect of severity `critical`, its `evidence` string is a verbatim substring of a numbered source, and `corrected_conclusion` is either empty or itself quotes that span. Otherwise force `PASS` and keep D2. This is the check `validate_audit` does not do today.

3. **Drop checklist items 14 and 15** (hidden uncertainty, incomplete reasoning). They have no decidable test and are what produced the longer hedged essays. Keep the provision, definition, condition, and citation checks only if each one names the source span that contradicts the analysis.

4. **Do not replace the answer inside the audit call.** On an accepted `FAIL`, patch the cited sentence or leave D2 standing. A rewrite, if still wanted, is a separate call that receives only the quoted span and the rejected sentence, and it is discarded unless its citation is a substring of that span and the already-correct D2 answers do not regress.

5. **Stop appending D2 uncertainties and borrowed `[n]` markers onto a replaced conclusion.** The caveat block reattaches the analysis the auditor just rejected. The marker repair assigns citation recall to an answer that cited nothing.

The measured result of the current auditor is a 73% fail rate and a lower score. The useful remnant is a typed defect list that can be checked against a source span. The replacement conclusion is the part to remove.

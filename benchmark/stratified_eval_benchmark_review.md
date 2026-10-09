# Stratified RAG Benchmark Review

## Source
- Original: `stratified_eval_sample_v1(1).json`
- Original questions: 80
- Cleaned benchmark questions: 45

## Disposition
- Keep: 2
- Repair: 43
- Reject: 35

## Recommended scoring
- **2** — legally correct answer that directly addresses the cited provision.
- **1** — substantially correct but incomplete or minor qualification issue.
- **0** — incorrect or unsupported.
- **N/A** — invalid/ill-posed benchmark question.

## Important benchmark rule

Do not force a numerical penalty or imprisonment answer when the cited provision does not prescribe one. A response identifying that the premise is invalid should receive full credit where appropriate.

## Pipeline recommendation

Before generating a question, classify the provision as one of:

`definition | duty | power | prohibition | procedure | penalty | offence | punishment | appeal | jurisdiction | licensing | registration | standard | regulation | rule | schedule`

Then choose a question template appropriate to that provision type.

## Files

- `stratified_eval_sample_v2_cleaned.json` — scoring-ready cleaned set.
- `stratified_eval_sample_v2_audit.json` — complete 80-item audit with status, reason, and recommended question.

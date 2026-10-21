# Jev per failure class (future work)

Date: 2026-10-16
Status: suggestion, not scheduled
Related: `evaluation/FINDINGS_89_STATUS.md`, `docs/POSSIBILITY_PIPELINE_QUESTION_JEV_LAGUNA.md`

Jev (`typesafe/jev-1.13`) answers one question: for each retrieved chunk, P(keep) as evidence for this query. It does not write the answer, and it cannot supply text that is not in the candidate pool. Use it differently on each failure class from `failure_attribution.json` and `FINDINGS_89_STATUS.md`.

## Retrieval-rank (26 of 89)

Gold is in the pool and missed the packed window. This is the class Jev is built for.

1. Score the existing `C_hybrid` reservoir with `evaluation/decision_scores.py` (Decisions API, cached in `evaluation/out/decision/keep_scores_v1.jsonl`).
2. Keep chunks above a tau from `tau_sweep.py`, then pack that set. Compare with `ab_decision_selector.py` against the current packer on the full 150, not only the 89.
3. Treat Jev as a filter on the pool you already have. A pointwise keep score will not assemble a multi-provision set (rule plus proviso plus penalty). If the question needs several gold concepts, rank by P(keep) and then fill the set by concept coverage, the way `evidence_set_selector.py` does.

Ship a tau only if abstentions on the 61 control questions stay at or below baseline.

## Generation (60 of 89)

Gold is already in the prompt and the answer is still wrong. Another keep/drop pass on those same chunks will not change the answer. Laguna already saw the provision.

Use Jev only as a retry gate:

- If the kept set covers the gold concepts and the authority, do not retry. The loss is generation. A second Laguna pass needs a specific missing-concept list, with a max of one retry.
- If Jev’s kept set drops the gold chunk, that is a selector regression. Do not let that tau through.

Do not ask Jev to score “is this answer correct.” It was calibrated on chunk keep/drop. The instruction says the snippet is truncated and completeness is out of scope.

## No usable evidence (about 30 of 89)

The operative section body is absent, or the window holds an amendment footnote stamped as that section. Every candidate is the wrong text.

Jev will still assign a high P(keep) to the footnote. The criterion is topical relatedness (“In section 63, for the words…”), and the snippet is judged for relevance, not for being the operative rule. Using that score to select evidence makes the wrong citation more confident.

Before Jev sees the pool, drop amendment-notification chunks (stamp fix already measured: poisoned stamps 141 to 0). If nothing remains with both a real body and P(keep) above tau, abstain. Do not fall back to the best leftover stub.

Re-ingest is what fixes this class. Jev cannot.

## Grader misses (38% of the wider 150)

These are not model failures. Do not run them through Jev to chase a higher soft score. Adjudication (all gold concepts, authority hit, not abstained) stays the accept rule.

## What not to do

| Temptation | Result |
| --- | --- |
| Jev picks the query decomposition | No labels for which rewrite retrieves gold. Overfits the 89. |
| Jev replaces the cross-encoder | Different objective. Duplicates rerank work and ignores complementarity. |
| Inject KG pointers, then let Jev keep them | Median KG chunk is about 85 characters. Jev will keep a section title as if it were the rule. |
| Sweep tau until the 89 look better | Identical reruns already flip the gate on 8 of 89. A small Jev delta is noise unless `score_significance` says otherwise. |

## Order of work

1. Blank amendment stamps.
2. Score the current pool once.
3. Sweep tau with an abstention cap on all 150 questions.
4. Turn on a Laguna retry only for the generation bucket where the kept set still contains the gold provision.
5. Leave the no-evidence questions as abstentions until the Act text is in the corpus.

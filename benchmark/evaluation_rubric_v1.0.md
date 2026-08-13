# Evaluation Rubric — Legal-RAG Golden Benchmark v1.0

Frozen benchmark: **150 questions** · `benchmark_v1.0.jsonl` · SHA-256 recorded in
`README_v1.0.md`. This rubric defines how answers and retrievals are scored.
It measures **retrieval and legal evidence**, not mere plausibility.

## 1. Retrieval metrics (evidence coverage)

| Metric | Definition |
| --- | --- |
| Recall@5 / @10 / @20 | Fraction of `gold_source_chunks` (resolved provision ids) retrieved within top-k |
| MRR | Reciprocal rank of the first gold source |
| nDCG | Discounted cumulative gain over gold + acceptable-alternative sources |

Gold relevance: `primary_provisions` = 2, `acceptable_alternatives` = 1, all else 0.

## 2. Legal evidence scoring (per question, binary)

| Evidence axis | Pass condition |
| --- | --- |
| Correct Act | Retrieved/ cited instrument family matches `gold_instruments` |
| Correct provision | Provision id matches `primary_provisions` or `acceptable_alternatives` |
| Correct authority | Authority name matches `gold_authorities` |
| Correct domain | Domains invoked match `domains` |
| Correct jurisdiction | Jurisdiction matches `jurisdiction` |
| Correct temporal status | Status matches `temporal_constraints` (current/amended/repealed) |
| Correct exception | `exceptions` acknowledged where present |
| Correct cross-domain evidence | Multi-domain questions cite ≥2 distinct collections |

## 3. Generation scoring (2 / 1 / 0)

| Score | Meaning |
| --- | --- |
| 2 | Fully correct: conclusion matches `acceptable_conclusion`, all citations valid, no unsupported claims |
| 1 | Partially correct: right conclusion but incomplete evidence, or a minor citation error |
| 0 | Incorrect: wrong conclusion, or unsupported legal claim |

## 4. Critical-error flag (binary, overrides score)

`critical_error = true` when the answer commits any of:

- wrong law (different Act)
- wrong provision
- wrong authority
- wrong jurisdiction
- wrong penalty amount/type
- wrong temporal status (citing repealed law as current)
- unsupported legal conclusion (no corpus evidence)

A `critical_error` answer is recorded as **0** regardless of partial overlap.

## 5. Safety metrics

| Metric | Rate definition |
| --- | --- |
| Wrong-authority rate | `critical_error` with wrong authority / N |
| Wrong-provision rate | `critical_error` with wrong provision / N |
| Hallucination rate | Claims not traceable to any gold source / N |
| Obsolete-law rate | Repealed/amended law cited as current / temporal questions |
| False-confidence rate | HIGH-confidence answer with a critical error / N |

## 6. Abstention correctness

For questions with `"insufficient_evidence": true`, a correct answer states the
corpus does not establish the proposition. Fabricating a figure = critical error.

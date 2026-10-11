# The KG question: what is measured, what is blocked

Written 2026-10-11. No LLM calls. Two facts arrived in the wrong order and need
separating.

## Correction: the earlier "KG has no path" claim was not a measurement

`CURRENT_STAGE_ASSESSMENT.md` concludes that a knowledge graph has no
demonstrated path to binary/soft correctness, on the evidence that retrieval
headroom is +0.004 and corpus word presence is non-discriminative.

**Both numbers came from arm `C_hybrid`, which does not use the KG.**

`evaluation/arms.py` defines the arms: C is dense+sparse hybrid, and only D, E
and F touch the graph. The KG is a *generation-time* stage —
`_generate_apply_kg_context` RRF-fuses expanded provisions into the LLM context
and does not re-rank the retrieval list at all. So the ranked list is
byte-identical with the KG on and off, and nothing measured on arm C can see it.

The claim "the KG adds nothing" was therefore never tested. It should be
restated as: *arm C without the KG shows no headroom, which says nothing about
what the KG adds at generation time.*

## The one existing KG measurement cannot detect a contribution

`evaluation/out/kg_contribution/report.md` reports `kg_injected_gold: 0` —
"retrieval already reached every gold clause on this benchmark".

It measures **5 questions**, all food-intent items (FI001–FI005), and
`retrieval_recall` was **1.0 on all five**. When retrieval already contains the
gold clause, a KG that *adds* clauses cannot be observed contributing one. The
zero is structural, not empirical.

The population where the KG's mechanism has room to act is the 89 model_wrong
failures, where gold-in-top10 is 45/89 (51%). That experiment has never been run.

## The blocker

`NEO4J_URI=neo4j+s://21090cf9.databases.neo4j.io`

* credentials are present in `.env` and well-formed;
* `neo4j.com` resolves from this environment, so DNS works in general;
* the instance host does **not** resolve — `getaddrinfo failed`, same as Qdrant's
  MITM-proxied situation;
* no local Neo4j is running (7687/7474/7473 all closed).

An unreachable graph and a reachable-but-empty graph both produce "the KG adds
nothing", and only the second is a statement about the KG. The harness refuses to
report either as a finding.

## What is built

`evaluation/kg_effect_on_failures.py` measures, per question and offline:

| field | meaning |
|---|---|
| `ctx_cov` | distinctive coverage of the reference by retrieved context |
| `kg_cov` | the same with KG-injected provision text added |
| `delta` | what the KG contributes to the context |

It drives the production `KGContextExpander` unchanged, checks reachability
*before* measuring, and exits with a diagnosis rather than a null result when the
graph is absent. It is ready to run; it needs a reachable Neo4j.

`delta` is not soft/binary correctness — that needs an LLM answer, and the LLM
budget is zero. It is the input to it: if the KG does not raise the context's
distinctive coverage, no model can convert it into correctness.

## Next steps, in order

1. **Make the graph reachable.** A live Aura instance, or a local Neo4j built
   from `scripts/build_kg_corpus.py`. Until then nothing about the KG can be
   measured or optimised, and any KG work would be code review, not measurement.
2. Run `python -m evaluation.kg_effect_on_failures` on the failure cohort.
3. Only if `delta` is positive does graph *structure* become worth optimising.
   If it is not, the defect is in what the graph is pointed at, and adding
   relationships to it would be tuning a channel that carries nothing.

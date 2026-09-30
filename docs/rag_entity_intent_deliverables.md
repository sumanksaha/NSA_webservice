# Deliverables — Entity–Provision-Aware Food Retrieval Upgrade

Date: 2026-09-29. Final report for the ENTITY ≠ INFORMATION REQUEST upgrade
("What is cumin?" → definition · "What is the standard for cumin?" → food
standard · "What is the moisture limit for cumin?" → parameter-specific
standard). Companion docs:
`docs/rag_entity_intent_architecture_assessment.md` (pre-modification
baseline), `docs/rag_entity_intent_failure_analysis.md` (§23 failure
analysis), `docs/adr/0009-tiered-provision-extraction-engine.md`.

## 1. Final ablation table (benchmark v1.1, n=26, live Qdrant, 2026-09-29)

| Arm | ent@1 | ent@5 | ent@10 | std@1 | std@5 | std@10 | par@1 | par@5 | src@10 | disambig |
|---|---|---|---|---|---|---|---|---|---|---|
| S0_baseline | 0.6923 | 0.8846 | 0.9231 | 0.4231 | 0.8077 | 0.8462 | 0.5385 | 0.7692 | 1.00 | 0.4231 |
| S1_plus_intent | 0.8462 | 0.9615 | 0.9615 | 0.6923 | 0.9231 | 0.9615 | 1.00 | 1.00 | 1.00 | 0.6923 |
| S3_plus_legal_rerank | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** |
| S4_full | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** |

Provision recall@1 mirrors entity recall (same gold set); parameter recall
is normalised over the 13 parameter-eligible questions. Full JSON:
`evaluation/out/food_intent/{arm}_metrics.json`, machine table in
`evaluation/out/food_intent/ablation_table.json`.

Headline deltas (S0 → S4):

- Entity Recall@1 **0.69 → 1.00** — the entity's own provision always ranks first.
- Standard Recall@1 **0.42 → 1.00** — definition anchoring eliminated.
- Parameter Recall@1 **0.54 → 1.00** (eligible subset) — parameter rows surface immediately.
- Disambiguation accuracy **0.42 → 1.00** — a definition never outranks the rows for a standard ask, and a definition-only clause's heading is never demoted.
- Source Recall@10 **1.00 → 1.00** — already saturated; preserved.

## 2. Reranker-iteration narrative (what each change bought)

Measured on the two-arm rerun after each iteration (S3/S4 identical on this
benchmark throughout):

| Iteration | Change | Effect |
|---|---|---|
| baseline S0 | — | ent@1 .50* → final re-measure .69; disambig .35* → .42 (*initial v1.1 measurement used provisional gold sets) |
| 2 | `_param_match` weight (w_param .20) + clause-lead tie-break | separated a clause's own parameter row from sibling rows; killed last-fragment dense-tie wins |
| 3 | definition intent: lead 1.0 / others 0.3; anchor-aware heading demotion | "What is cumin?" returns the definition head; "standard for cumin" stops returning it |
| 4 | phrase-aware entity match; strict pool identity cap 0.45; anchor fetch (stage 2b) | ent@10 1.00 reached; std@5 .96; disambig .85 — but gazette blobs still leaked |
| 5 | exact clause-sibling fetch (scroll-all + local filter; server payload filter impossible in strict mode); definition-lead boost | std@5 1.00; disambig .88 |
| 5b | commodity-compatibility gate | disambig .92 — OCR blobs' incidental mentions no longer count as identity |
| 6 | definition-form cap keyed on clause-lead *position* (not derived role) | FI012 ("What is cardamom?") fixed — sub-definition no longer outranks the clause lead; disambig .96, ent@1 .96 |
| 7 | left-branching compound rule (`_COMPOUND_HEADS`) in `commodity_phrase_match` | FI015 fixed — "Pan Masala 8000 ppm" no longer matches "masala"; **disambig 1.00, ent@1 1.00, std@5 1.00** |
| 8 | commodity vocabulary harvested from corpus headings (146 names) wired into `_KNOWN_COMMODITIES`/`_COMMODITY_VOCAB`; `commodity_agrees` bidirectional gate; `_entity_variants` restricted to known commodity tokens; preceding-word compound fusion in heading extraction | vocabulary survives ablation unchanged — S3/S4 stay **all 1.00, 0 failures** (the harvest initially regressed disambig to 0.96 via FI015; see M8 in the failure analysis) |

Two irreducible-at-first failures (FI012, FI015) both fell to position-/preceding-word-keyed
rules rather than more weights — the mechanism, not the score, was wrong.
Iteration 8's harvest regression was also a mechanism bug (a generic variant
token + a compound head lost in the clause map), not a weighting one.

## 2b. Commodity vocabulary harvest

`evaluation/harvest_commodity_vocabulary.py` scans every clause-lead heading
in the payload index (`evaluation/out/cache/payload_index.jsonl`, 27,351
points) through the same `_CLAUSE_LEAD_RE` gate the reranker uses, filters
administrative headings, and emits **`app/rag/retrieval/commodity_vocabulary.json`**:

- **146 commodities** across 12 clause families (2.1 fats, 2.3 beverages,
  2.4 bakery, 2.9 spices, 2.10/2.11 dairy/pan masala, …) — 60 hand-curated
  words grow to **179** in `_KNOWN_COMMODITIES` and **187** in the query-side
  `_COMMODITY_VOCAB` (longest-first extraction unchanged).
- Joint headings split on "A or B"/"A and B"; duplicate-word tails collapse
  ("oils and oils" → "oils"); a >3-word name is kept only when the heading
  contains "means" (definition-form guard).
- **`candidate_synonyms` (41) are deliberately NOT auto-wired**: pairing
  words like "edible" with "catechu" from a shared heading would hijack
  unrelated queries. They are exported for human curation only.
- Missing/ malformed JSON degrades to the hand-curated set
  (`_load_harvested_commodities()` → `()`); the loader is duplicated in
  `provision_metadata.py` (chunk side) and `food_query_understanding.py`
  (query side) with no import cycle.

New gate **`commodity_agrees(derived, entity)`** — bidirectional containment
agreement guarded by the compound rules — replaces the single-direction
`commodity_phrase_match` comparison in the reranker's `_entity_match`, the
clause-map inheritance branch, and validation's entity-evidence checks:
`("mixed masala", "masala")` agree (family/clause pair) while
`("pan masala", "masala")`, `("cumin", "cumin black")`, and
`("ginger", "ginger cocktail")` disagree (distinct commodities).

## 3. What shipped

| Component | File | Flag |
|---|---|---|
| Food query understanding (entity/intent/parameters, deterministic) | `app/rag/retrieval/food_query_understanding.py` | `RAG_FOOD_INTENT_ENABLED` |
| Query-time provision metadata (role, commodity, compound-aware) | `app/rag/retrieval/provision_metadata.py` | always-on pure function |
| Legal-aware two-stage reranker (9 features + caps + tie-breaks) | `app/rag/retrieval/legal_reranker.py` | `RAG_FOOD_LEGAL_RERANK` |
| Parent/clause reconstruction + clause→commodity map | `app/rag/retrieval/parent_reconstruction.py` | `RAG_FOOD_PARENT_RECONSTRUCT` |
| Post-retrieval validation + fallback rounds | `app/rag/retrieval/validation.py`, stage 3 in `tasks.py` | `RAG_FOOD_VALIDATE` / `RAG_FOOD_FALLBACK_ROUNDS` |
| Dynamic identity-anchor fetch + exact clause-sibling fetch (stage 2b) | `app/rag/tasks.py` | with rerank/parent flags |
| Intent-conditioned food answer prompts + completeness check | `app/rag/generation/food_answer.py` | `RAG_FOOD_ANSWER_MODE` |
| Commodity vocabulary harvester (clause-heading scan, deterministic) | `evaluation/harvest_commodity_vocabulary.py` | offline tool — run on demand |
| Harvested vocabulary (146 commodities + 41 candidate synonyms, audit stats) | `app/rag/retrieval/commodity_vocabulary.json` | consumed by metadata + query understanding |
| Benchmark v1.1 (26 q, categories A–F, gold verified) | `benchmark/benchmark_food_intent_v1.1.jsonl` | frozen |
| Metrics (§18) + ablation runner (§19) | `evaluation/food_intent_metrics.py`, `evaluation/run_food_intent_ablation.py` | — |
| Acceptance tests T1–T6 | `evaluation/run_food_acceptance_tests.py` | — |

## 4. Acceptance tests (live, shipped configuration)

```
PASS  T1  definition ask -> definition head top-1            [e5a7795a]
PASS  T2  standard ask -> cumin standard row top-1           [0912f167]
PASS  T3  parameter ask -> cumin limit row top-1             [eff59d63]
PASS  T4  benchmark disambiguation = 1.00
PASS  T5  sampling ask -> sampling provision in top-5
PASS  T6  compliance ask -> cumin clause evidence in top-5
```

Reproduce: `python -m evaluation.run_food_acceptance_tests` (~6 min, live
Qdrant, CE model cached).

## 5. Verification & reproduction

- Fast suite: `export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1; python -m pytest tests/test_food_intent_retrieval.py tests/test_rag_tasks.py -q -p no:cacheprovider` → **77 passed** (68 food-intent + 9 rag-tasks); re-verified green after each wiring step of the harvest phase.
- Vocabulary spot-checks: entity extraction ("vodka"→vodka, "pickles"→pickles, "ginger cocktail"→full compound, spice queries unchanged), `commodity_phrase_match` 7-case probe, `commodity_agrees` 15-case probe — all pass.
- Harvest reproduction: `python -m evaluation.harvest_commodity_vocabulary` (deterministic; `--dry-run` prints commodities/synonyms/families/skipped without writing).
- Full ablation: `python -m evaluation.run_food_intent_ablation --arms S0_baseline,S1_plus_intent,S3_plus_legal_rerank,S4_full --force` (~6 min for rerank arms; S0/S1 cheap).
- Results cache: `evaluation/out/food_intent/*.jsonl` (append-only per arm; resume without `--force`).
- Payload index cache: `evaluation/out/cache/payload_index.jsonl` (27,351 points, all collections).

## 6. Known limitations & future work

1. **Answer-level (LLM) quality unmeasured** — retrieval-side metrics are
   exhaustive; answer faithfulness/completeness need live LLM evaluation
   (stub LLM used in all arms).
2. **KG arms verified-empty only** — no local Neo4j; graceful degradation
   confirmed, recall contribution not measurable.
3. **Clause-sibling fetch is O(pool)** — strict-mode Qdrant lacks a payload
   index on `clause_number`; a one-shot index build (or payload backfill of
   derived metadata) would make it O(log n).
4. **Benchmark size** — 26 hand-verified questions is the right size for
   iteration but small for regression confidence; growing categories C/F
   (parameter asks, traps) is the highest-value extension.
5. **Corpus scope** — rules are tuned on FSSAI spice clauses (2.9.x);
   other acts/regulations inherit the machinery but not the tuning.

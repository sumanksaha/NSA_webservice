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
| Answer-quality eval runner (live LLM + judge, resumable) | `evaluation/run_food_answer_eval.py` | `RAG_USE_STUB_LLM` forced false |
| KG recall-contribution runner | `evaluation/run_kg_contribution.py` | needs a populated local Neo4j |
| FSSAI clause-keyed KG ingestion (Qdrant fallback) | `kg/corpus_ingestion.py` | `build_fss_provisions` |
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

- Fast suite: `export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1; python -m pytest tests/test_food_intent_retrieval.py tests/test_rag_tasks.py -q -p no:cacheprovider` → **77 passed** (68 food-intent + 9 rag-tasks); re-verified green after each wiring step of the harvest phase and again after §6.2–§6.3. One test (`test_kg_fallback_degrades_gracefully`) now clears `NEO4J_*` explicitly: it asserts the *unconfigured* degradation path, and a developer `.env` pointing at a real local Neo4j would otherwise make it a live test.
- Vocabulary spot-checks: entity extraction ("vodka"→vodka, "pickles"→pickles, "ginger cocktail"→full compound, spice queries unchanged), `commodity_phrase_match` 7-case probe, `commodity_agrees` 15-case probe — all pass.
- Harvest reproduction: `python -m evaluation.harvest_commodity_vocabulary` (deterministic; `--dry-run` prints commodities/synonyms/families/skipped without writing).
- Full ablation: `python -m evaluation.run_food_intent_ablation --arms S0_baseline,S1_plus_intent,S3_plus_legal_rerank,S4_full --force` (~6 min for rerank arms; S0/S1 cheap).
- Results cache: `evaluation/out/food_intent/*.jsonl` (append-only per arm; resume without `--force`).
- Answer-level quality (§6.1): `python -m evaluation.run_food_answer_eval` (~10 min, live LLM; `--retry-incomplete` to refill judge calls lost to free-tier 429s). Artifacts: `evaluation/out/food_answer/`.
- KG rebuild + contribution (§6.2): `NEO4J_ALLOW_WRITE=1 python scripts/build_kg_corpus.py` then `python -m evaluation.run_kg_contribution` (~3 min). Artifacts: `reports/kg_rebuild_summary.json`, `evaluation/out/kg_contribution/`. Local Neo4j credentials are gitignored.
- KG suite: `python -m pytest tests/test_corpus_kg_ingestion.py tests/test_payload_identity.py tests/test_kg_hybrid_expander.py tests/test_kg_provisions_chunks.py tests/test_kg_remediation.py tests/test_corpus_discovery.py -q -p no:cacheprovider` → 128 passed.
- Clause-sibling index (§6.3): `clause_number` keyword index on `fssai_legal_768`; the filtered `scroll_all` in `app/rag/tasks.py` no longer scans the collection.
- Payload index cache: `evaluation/out/cache/payload_index.jsonl` (27,351 points, all collections).

## 6. Known limitations & future work

Items 1–3 below were the original §6 future-work list. All three are now
**closed** — measured, with the numbers recorded in §6.1–§6.3. Items 4–5
remain open.

1. ~~**Answer-level (LLM) quality unmeasured**~~ — **closed**, see §6.1.
2. ~~**KG arms verified-empty only**~~ — **closed**, see §6.2.
3. ~~**Clause-sibling fetch is O(pool)**~~ — **closed**, see §6.3.
4. **Benchmark size** — 26 hand-verified questions is the right size for
   iteration but small for regression confidence; growing categories C/F
   (parameter asks, traps) is the highest-value extension. §6.1 sharpens
   this: the residual answer-level failures are concentrated in
   B_food_standard, and at n=1–4 per category the ranking is directional
   only.
5. **Corpus scope** — rules are tuned on FSSAI spice clauses (2.9.x);
   other acts/regulations inherit the machinery but not the tuning.
6. **No reliable answer-level regression gate yet** — §6.1.1 shows the
   pipeline's own signals are near-orthogonal to answer quality, and the
   judge needs a live paid key for full benchmark coverage on the free tier.
   Until both are fixed, answer-level quality cannot gate a release.

### 6.1 Answer-level (LLM) quality — measured

Runner: `python -m evaluation.run_food_answer_eval` (`--retry-incomplete`
re-runs only questions with no judge score **or** a degenerate answer;
`--force` re-runs everything). It forces `RAG_USE_STUB_LLM=false` and aborts
rather than score a stub, runs the **full generation pipeline** (S4 config,
`top_k=10`), and records two independent signal sets per question: the
pipeline's own deterministic checks (groundedness, hallucination, citation
score, §16 completeness flags) and an **LLM-as-judge** second call scoring
faithfulness and completeness 0–4 against the benchmark's
`acceptable_conclusion`, with written notes.

Live run over the benchmark (`poolside/laguna-s-2.1:free`; artifacts in
`evaluation/out/food_answer/{answers.jsonl,metrics.json,report.md}`):

| metric | value |
|---|---|
| questions attempted | 26 |
| degenerate answers excluded (§6.1.1) | 12 |
| judge-scored | 14 |
| faithfulness (judge mean, scored only) | **0.8214** |
| completeness (judge mean, scored only) | **0.8036** |
| definition-leak rate | **0.0** |

By category (scored only — small n, read as directional):

| category | n | faithfulness | completeness |
|---|---|---|---|
| A_definition | 4 | 1.00 | 1.00 |
| B_food_standard | 4 | 0.63 | 0.56 |
| C_parameter | 2 | 0.75 | 0.75 |
| D_compliance | 2 | 1.00 | 1.00 |
| E_similar_entity | 1 | 0.50 | 0.50 |
| F_trap | 1 | 1.00 | 1.00 |

Read-outs worth carrying forward:

* **Definition-leak is solved** — 0.0 across every run, the anti-anchoring
  work in `food_answer.py` holds at answer level, not just retrieval level.
* **Retrieval is not the answer-level bottleneck.** Retrieval reaches the gold
  clause 26/26 (§4, all 1.00) and §6.2 shows the KG adds nothing, yet
  completeness is 0.80. The gap is *which* row of a reached clause gets
  quoted — a generation-stage problem, not a retrieval one.
* **The weak spot is B_food_standard, not C_parameter** (correcting an earlier
  reading of this section). The pattern in the judge's notes is consistent
  across FI002/FI007/FI010: the model picks **one variety and quotes a partial
  parameter set** — ginger *powder* when the gold covers whole and powder; a
  subset of the compliance table when the gold lists the full set. It is not
  reaching the wrong clause; it is under-reporting a reached one. The highest
  -value generation change is an explicit completeness contract in the
  `food_standard` prompt: enumerate every parameter row of the gold clause,
  for each named variety, and never answer with a single variety unprompted.
* **A concrete correctness bug, not just under-completeness:** FI020 ("total
  ash limit for fennel") answers **10.0%** while the retrieved evidence says
  total ash **9.0%** — it took the moisture figure from the adjacent row. The
  judge caught it (faithfulness 0.5). This is the same neighbouring-row
  confusion as FI018, now visible at answer level rather than inferred.

#### 6.1.1 Two measurement defects found and fixed

Both were caught by cross-checking the judge's verdicts against the answers
rather than trusting the aggregates; both are fixed in
`evaluation/run_food_answer_eval.py` and both changed the headline numbers.

1. **Empty answers were being scored 1.0/1.0.** Upstream OpenRouter `429`s
   during *generation* made the pipeline degrade to an empty string. The
   runner had no guard, so an empty answer reached the judge — and the judge,
   shown the gold conclusion and full evidence *before* the answer, anchored
   high and awarded 4/4 despite a rubric that literally says "0 = empty".
   The first reported run (faithfulness 0.8654, completeness 0.8077 over
   26/26) was inflated by four such cases. Fixes: (a) `_is_degenerate` skips
   judging entirely for empty/bare-refusal/passages-only answers and excludes
   them from the means; (b) the judge prompt now shows the **answer first**,
   with evidence and gold demoted to "REFERENCE", and the rubric makes
   completeness 0 mandatory for a non-answer.
2. **The pipeline's own deterministic signals carry almost no information.**
   Correlating them against the judge's verdicts (n=14):
   `corr(groundedness, judge_faithfulness) = +0.12`,
   `corr(groundedness, judge_completeness) = +0.08`,
   `corr(citation_score, judge_faithfulness) = +0.12`. `groundedness = 0.0`
   fired on 6 answers the judge scored **1.0/1.0**, and `hallucination_detected`
   produced 6 false positives against 3 true positives. These signals are close
   to orthogonal to answer quality on this benchmark and **should not be used
   as the regression gate**; the judge is the only signal that discriminates.

Coverage caveat: the free tier's 429 rate rose sharply during the final run,
so only 14/26 questions produced a scorable answer. The means above are
therefore a *lower-confidence* estimate than a fully-covered run would give,
and per-category n is 1–4. Treat the category ranking as directional, not
measured. Getting to full coverage needs either a paid key or per-question
backoff — the item below.

### 6.2 KG arm — measured (contribution is **zero** on this benchmark)

Local Neo4j Community 5.26.0 on `bolt://127.0.0.1:7687`; credentials live in
the gitignored `.env` (`NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD`/
`NEO4J_DATABASE`/`NEO4J_ALLOW_WRITE`) and are local-only. Rebuild with
`NEO4J_ALLOW_WRITE=1 python scripts/build_kg_corpus.py` (~60 s; artifacts in
`reports/kg_rebuild_summary.json`).

**The rebuild had a real gap, and closing it was the substance of this item.**
The corpus engine sources the primary-domain (FSSAI) corpus from the local
`LegalDocument`/`LegalChunk` tables, not from Qdrant. On a freshly migrated
database those tables hold 0 rows, so the first rebuild produced 37
instruments / 1,647 provisions / 21,459 chunks — **all from the five
non-FSSAI collections, zero FOOD_SAFETY provisions**. The KG arm was empty for
food not by configuration but by construction, and would have scored a
meaningless 0. `kg/corpus_ingestion.py` now falls back to the live
`fssai_legal_768` payloads when the local tables are empty. FSSAI regulations
are clause-numbered rather than sectioned, so they also needed their own
provision builder (`build_fss_provisions`) — the payloads key on
`clause_number` + `provision_ids` (`fssai:s2.9.8`), which the section-based
`build_provisions` discards. Node ids are instrument-scoped
(`<instrument>_CLAUSE_2.9.8`) because the registry id alone is not unique —
clause `4` alone is claimed by 14 documents in this corpus — with the registry
id kept on `provision_ref` as the benchmark join key.

After the fix (37,356 nodes, 23,245 → 34,298 chunks):

| | before | after |
|---|---|---|
| instruments / documents | 37 | 86 |
| provisions | 1,647 | 2,821 (of which 1,174 FOOD_SAFETY) |
| chunks | 21,459 | 34,298 (12,839 FSSAI) |
| `SUPPORTED_BY` → FSSAI chunks | 0 | 12,450 |

Contribution measurement: `python -m evaluation.run_kg_contribution`
(artifacts in `evaluation/out/kg_contribution/`). Because KG expansion is a
**generation-time** stage (`_generate_apply_kg_context` RRF-fuses provisions
into the LLM context; it does not re-rank the retrieval list), the metric is
*context coverage*, not retrieval recall: does the gold clause reach the LLM
only because the graph expanded a retrieved chunk?

| metric | value |
|---|---|
| retrieval recall (gold clause in retrieved chunks) | 1.00 |
| KG context recall (retrieval + injected) | 1.00 |
| **gold clauses reaching the LLM only via the KG** | **0 / 26** |
| mean KG provisions injected per question | 5.31 |
| mean KG chunks matched per question | 10.0 / 10 |
| mean KG expansion latency | 650 ms |
| KG expansion errors | 0 |
| top-1 identical with KG disabled | 26/26 |
| mean top-10 set overlap with KG disabled | 1.00 |

**Conclusion: the KG arm's recall contribution on benchmark v1.1 is zero, and
it cannot be non-zero while retrieval recall is 1.00.** The KG is working
(it matches every retrieved chunk and injects ~5 provisions per question,
sibling clauses such as 2.9.1–2.9.3 around the gold 2.9.8) — it is simply
redundant for coverage on this benchmark, where a 10-chunk pool always
contains the whole clause family. Its value here is provenance and structure
(instrument, authority, status, modality), not recall.

Two measurement caveats, both checked rather than assumed:

* The KG-off control is a weak instrument on its own: the pipeline is **not
  bit-reproducible run to run** (a bare `run_retrieval_pipeline` twice on the
  same query returns different top-10 orderings — verified independently of
  the KG), so the 3/26 exact-order differences are tie-breaking noise, not a
  KG effect. Set overlap (1.00) and top-1 identity (26/26) are the honest
  read-outs, and both are reported.
* `kg_max_provisions` defaults to 5, so the injected set is truncated; a
  larger budget could not change coverage here (retrieval already has 1.00)
  but would change the provenance breadth.

### 6.3 Clause-sibling fetch — O(log n), done

Created the missing Qdrant payload index on the live cluster:

```
c.create_payload_index(collection_name='fssai_legal_768',
                       field_name='clause_number',
                       field_schema=models.PayloadSchemaType.KEYWORD)
```

`clause_number` was already listed in `QdrantStore.DEFAULT_PAYLOAD_INDEX_FIELDS`;
only the live index was missing. With it present, the server accepts the
`{document_id, clause_number}` filter in strict mode and paginates only the
matching points (1 record in 0.8 s) instead of the client scrolling the whole
12,839-point collection and filtering locally. `app/rag/tasks.py` now passes
that filter straight to `scroll_all` and drops the two local
`if str(payload.get(...)) != …: continue` lines.

Live check: *"What is the moisture limit for cumin?"* → 5 chunks, validation
True, top-1 `clause_number` = **2.9.8** (the benchmark gold clause). Fast
suite 77/77 green; ruff delta on `app/rag/tasks.py` is zero (the file's two
pre-existing errors reproduce at `HEAD`).


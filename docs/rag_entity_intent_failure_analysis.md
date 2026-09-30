# §23 Failure Analysis — Entity–Provision-Aware Food Retrieval

Date: 2026-09-29. Scope: every failing case observed on benchmark v1.1
(n=26) during the ablation programme, organised by failure mechanism, with
the fix applied and the verification status. Companion to
`docs/rag_entity_intent_architecture_assessment.md` (pre-modification
snapshots) and `docs/rag_entity_intent_deliverables.md` (final metrics).

## 1. Case accounting

A "case" is a failing metric cell (`{entity,standard,provision,parameter,source}@{1,3,5,10,20}`
or the disambiguation top-1 judgement) on the S0 baseline run, restricted to
metrics that are meaningful for the question (vacuous parameter cells on
questions that name no parameter are excluded):

- **110 failing cases across 22 of 26 questions** (baseline S0).
- 15 distinct questions appear in the failure examples list
  (`S0_baseline_metrics.json → failures`), spanning 22 failure-mode
  incidences: 10 `wrong_top1`, 5 `trap_rank1`, 5 `standard_miss@5`,
  2 `entity_miss@10`.
- After the full upgrade (S4_full), **0 failing cases remain** on every
  meaningful metric cell and the disambiguation judgement.

## 2. Failure mechanisms and cases

### M1 — Definition anchoring (the core ENTITY ≠ INFORMATION failure)

**Mechanism.** A definition-form clause heading ("2.9.8: Cumin … whole means
the dried mature fruits…") shares the entity vocabulary with standard asks,
so pure text similarity ranks it top-1 for "What is the standard for
cumin?". The user asked for a limit; the answer returned a description.

**Cases (baseline).** FI002, FI003, FI007, FI021, FI026 — 5 × `trap_rank1`
(the definition head of the correct clause occupying rank 1 for a standard
ask), plus FI009/FI011 (definition heads in the pool outranking rows).

**Fix.** Legal-aware reranker (S3): definition-ask intent matching +
anchor-aware heading demotion (heading → 0.9 when a standard-anchored
entity-matched sibling exists), parameter weight (w_param 0.20), clause-lead
tie-break. Verified: all five questions now return a clause 2.9.8-family
limit row at top-1 (`entity@1 1.00`, `standard@1 1.00` on S3/S4).

### M2 — Orphan table rows (commodity identity invisible in-chunk)

**Mechanism.** Provision tables chunk into ~200-char row fragments
("(v) Ash insoluble in dilute HCl … Not more than 1.5 percent") that never
name their commodity. For the row to be *recognised* as answering a
"moisture limit for cumin" ask, identity must come from the clause sibling.

**Cases (baseline).** FI003 (cumin moisture — rows present but definition
head ranked above them), FI009 (coriander moisture — row top-1 was the
clause heading `32359d21` instead of the row), FI011 (turmeric extraneous
matter — wrong clause entirely at top-1), FI014 (fennel moisture → generic
15 %-moisture row of another regulation `0e5368ce`), FI018 (ginger total ash
— the bare numeral chunk `1bb269dc` won; the gold clause never surfaced),
FI025 (ginger moisture → "Ginger Cocktail" clause `2.3.22`).

**Fix.** Two-stage: (a) clause→commodity map published by
`group_by_clause` from the pool's heading chunks, inherited by row chunks in
the entity feature (0.9); (b) stage-2b *exact clause-sibling fetch*
(scroll-all + local `document_id`/`clause_number` filter — the live Qdrant
index has no payload index on `clause_number`, so server-side filtering
fails) when the heading is known but <3 clause chunks are in the pool.
Verified: all six questions now put the correct clause's operative row at
top-1 on S3/S4.

### M3 — Ingredient-list / gazette OCR blobs (verbatim mention ≠ identity)

**Mechanism.** OCR chunks of gazette pages and ingredient lists name 6–10
commodities incidentally ("Zingiber officinale - rhizome standardized",
label declarations, sweetener limit tables). A verbatim entity mention there
is *not* identity, but text similarity rewarded it.

**Cases (baseline).** FI010 (ginger standard → numeral-index gazette chunk
`6baf290e` listing "186. Zingiber officinale"), FI016 ("Define ginger" →
gazette fragment `1bb269dc`), FI015 (mixed masala standards → clause 3.1.3
gazette blob `1619d7a9` whose only "masala" is "Pan Masala 8000 ppm" in an
artificial-sweetener table).

**Fix.** Two gates in the entity feature: (a) *commodity-compatibility* —
a text mention counts only when the chunk's derived commodity agrees (or is
unknown); (b) *left-branching compound rule* (iteration 7) —
`commodity_phrase_match` now rejects an occurrence whose immediately
preceding word is a compound head (`pan`, `garam`, `chai`, `meat`, …), so
"Pan Masala" no longer matches "masala", mirroring the existing next-word
rule for "masala milk"/"Ginger Cocktail". Verified: FI010/FI016/FI015 all
correct at top-1 on S3/S4 (final disambiguation 1.00).

### M4 — Compound-product confusion (left- and right-branching)

**Mechanism.** "Ginger Cocktail", "Masala Bread", "Cumin Black", "Pan
Masala" are distinct products with their own clauses; the bare commodity
query must not match them.

**Cases (baseline).** FI025 (moisture in ginger → Ginger Cocktail clause),
FI010 (compound listing), FI015 (Pan Masala).

**Fix.** `_PRODUCT_WORDS` next-word rule (iteration 4) + `_COMPOUND_HEADS`
preceding-word rule (iteration 7) in `commodity_phrase_match`; compound
fusion in `_extract_commodity_from_heading` only when the next word is a
product/modifier/known commodity. Verified by 11-case unit probe (all pass)
+ benchmark.

### M5 — Cross-regulation parameter rows (right value, wrong law)

**Mechanism.** Generic limit rows from other regulations ("(a) more than 15
per cent moisture" in clause 2.11.3; "(1) Moisture Not more than 16.0
percent" in 2.6.1) carry the parameter + measurement vocabulary but not the
entity, and outranked the true clause.

**Cases (baseline).** FI014 (`0e5368ce`), FI013/FI019 (compliance asks →
`0caabf71` clause 2.6.1 moisture row), FI020 (fennel total ash → clause
2.9.29 ash row `952b0abd`).

**Fix.** Strict pool identity cap (iteration 4): when the pool contains an
identity anchor, chunks with no entity evidence are capped at 0.45;
unknown-identity rows stay uncapped when no anchor exists (no information =
no cap). Verified: FI013/FI019/FI014/FI020 all return the correct clause's
evidence in the top-5 (acceptance T6 PASS).

### M6 — Definition-only clauses need their heading (the inverse trap)

**Mechanism.** Clause 2.9.20 MIXED MASALA is definition-only: the heading is
the entity's *only* provision. Demoting definition-form headings because
they "look like the trap" would break the definition ask for such entities.

**Cases.** FI015's gold is the heading itself (`cc28f1aa`) even though the
intent is `food_standard`; a naive anti-definition rule would demote it.

**Fix.** Heading demotion is *conditional* — a heading scores 0.0 only when
a standard-anchored entity-matched sibling exists in the pool; otherwise it
keeps 0.9 (and the definition-ask lead cap of iteration 6 keys on
clause-lead *position* among entity-matchers, not on derived role, because
clause headings routinely carry a requirement tail that stamps them
role="standard"). Verified: FI015 top-1 = `cc28f1aa` on S3/S4.

### M7 — Metrics-layer accounting bug (found during final analysis)

**Mechanism.** `parameter recall` was normalised over all 26 questions
although only 13 name a parameter; the other 13 contribute vacuous False,
so a *perfect* eligible-subset score reported as 0.5.

**Cases.** All post-S1 arms reported par@K = 0.5 despite all 13 eligible
questions being answered within top-1.

**Fix.** Eligible-only denominator in `evaluate_food_intent`
(`evaluation/food_intent_metrics.py`). Corrected final: S0 par@1 0.54 → S1+
par@1 1.00 (recomputed without re-retrieval; pools unaffected).

### M8 — Harvest regression: generic variant token + compound head lost in the clause map (iteration 8)

**Mechanism.** Wiring the harvested vocabulary (146 names) initially
regressed disambiguation 1.00 → 0.96 on FI015 ("What are the standards for
mixed masala?"), whose top-1 became the clause 2.2.2 sesame-oil row
`be203627` with the gold heading `cc28f1aa` at #2. Two compounding bugs,
both created — not healed — by richer vocabulary:

1. `_entity_variants("mixed masala")` emitted the single words of the
   compound as standalone identity tokens. "mixed" is not a commodity word,
   so it matched the sesame row's incidental prose "when it is **mixed**
   with refined groundnut oil" → a false `entity = 1.0` *identity anchor*.
2. That bogus anchor made `_intent_match` believe a real standard-shaped
   sibling existed, demoting the definition-form gold heading's intent to
   0.0 (M6's conditional demotion firing on a false premise). Meanwhile the
   Pan Masala definition clause (2.11.5) had registered its clause-map
   commodity as bare "masala" — the preceding compound head "Pan" was
   dropped by heading extraction — so `commodity_agrees("masala", "mixed
   masala") = True` granted it an inherited 0.9 entity match as well.

**Fix.** (a) `_entity_variants` keeps single-word variants only when they
are known commodity words ("masala" of "mixed masala" survives; "mixed"
does not), so prose verbs/adjectives can never fabricate anchors;
(b) `_extract_commodity_from_heading` now fuses a *preceding* compound head
("2.11.5 Pan Masala …" → `pan masala`), so the clause map registers the
distinct product and `commodity_agrees("pan masala", "mixed masala") =
False` blocks the inheritance.

**Lesson.** Enlarging the vocabulary enlarged both the query side (new
compound entities) and the chunk side (new derived names); every derived
word that feeds an identity decision must be a known commodity word, and
compound qualifiers must survive into the clause map.

**Verified.** Re-run S3+S4 (`--force`) after the fix: all metrics 1.00,
**0 failures on every arm**; full 4-arm table rebuilt; T1–T6 PASS; 77-test
suite green.

## 3. Residual / accepted limitations

1. **Real-LLM answer quality is out of scope** of this benchmark — the
   ablation runs with the LLM stubbed; answer-level metrics need live LLM
   spend and were not measured.
2. **KG arms verified-empty only** — no Neo4j instance locally
   (`NEO4J_URI` unset); the KG fusion arm's behaviour is verified to
   degrade gracefully, not to add recall.
3. **Definition-form cap keys on position, not role** — correct for this
   corpus, but a future clause whose *lead* chunk is not a definition while
   later sub-definitions are would need re-validation.
4. **Scroll-all sibling fetch is O(pool)** — the strict-mode Qdrant index
   (no payload index on `clause_number`) forces local filtering over all
   27,351 points; acceptable per-call, worth a payload index if the
   corpus grows.
5. **S3 ≡ S4 on this benchmark** — validation/fallback never fires
   differently from rerank alone post-fix; their distinct value is
   exercised only on harder out-of-benchmark queries.

## 4. Verification status

| Check | Result |
|---|---|
| Fast suite (`test_food_intent_retrieval.py` + `test_rag_tasks.py`) | 77 passed |
| Ablation S0 → S1 → S3 → S4 (26 q, live Qdrant) | ent@1 .69/.85/1.00/1.00 · disambig .42/.69/1.00/1.00 |
| Acceptance tests T1–T6 (`evaluation/run_food_acceptance_tests.py`) | 6/6 PASS |
| Compound-rule probe (11 regression cases) | 11/11 pass |
| `commodity_agrees` probe (15 cases) + entity-extraction spot-checks (harvest phase) | pass |
| Harvest regression re-run (iteration 8, S3+S4 `--force`) | all metrics 1.00, 0 failures |
| Baseline failure cases remaining after S4 | 0 of 110 |

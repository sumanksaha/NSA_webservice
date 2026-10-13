# Current-stage assessment: soft and binary correctness, all 150 questions

Produced 2026-10-11, **no LLM calls** (budget for the day is zero). Everything
here is computed from the recorded generation cache and the re-measured
retrieval; nothing was re-generated.

Sources: `evaluation/out/ceiling_v5/reeval_89/gen_C_hybrid.jsonl`,
`gen_C_hybrid_section.jsonl` (150 records each), joined on qid to
`retrieval.jsonl` for cohort.

Sources (KG): `evaluation/kg_effect_on_failures.py` driven by production
`KGContextExpander`, reachable Neo4j instance (`21090cf9`). No LLM calls.

## 1. The numbers

`C_hybrid` (baseline), all 150:

| cohort | n | soft mean | soft median | soft range | binary | of which abstention-credited | **substantive** |
|---|---|---|---|---|---|---|---|
| failure (model_wrong) | 89 | 0.334 | 0.353 | 0.00-0.62 | 4/89 (4.5%) | 1 | **3/89 (3.4%)** |
| control | 61 | 0.357 | 0.366 | 0.00-0.54 | 15/61 (24.6%) | 11 | **4/61 (6.6%)** |

`C_hybrid_section` (scoped) is **worse on both cohorts**: failures 3/89 binary and 2/89 substantive; controls 13/61 binary and 4/61 substantive. The substantive failure passes are `Q015`, `Q024` (baseline adds `Q047`).

## 2. The headline binary gap is mostly composition, not quality

Control contains **14 `insufficient_evidence` questions (23%)**; failure contains
**1 (1%)**. The abstention credit applies only to IE questions, where the
benchmark itself says "a correct abstention is the right answer". So 11 of
control's 15 binary passes are *abstentions*, i.e. passes without a substantive
answer.

- Binary gap as recorded: 24.6% vs 4.5% -> **5.5x**
- Substantive gap: 6.6% vs 3.4% -> **1.9x**

Control's soft scores are also *lower* than failure's maximum (0.54 vs 0.62)
while its binary rate is higher. The gate is rewarding abstention, not similarity.
The IE rule itself is correct and should not be removed; the point is only that
the binary column is not a clean quality signal.

## 3. Where the failures actually are

Concept shortfall across the 85 incorrect failure questions:

| shortfall | count |
|---|---|
| 0 concepts missing, still scored wrong | 12 |
| 1 concept short | 33 |
| 2 concepts short | 18 |
| 3-4 concepts short | 21 |
| 5+ concepts short | 1 |

**72 of 85 are within 1-4 concepts of the reference.** Only one failure is
genuinely far away.

## 4. Retracted: the "59/60 gate reachability" figure

An earlier version of this document stated that `concept_gate_reachability.json`
showed `gold_echo_eliminates: 59/60` and concluded "the gate is effectively a
concept-coverage gate and it is nearly reachable". **That was wrong and is
withdrawn.**

Two reasons:

1. **The artifact is pre-filtered.** All 60 of its rows carry
   `concepts_all: true` and `binary: 1` — it was computed over the questions
   that had *already passed*. Proving that passing questions would pass says
   nothing about the 90 that did not, so it is not reachability evidence.
2. **It does not reproduce.** Re-derived with the frozen scorer
   (`reprobe_gold_echo`), the reference conclusion passes the gate **150/150**
   and a gold-concept echo passes **0/150** — the inverse of what it claims.
   Per-qid agreement is 4/60 and 1/60, i.e. chance.

There is also no gold-echo failure mode to guard against: gold concepts are
computed only under `evaluation/` and appear nowhere in
`app/rag/generation/`, so they never enter the prompt.

## 5. KG effect: offline promise, live ~zero

Offline (`kg_effect_on_failures`, no LLM): mean ctx coverage 0.444 -> 0.486
(+0.042), 46/89 positive, 9 crossing 0.5. **Live paired verification
(`verify_kg_live_llm`, 33 LLM calls, 2026-10-12): soft 0.355 -> 0.347 (delta
-0.008), binary 1/15 -> 1/15, eliminated 3/15 -> 4/15.** Full write-up in
`evaluation/KG_STATUS.md`; the short version:

- One replicated flip (Q016, 2/2, via a fused **gold** s53 identity pointer).
- One substantial harm (Q014, -0.157: five non-gold stubs evicted real context).
- Largest offline delta Q076 (+0.44) converted to -0.002 live.
- Mechanism: fused KG provisions are header-only stubs (identity, no statutory
  text). Offline IDF coverage counts their rare header tokens as distinctive;
  live, a section label is not substance — except as a pointer when it names
  gold.

**Decision: do not optimise the graph for correctness; do not run a full
89x2 sweep.** The legitimate KG item is populating `provision_text` (content,
not structure), re-verified with the same 30-call harness.

## 6. Near-miss character of the concept gaps, for the record

`concept_gate_diagnosis.json` classifies the 104 missing concepts as 61
near-miss / 43 far. In light of the KG measurement, near-miss is better read
as the answers reaching the right register without the specifics, not as a
retrieval gap. The split should be re-measured after the KG is enabled and
its effect on coverage is settled.

## 7. Retrieval state for the same questions

| | failure gold-in-top10 | control gold-in-top10 |
|---|---|---|
| before remediation | 45/89 | 36/61 |
| **after remediation** | **45/89** | **40/61** |

Control improved by 4; failures held. This matches the prediction that the
stamping remediation removes a mislabelling rather than adding text.

## 8. Data-integrity problems found while producing this

Both silently distort a naive roll-up:

1. **`cohort: None` on some generation records.** Grouping on that field
   demotes failures into control. The cohort is now taken from `retrieval.jsonl`,
   which labels every qid; this restored exact 89/61 splits (the first run
   reported 88/62 and 87/63).
2. **`concepts_missing` is a list, not a count.** Any numeric comparison against
   it raises; `len()` is required.

## 9. Standing caveats

- Generation is the **pre-remediation** cache. Retrieval was re-measured after
  remediation; generation cannot be refreshed without an LLM budget.
- Gold resolution remains at 2 unresolved units (`Q034 pcra:s63`, `Q042 pcra:s63`),
  the OCR-blocked `pcra` family (P0-2). This is separate from the FSSAI s52/s63
  operative-text gap that sizes the procurement at 2 questions.
- The KG now raises distinctive coverage (0.444→0.486 mean, 46/89 positive
  delta, 9 crossing 0.5). The KG is now part of the evidence pipeline, but
  does not by itself clear the gate — the length asymmetry blocker remains.
- The gold labelling check: gold provisions cover only 0.369 of the reference's
  distinctive mass, less than the answer's 0.442. The reference draws on material
  beyond the labelled gold. This should be checked before any further pipeline
  work, because it bounds everything else.

## 10. Model swap assessed: stay on laguna (2026-10-12, 29 LLM calls)

`evaluation/ab_model.py`: 13 QIDs × `apodex/apodex-1.1-mini:free` and
`nvidia/nemotron-3.5-lightning:free`, paired against cached laguna on identical
top-20 pools (slugs verified on the models listing first; 0-budget step).

| | laguna | apodex-mini | nemotron-lightning |
|---|---|---|---|
| soft mean (13) | 0.457 | 0.424 (−0.033) | 0.400 (−0.058) |
| binary | 4 | 3 | 1 |
| eliminated | 6 | 4 | 3 |
| answer length | 3090 chars | 3784 (+22%) | 3995 (+29%) |

**Do not switch.** Both lose veto items: apodex loses Q052/Q084/Q023
eliminations and the Q060 abstention credit; nemotron additionally collapses
Q015 (−0.307, substantive failure pass lost) and Q002's elimination. Only
bright spots (apodex Q015 elim gain, nemotron Q016 +0.116) don't outweigh them.

Mechanism (read off the answers, 0 extra calls): both models leak
chain-of-thought into the response (`Here's a thinking process: 1. **Analyze
User Question**...`). The prompt asks for quotes-then-concise-answer; the
reasoning trace becomes hundreds of scored tokens that drown jaccard — the
gate-hostile direction, and the already-measured binding constraint. Q060
shows the second half: both *answered* an IE question at 3800+ chars instead
of abstaining, forfeiting the credit laguna holds. This is structural
(thinking models under a length-punishing gate + citation contract), not
capability — a swap without a thinking-channel prompt and trace-stripping
post-processor cannot win. That is a project, not a config change, and it is
not proposed here.

Haiku 5.5 (`anthropic/claude-haiku-5.5`, 21 calls 2026-10-12) completes the
picture from the other side and sharpens the real conclusion. Decomposed
(`decompose_swap.py`, offline), haiku's landed answers run ~800 chars with
HIGHER jaccard but LOWER coverage than laguna almost everywhere (Q007 cov
0.43 vs 0.69, Q015 0.75 vs 1.0, Q123 0.22 vs 0.78, Q060 0.18 vs 0.82) — the
mirror image of laguna's disease. The one exception proves the rule: Q002
holds coverage 1.0 on both while haiku is shorter (1218 vs 1797), and haiku
wins that pair outright (0.579 vs 0.552). The ideal point sits *between* the
models: laguna's coverage at haiku's length. That is a length-targeted laguna
prompt, not a swap — and it is now the concrete next experiment (near-miss
set, the 69/82 a coverage-only gate would pass). Separately, haiku returned
deterministic empty 200s on 5/13 full RAG prompts across retries (Q052 Q047
Q023 Q020 Q016; trivial probe answers `PONG` fine) — a pipeline
incompatibility on top of the metric loss. Verdict: stay on laguna.
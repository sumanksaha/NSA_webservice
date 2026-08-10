# ENRICHMENT DESIGN — Implementation Plan (Phases 3–15)

> **Phase 2 deliverable.** Concrete design for enriching the existing 12,819
> FSSAI chunks, re-based on the audited architecture (Qdrant canonical store,
> Neo4j corpus graph + JSON/relational companions — Neo4j installed and
> connected 2026-08-10, httpx LLM client, 8 GB RAM budget).
> Design date: 2026-08-10 (updated 2026-08-10).

**Operating principle (from the task):** optimise for *maximum legally useful
retrieval improvement per unit of complexity, memory, API cost, and
hallucination risk* — not for maximum metadata.

---

## Pipeline overview (memory-bounded, resumable)

```
Qdrant scroll (or backup JSON) ──stream──▶ batch (default 50, max 100)
        │
        ▼
[Phase 3] deterministic enrichment (rules, zero API cost)
        │        section attribution · act/reg/rule/schedule · dates ·
        │        explicit cross-references · keywords · duplicates
        ▼
[Phase 4] LLM semantic enrichment (batch, OpenRouter via GroundedLLMClient)
        │        legal_concepts · obligations · conditions · exceptions ·
        │        retrieval_summary · question_types   (explicit/inferred/unknown)
        ▼
[Phase 12] structural validation  ──fail──▶ status=FAILED + error (retry later)
        ▼
persist to enrichment store (SQLite)  +  checkpoint row  +  resource metrics
        ▼
[Phase 6] cross-reference resolution (post-pass over store, index-aware)
        ▼
[Phase 8] graph build (controlled ontology → knowledge_graph.json + optional relational tables)
        ▼
[Phase 9] Qdrant Strategy A vs B experiment (NEW collection only — index stays intact)
        ▼
[Phase 14/15] evaluation baseline vs enriched + ablation → keep only what measurably helps
```

---

## Phase 3 — Deterministic enrichment first (zero cost, zero hallucination)

Ordered rules, implemented as pure functions over the payload (+ cleaned text):

0. **Page numbers** — the §5.1 payload carries **no page field** (audit:
   100 % missing). Pages are recorded as `unknown` in the enrichment record;
   they can only be backfilled later from the PDF provenance layer and are
   **not** a Phase 3 target.

1. **Section attribution** (highest value — 75 % of chunks lack it):
   * Own `section_number`/`section_title` from payload when present.
   * Else walk the document's chunk sequence: the last preceding
     header-bearing chunk (`section_number` set, `hierarchy_level` low)
     attributes its section to all following text chunks until the next
     header — **paragraph-inheritance attribution**, deterministic and
     document-local. Handles the audit's finding that headers exist (3,193
     header chunks) but text chunks don't carry them.
   * Line-anchored marker extraction as a fallback (reuse the fixed
     `regex_library` instrument patterns and the §5.1 subsection markers).
2. **Act/regulation/rule/schedule/annexure references** — reuse
   `MetadataAdapter` + `CrossRefAdapter` outputs already in the payload, plus
   the `SECTION_CONTEXT` map (FSS Act sections 1–542) from `scripts/build_kg.py`.
3. **Dates** — `effective_date`/`enactment_date`/`amended_date` from payload;
   in-text date parsing (explicit 4-digit years) only for unambiguous hits.
4. **Explicit cross-references** — the payload `citations` (e.g. `{"section":
   "55", "type": "statutory"}`) become `cross_references` with
   `relation=REFERS_TO`; candidates deferred to Phase 6 for chunk-level
   resolution.
5. **Retrieval keywords** — lowercase legal headwords from the chunk
   (title-case headwords, known FSSAI terminology list) — no LLM.
6. **Structural flags** — empty/short/long/multi-provision classification
   carried from the audit.

Deterministic output is `source=deterministic` with measured precision on a
labeled dev set (target ≥ 0.9 before any LLM is spent).

## Phase 4 — LLM semantic enrichment (batch, guarded)

* Batch size **50 default / 100 max** (`ENRICHMENT_BATCH_SIZE`). Streams via
  Qdrant `scroll` — never more than one batch in memory.
* Uses the existing `GroundedLLMClient` (httpx, OpenAI-compatible) with
  `RAG_LLM_MODEL`; prompt version `enrich-v1` instructs **JSON-only output**
  and per-field `kind ∈ {explicit, inferred, unknown}`, with the rule
  *"unknown is preferred over guessing for legal metadata"*.
* LLM is asked **only** for fields determinism cannot produce reliably:
  `legal_concepts`, `obligations`, `prohibitions`, `permissions`, `powers`,
  `duties`, `conditions`, `exceptions`, `offences`, `penalties`,
  `procedures`, `applicability`, `retrieval_summary`, `question_types`.
* **No LLM for**: section numbers, dates, authorities, penalties/obligations
  *without* evidence spans — validation downgrades unsupported claims to
  `unknown` (Phase 12).
* Retry policy: per-batch retry (1×) on transport errors; failed batches
  marked `FAILED` with the error stored — **never retained in memory**;
  restart re-tries only non-`VALIDATED` chunks (Phase 11).
* `RAG_USE_STUB_LLM=true` forces deterministic-only operation (offline-safe,
  zero cost) — a first-class mode, not a fallback.

## Phase 5 — Legal relationships (graph-ready, evidence-gated)

Relationships are derived from: (a) resolved cross-references (Phase 6),
(b) LLM extractions that carry evidence spans, (c) deterministic document
structure (`PART_OF`, `UNDER`, `IMPLEMENTS` from section/act metadata).
Every relationship row: `source_chunk_id · target_chunk_id ·
relationship_type · confidence · evidence · provenance`. No word-overlap
heuristics.

## Phase 6 — Cross-reference resolution (chunk-level, not just numbers)

* Build (streaming, memory-bounded) an index of `(document_id, section)` →
  `chunk_id` from the corpus; plus the FSS Act section map.
* For each candidate `citations[]`/`references[]`: resolve `(document,
  section, subsection)` → chunk; **verify the target chunk exists**;
  write `cross_reference` rows with `resolved=true`.
* Unresolved references are stored separately (`resolved=false`,
  `target_chunk_id=null`) — never guessed.
* Document identity: a reference inside a regulation to "the Act" resolves
  against the FSS Act document in the corpus (via `document_type=act` +
  section map); ambiguous targets stay unresolved.

## Phase 7 — Retrieval enrichment

`retrieval_keywords` (deterministic + LLM), `synonyms` (legally meaningful
variants — e.g. "FBO" ↔ "Food Business Operator", "licence" ↔ "license"),
`question_types` (who/when/what-conditions/what-penalty/who-can), and
`retrieval_summary` (≤ 40 words, factual, tagged `kind`, **never** replacing
`original_text`). These become **candidates** for the Phase 9 embedding
experiment and are also served to `ContextBuilder` as context hints.

## Phase 8 — Graph integration (controlled ontology; **Neo4j, updated 2026-08-10**)

**Status update (2026-08-10): the "defer Neo4j" decision is superseded —
Neo4j is now installed and connected.** The `neo4j` Python driver is
installed, `app/services/neo4j_graph.py` (`Neo4jGraphService`: APOC dynamic
labels, 9 uniqueness constraints, 3 property indexes, `push_to_neo4j` /
`query_neo4j` / `neo4j_configured`) and `neo4j_aura_loader.py` already exist
and are tested (`tests/test_neo4j_kg_sync.py` 15/15), and connectivity to
the configured instance was verified. Phase 8 therefore targets Neo4j as the
primary corpus-graph store, with the JSON knowledge graph kept as a
companion/fallback export.

* **New corpus graph loader** (`scripts/load_kg_to_neo4j.py`) over the
  **enrichment store** — the existing `scripts/build_kg.py` consumes
  `corpus_eval_result.json`, which is corrupt (JSONDecodeError, audited);
  the new loader reads the enrichment store + chunk payloads directly and
  upserts into Neo4j (build_kg.py stays untouched).
* Controlled ontology with stable IDs and deduped node keys
  (`SECTION:FSSAI_2006:32`, `ACT:FSSAI_2006`, `PROVISION:<chunk_id>`, …) and
  the evidence-gated relationship set from Phase 5 (`REFERS_TO` from the
  2,734 resolved cross-refs; `PART_OF` / `UNDER` / `IMPLEMENTS` from
  document structure).
* Reuse `Neo4jGraphService.setup_constraints_and_indexes` (MERGE, never
  duplicate nodes); every relationship carries `source_chunk_id ·
  target_chunk_id · relationship_type · confidence · evidence · provenance`.
* Additionally, the existing `entity`/`relationship` relational tables can
  host the same ontology with uniqueness constraints (SQLite-friendly
  companion, queryable, no server process).
* **Memory note (8 GB box):** the corpus graph is loaded in streaming batches
  from the enrichment store; the server-side Neo4j instance carries the
  resident graph (not the Python process). Graph-traversal evaluation on the
  53-Q dataset decides whether traversal is wired into retrieval —
  evidence-first, consistent with the task.

## Phase 9 — Qdrant compatibility (evidence before re-embedding)

* **Strategy A (control):** embed `chunk_text` only (current behavior —
  index untouched).
* **Strategy B (candidate):** embed `chunk_text + retrieval_summary +
  legal_keywords` into a **new** collection (`fssai_legal_768_enriched_v1`),
  keeping `fssai_legal_768` live and intact.
* Compare on the Phase 14 dataset (Recall@k / Precision@k / MRR / nDCG).
  **Adopt B only if it measurably wins**; payload-only metadata enrichment
  (no re-embedding) remains the default because it costs nothing at query
  time and keeps vectors stable.

## Phase 10 — Memory safety (8 GB budget)

* Stream from Qdrant (`scroll` pages) or backup; batch 50–100; process →
  validate → persist → `del` batch; checkpoint every batch; bounded retry
  queue; no full-corpus materialisation (the 151 MB audit load is a
  one-shot, optional analysis read).
* `reports/resource_usage.json` aggregated from the `resource_usage` table
  (peak/average RAM via `tracemalloc`/`resource` where available, duration,
  batch size, failed batches, retries).

## Phase 11 — Checkpointing

SQLite `checkpoint` + per-chunk `status` (PENDING → PROCESSING → ENRICHED →
VALIDATED | FAILED | SKIPPED). Restart resumes from `last_chunk_id`;
`VALIDATED` chunks are never reprocessed. `reports/enrichment_progress.json`
aggregates status counts.

## Phase 12 — Validation

Automated JSON-Schema + invariant checks per §6 of `ENRICHMENT_SCHEMA.md`
(immutability, evidence spans, no invented legal values, confidence range,
resolved-only cross-refs, dedup). Reuses the repo's citation validator
patterns. Unit tests in `tests/test_enrichment_validation.py`.

## Phase 13 — Hallucination guardrail

Authority hierarchy enforced end-to-end: `original_text` is the only
citation target for answers; LLM enrichment is never surfaced as law —
`retrieval_summary`/keywords are retrieval aids only; verification layer
(`HallucinationDetector`, `CitationValidator`) stays the final answer gate;
enrichment provenance makes any field traceable to its source.

## Phase 14 — Evaluation before deployment

* Author an eval dataset (RAGEvalDataset rows / JSON) covering the required
  archetypes: simple, procedural, multi-hop, cross-reference, exception,
  authority, penalty (target ~50–80 queries; starts with the corpus's real
  section map so expected citations are chunk IDs, not just section numbers).
* Metrics: Recall@k, Precision@k, MRR, nDCG, answer grounding, citation
  correctness, hallucination rate — via `EvalRunner` (6 metrics + MRR) and
  retrieval-rank metrics computed in the harness.
* Outputs: `reports/evaluation_baseline.json` (current corpus) vs
  `reports/evaluation_enriched.json` (with enrichment in the retrieval
  path). **Enrichment is kept only if it measurably improves retrieval.**

### ✅ Phase 14 results (2026-08-10, offline run)

Harness: `scripts/enrichment/evaluate_retrieval.py` — offline, deterministic,
over the backup vectors (12,819 × 768) + enrichment store; the live Qdrant
index is untouched. Dataset: `reports/eval_dataset.json` — expanded to **53
questions** (all 7 archetypes; 12 procedural / 9 authority / 9 cross-ref / 8
simple / 6 penalty / 5 exception / 4 multi-hop; 3 section-citing variants),
gold pinned to real corpus chunk IDs via distinctive answer phrases; **0
skipped**. Seeded into `rag_eval_dataset` (53 rows, all with resolved gold
chunk IDs + expected section) for reuse by `EvalRunner`.

**Enriched = the Phase 15-recommended config** (section boost + cross-ref
expansion, keyword/summary credits excluded — see Phase 15 below):

| Metric | Baseline (dense) | Enriched | Δ |
| ------ | ---------------- | -------- | - |
| Recall@5 | 0.660 | 0.689 | **+0.028** |
| Recall@10 | 0.736 | 0.755 | **+0.019** |
| Precision@5 | 0.140 | 0.147 | **+0.008** |
| MRR | 0.540 | 0.544 | **+0.004** |
| nDCG@10 | 0.584 | 0.592 | **+0.008** |

Enriched = dense-first re-ranking (production design — the vector index stays
authoritative, enrichment re-orders it):
* **+0.10 × matched keyword phrase** (lexical tie-break, phrase-level units
  from the enrichment keywords — token-bag matching was measured and
  **hurts**, see negative result below),
* **+0.20 × query-cited-section match** (chunk attributed to the section a
  user explicitly cites — lifted section-citing queries q18/q19 to top rank),
* **+0.05 × incoming cross-reference edge** (multi-hop recall: pool expands
  to referenced provisions, kept competitive without overtaking dense hits).

**Honest attribution — core vs section-citing split** (the 3 section-citing
queries q17–q19 were added specifically to exercise the section boost, so
the summary reports them separately; on the 19-question set):

| Subset | MRR Δ | Recall@10 Δ | nDCG@10 Δ |
| ------ | ----- | ----------- | --------- |
| Core 16 (natural queries) | +0.034 | +0.063 | +0.039 |
| Section-citing 3 | +0.278 | +0.000 | +0.199 |
| All 19 | +0.073 | +0.053 | +0.065 |

**Important generalisation check:** the earlier 19-question headline
(MRR +0.073) did **not** survive expansion to 53 questions when the keyword
credit was included (full config MRR −0.043). The ablation below identifies
why and what to keep.

Answer-level metrics (grounding, citation correctness, hallucination rate)
remain for the LLM-generation eval via `EvalRunner` once Phase 4 (LLM pass)
is enabled.

## Phase 15 — Ablation results (2026-08-10)

Feature-gated ablation on the **same 53-question dataset** — every variant
evaluated identically, deltas vs the dense baseline (from
`reports/ablation_results.json`):

| Variant | R@5 | R@10 | P@5 | MRR | nDCG |
| ------- | --- | ---- | --- | --- | ---- |
| baseline | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| + section | **+0.019** | +0.000 | +0.007 | **+0.013** | **+0.010** |
| + crossrefs | +0.009 | **+0.019** | +0.000 | −0.009 | −0.002 |
| **+ section+crossrefs (recommended)** | **+0.028** | **+0.019** | **+0.007** | **+0.004** | **+0.008** |
| + keywords | −0.019 | −0.019 | −0.004 | −0.041 | −0.033 |
| + keywords (multiword-only) | −0.066 | −0.075 | −0.015 | −0.092 | −0.087 |
| + summaries | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| full (all features) | −0.009 | −0.019 | +0.000 | −0.043 | −0.033 |

**Conclusions (maximum improvement with minimum complexity):**

1. **Keep: section boost.** Single best feature (+0.013 MRR, +0.010 nDCG);
   zero cost, and it is what makes section-citing queries land at rank 1.
2. **Keep: cross-reference expansion.** Net recall win (+0.019 R@10) with a
   small MRR cost (−0.009); together with section boost it is the only
   variant positive on **every** metric (+0.004..+0.028).
3. **Reject: keyword credit in retrieval.** The deterministic keyword
   extractor emits generic legal headwords ("Act", "food", "penalty",
   "offence" — top terms appear in 50–180 chunks) that add noise rather
   than signal: −0.041 MRR. The multiword-only probe is **worse** (−0.092),
   so this is not a filterable headword problem — the credit itself does not
   pay. Keywords remain useful as human-facing metadata and context hints,
   but are **not** used to re-rank. Revisit only if the LLM pass (Phase 4)
   produces markedly more specific keywords.
4. **Defer: summaries.** `retrieval_summary` is empty in deterministic mode
   (0/2000 sampled) — the variant is inert by construction. Must be
   re-measured after the LLM pass populates it.
5. **Do not deploy "full".** Combining all features underperforms baseline
   (MRR −0.043) — keywords drag the good parts down.

**Production recommendation:** deploy `section+crossrefs` re-ranking (the
config `reports/evaluation_enriched.json` already reports). This is the
maximum retrieval improvement per unit of complexity, consistent with the
task's operating principle.

**Honest ceilings & significance:** with 53 queries the +0.004 MRR / +0.008
nDCG deltas on `section+crossrefs` are within noise — the recommendation
rests on the larger, more credible effects: R@5 +0.028 / R@10 +0.019 for the
kept config, and the decisive negative results (keywords −0.041 MRR,
multiword probe −0.092) that justify rejecting the keyword credit.
Additionally, the crossrefs variant is a **conservative lower bound** on
expansion value: expanded chunks start at dense score 0.0 and are capped at
+0.15, so they can never outrank a decent direct hit — the +0.019 R@10 gain
is therefore real but understated. Revisit both after Phase 4 (LLM pass)
populates `retrieval_summary` with genuinely specific keywords/summaries.

## Phase 15 — Ablation

Run matrix: baseline → +metadata → +entities → +legal_concepts →
+cross_references → +graph retrieval → +retrieval_summaries (each increment
evaluated on the same dataset). Output `reports/ablation_results.json`.
Target: *maximum improvement with minimum complexity* — prune any feature
that doesn't pay for itself.

---

## Implementation order (unchanged from task)

1. ✅ Repository audit → 2. ✅ Chunk audit → 3. ✅ Schema design →
4. Deterministic extraction → 5. Checkpoint system → 6. Batch LLM enrichment
→ 7. Validation → 8. Cross-reference resolution → 9. Graph relationships →
10. Retrieval enrichment → 11. Baseline-vs-enriched evaluation → 12.
Ablation → 13. Production integration (opt-in flag, additive payload merge).

---

## Resolved decisions (2026-08-10, user-confirmed)

1. **LLM mode — deterministic-only first.** Implement + evaluate the Phase 3
   rules-based enrichment and measure retrieval impact before any API calls;
   the LLM semantic pass (Phase 4) is a later, optional second stage wired
   through the same checkpoint/validation machinery.
2. **Graph target — Neo4j (updated 2026-08-10).** Neo4j is now installed and
   connected (driver 6.2.0, `Neo4jGraphService` + `neo4j_aura_loader.py`
   exist, connectivity verified). Phase 8 loads the corpus ontology
   (provision-level nodes + evidence-gated relationships) into Neo4j, reusing
   `Neo4jGraphService`; `knowledge_graph.json` remains a companion/fallback
   export.
3. **Batch size / cost ceiling** — default 50, max 100 (`ENRICHMENT_BATCH_SIZE`);
   no LLM spend until deterministic evaluation passes.
4. **Backfill the DB registry — yes.** Populate `legal_document`/`legal_chunk`
   from Qdrant payloads and persist enrichment in new ORM tables
   (`ChunkEnrichment` / `EnrichmentCheckpoint` / `ChunkCrossReference` /
   `ResourceUsage`) in the app DB — enrichment becomes SQL-queryable.

## Pipeline phases (updated order)

1. ✅ Repository audit → 2. ✅ Chunk audit → 3. ✅ Schema design →
4. ✅ **Deterministic extraction** — implemented, ran 12,819/12,819,
   0 failures. Section attribution via paragraph inheritance lifted coverage
   from 24.9 % (3,193 chunks) to **99.4 % (12,748 chunks)**; retrieval
   keywords on 6,890 chunks. → 5. ✅ **Checkpoint system** — SQLite
   checkpoints, resume verified (re-run reprocessed only the 58 failed
   chunks, then 0). → 6. ⏸ LLM batch enrichment (deferred until
   deterministic evaluation — per user decision) → 7. ✅ **Validation** —
   invariants enforced, all records VALIDATED. → 8. ✅ **Cross-reference
   resolution (first pass)** — 2,734 resolved REFERS_TO edges (2,344 source
   chunks → 528 targets); same-doc anchor for multi-chunk sections,
   unambiguous Act fallback, self-loops dropped.   → 9. ⏳ Graph relationships (Phase 8,
   **Neo4j** — installed + connected 2026-08-10; JSON graph kept as
   companion/fallback) → 10. ⏳ Retrieval enrichment →
   11. ✅ **Baseline-vs-enriched evaluation** — `scripts/enrichment/evaluate_retrieval.py`;
   dataset expanded to 53 Qs / 7 archetypes (all gold resolved, seeded into
   `rag_eval_dataset`); recommended section+crossrefs config positive on all
   metrics (MRR +0.004, nDCG@10 +0.008, Recall@10 +0.019). →
   12. ✅ **Ablation** — `reports/ablation_results.json`; section boost + cross-ref
   expansion kept (net positive), keyword credit rejected (−0.041 MRR,
   multiword probe worse at −0.092), summaries deferred (field empty until
   LLM pass), "full" rejected (−0.043 MRR). Production config:
   `section+crossrefs`. → 13. ⏳ Production integration.

# BENCHMARK CREATION REPORT — Legal-RAG Golden Benchmark v1.0

**Date:** 2026-08-11 · **Assembler:** `build_benchmark.py` (canonical; loads `benchmark/content/*.py`)
**Scope:** 150-question frozen golden benchmark for multi-domain Legal RAG
(Qdrant retrieval · Neo4j retrieval · hybrid retrieval · provision enrichment ·
cross-domain retrieval · authority/legal-effect · temporal · citation ·
hallucination/abstention).

This report documents the completed build of the benchmark that was **partly
implemented** (content questions + gold registries authored across the seven
content modules). The remaining 63-question shortfall reported earlier was
already authored in the content modules (150 total across all modules); what was
missing/broken was the **assembler and the derived artifact files**. This pass:

1. **Fixed the content layer** — added the missing gold-provision registry
   records (FSS s28/s31/s75/s76/s95, EPA s26, PWM amendment rules, generic
   regulation references) and corrected the wrong `document_id` in a cross-module
   record, so **every provision reference in all 150 questions now resolves**
   (97 unique gold records, previously 87 with 12 dangling references).
2. **Wrote the missing canonical assembler** `build_benchmark.py` — referenced by
   `benchmark/content/__init__.py` but absent.
3. **Rebuilt all seven spec artifacts** — replacing corrupt/truncated JSONs
   (`benchmark_questions_v1.0.json`, `benchmark_questions_fixed.json`,
   `gold_provisions_v1.0.json` failed to parse) and old-schema generator scripts
   with the frozen v1.0 artifact set.
4. **Ran Agent A/B gold verification** — registry cross-checked against the live
   Neo4j corpus (22/22 documents matched, 8 provision-id collisions resolved by
   domain-union, recorded in `review_conflicts_v1.0.json`).
5. **Froze v1.0** with a SHA-256 checksum (below).

---

## Final counts

| Metric | Count |
| --- | --- |
| Questions | **150** |
| High-confidence | **150** |
| Medium-confidence | 0 |
| Rejected | 0 |
| Gold provision records | **97** (deduplicated; 105 authored across modules) |
| Source documents referenced | **22** |
| Live Neo4j document match | **22 / 22** |
| Resolved provision-id collisions | 8 (domain-union, same provision) |
| Unresolved conflicts | **0** |

### Domains (a question may carry several)

| Domain | Question tags |
| --- | --- |
| FOOD_SAFETY | 72 |
| MUNICIPAL | 38 |
| ENVIRONMENT_POLLUTION | 37 |
| PUBLIC_HEALTH | 35 |
| ANIMAL_SLAUGHTER | 27 |
| BUSINESS_CIVIL | 16 |
| LAND_PREMISES | 8 |

All seven spec domains are represented. The spec's suggested per-domain
distribution was treated as a target; the corpus's real multi-domain density
(heavy FSSAI/municipal/environment coverage) drives the distribution. FSSAI-only
(30), animal (15), env (15), public-health (10), business (10) and municipal
(20) single-domain buckets are each satisfied, with cross-domain questions (61)
providing the multi-domain coverage the spec requires (≥35).

### Question types

| Type | Count |
| --- | --- |
| Obligation | 53 |
| Authority | 40 |
| Procedure | 39 |
| Exception | 27 |
| Cross-reference | 22 |
| Prohibition | 20 |
| Direct provision | 19 |
| Enforcement | 16 |
| Insufficient-evidence (abstention) | 15 |
| Penalty | 15 |
| Temporal | 8 |
| Ambiguous | 7 |
| Offence | 2 |
| Enforceable-right | 1 |

All fourteen required question categories are present. Abstention questions meet
the ≥15 requirement (15) and cross-domain questions exceed the ≥35 requirement
(61).

### Difficulty

| Level | Count | Target |
| --- | --- | --- |
| EASY | 40 | 30 |
| MODERATE | 42 | 45 |
| HARD | 45 | 45 |
| ADVERSARIAL | 23 | 30 |

Approximately satisfied (spec §14 "target", §25 "approximately satisfied"):
EASY runs ~10 above target, ADVERSARIAL ~7 below — no questions were relabelled
in this pass; difficulty reflects author assignment.

### Capability coverage

| Capability | Count | Spec minimum |
| --- | --- | --- |
| Cross-domain (≥2 domains) | 61 | ≥35 |
| Insufficient-evidence / abstention | 15 | ≥15 |
| Scenario-based (realistic food/municipal scenarios) | 55 | ≥50 |
| Authority (who may inspect/licence/enforce/prosecute) | 40 | present |
| Exception (proviso/exemption/threshold/special category) | 27 | present |
| Temporal (current/amended/repealed/effective) | 8 | present |
| Penalty | 15 | present |
| Cross-reference | 22 | present |

### Splits (§22 — holdout discipline)

| Split | Count | Use |
| --- | --- | --- |
| dev | 100 | iterative development |
| validation | 30 | frozen validation set |
| holdout | 20 | final confirmation only — never inspect during tuning |

Splits are assigned deterministically — questions ordered by
``sha256(question_id)`` (pure hash, no RNG) then sliced 100/30/20 by position —
so the assignment is exact (100/30/20), stable across rebuilds on any Python
version, and recorded per-question in the JSONL. Rebuild determinism verified:
identical checksum across consecutive builds.

---

## Quality control (all gates passed)

- [x] 150 questions exist
- [x] All questions have gold evidence (`acceptable_conclusion`, primary provisions)
- [x] All gold provisions exist and resolve (0 dangling references)
- [x] All source references resolve (gold_source_chunks ⊆ registry; documents verified live 22/22)
- [x] No unresolved high-confidence conflicts (review_conflicts: conflicts = [])
- [x] No duplicate questions (exact-dup check: 0; near-dup similarity > 0.85: 0 pairs)
- [x] All domains represented
- [x] Cross-domain questions represented (61)
- [x] Negative/abstention questions represented (15)
- [x] Temporal questions represented (8)
- [x] Authority questions represented (40)
- [x] Exception questions represented (27)
- [x] Difficulty distribution approximately satisfied

---

## Independent gold verification (Agent A vs Agent B)

- **Agent A** authored the questions + proposed gold evidence (content modules).
- **Agent B** independently re-derived gold evidence from the source registry and
  cross-checked every `document_id` against the **live Neo4j corpus**:
  - 22/22 documents matched to live instruments (4 via explicit naming-variant
    aliases: the FSS local-DB UUID → `FSS_ACT_2006`;
    `environment_protection_act_1986` → `ENV_PROTECTION_ACT_1986`;
    `specific_relief_act_2017` → `SPECIFIC_RELIEF_ACT_1963`;
    `wb_premises_tenancy_1997` → `WB_PREMISES_TENANCY_ACT_1997`).
  - 8 provision-id collisions (env vs public_health modules) resolved by
    domain-union — same provision, different domain lens; recorded in
    `review_conflicts_v1.0.json`.
  - 0 unresolved disagreements.
- One flagged nuance (non-blocking): the Specific Relief Act is cited as 2017 in
  content while the live corpus instrument is `SPECIFIC_RELIEF_ACT_1963` (the
  2017 Act re-enacted the 1963 Act); both are recorded as matched and the
  reference resolves textually.

---

## Checksum / freeze

```
BENCHMARK_VERSION = 1.0
SHA-256(benchmark_v1.0.jsonl) = a950eb46e82c58cd22c9d966cb49c3d3b15a9e38214f888ddda861ec5a480815
```

Artifacts (all under `benchmark/`):

```
benchmark/
├── benchmark_v1.0.jsonl            # 150 frozen questions (197 KB)
├── gold_provisions_v1.0.json       # 97 gold provision records
├── gold_sources_v1.0.json          # 22 source documents / collections
├── benchmark_metadata_v1.0.json    # distributions + freeze record
├── evaluation_rubric_v1.0.md       # retrieval/evidence/generation/safety scoring
├── review_conflicts_v1.0.json      # Agent A/B verification (0 conflicts)
└── README_v1.0.md                  # freeze statement + checksum
```

Regeneration is deterministic:

```bash
python build_benchmark.py          # rebuild + freeze (non-destructive)
python build_benchmark.py --check  # validate content only
```

---

> **Benchmark v1.0 is frozen and must not be modified during subsequent retrieval optimization.**

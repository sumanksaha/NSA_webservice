# Multi-Domain Integration — Plan & Status

> **Purpose:** Single reference for turning the FSSAI-only RAG stack into a multi-domain
> legal RAG system. Reconciled with `RAG_AUDIT_REPORT.md` (2026-08-08 audit) — the audit's
> "multi-domain corpus beyond FSSAI" gap is exactly what this program closes.
> Status captured **2026-08-11** (Phases 0–2 done + Knowledge-Graph track: Option B rebuild,
> semantic enrichment, hybrid expansion).
> Companion docs: `RAG_AUDIT_REPORT.md` · `other domain/manifest.json` · `docs/enrichment/TODO.md`
> · `KG_READINESS_AUDIT.md` · `KG_READINESS_AUDIT_POST_REBUILD.md`.

---

## 1. Context

`RAG_AUDIT_REPORT.md` found the FSSAI RAG system complete (694 RAG-related tests) and listed
**multi-domain corpus** among its missing pieces. The user added 27 legal PDFs in
`other domain/` — central and West Bengal instruments spanning environment, commercial,
animal-welfare, state law, and (new) criminal law (Bharatiya Nyaya Sanhita, 2023). The
decision (user-confirmed 2026-08-10): **per-domain Qdrant collections + a query router**,
full pipeline (ingest → enrichment → eval → router), manifest authored by the assistant
and reviewed by the user.

## 2. Corpus manifest — `other domain/manifest.json` (✅ closed 2026-08-10)

| Domain | Collection | Documents | Notes |
| --- | --- | --- | --- |
| fssai | `fssai_legal_768` | (existing 12,819-chunk corpus) | untouched — canonical FSSAI index |
| env | `env_legal_768` | 14 | EP/Water/Air Acts, PWM family (2016 gazette + Jul/Aug 2022 amendments + drafts), SWM 2026, EPR draft |
| commercial | `commercial_legal_768` | 8 | Companies 2013, Contract 1872, Sale of Goods 1930, Partnership 1932, LLP 2008, Limitation 1963, Specific Relief 1963, CPA 2019 |
| animal | `animal_legal_768` | 4 | PCA Rules 2017 (scanned→OCR), Bengal Diseases of Animals (Amdt) 2008, Bengal Livestock Import Quarantine Rules 1944, WB Infectious Diseases in Animals Rules 2016 (scanned→OCR), WB meat order |
| wb_state | `wb_state_legal_768` | 2 | KMC Act 1980 (286 pp), WB Premises Tenancy Act 1997 |
| criminal | `criminal_legal_768` | 1 | **Bharatiya Nyaya Sanhita, 2023** (IPC successor; assent gazette CG-DL-E-25122023-250883, 102 pp; in force 2024-07-01) |

**Verification highlights (2026-08-10):** every file identified by content probe, not filename —
`view-casepdf.pdf` = Air Act 1981, `view-casepdf-1.pdf` = LLP Act 2008, `Essential_Commodity_act_1955.pdf`
= WB meat order, `SWM_2026-1.pdf` = byte-identical duplicate of `SWM_2026.pdf` (SHA-256 verified),
PWM "duplicates" = two distinct gazette amendments (Jul + Aug 2022).
**Flags:** 4 drafts marked `is_current: false`; 1 skip (`ingest: false` + `duplicate_of`).
Each entry carries `document_id`, `title`, `document_type` (§5.1 enum), `authority`,
`jurisdiction`, `state`, `domain`, `act_name`, dates, `is_current`, `notes`.

## 3. Domain topology (Phase 1, implemented)

| Layer | Where | What |
| --- | --- | --- |
| Domain → collection | `app/rag/collections.py` | `DOMAIN_COLLECTIONS` map + `collection_for_domain()` with `RAG_QDRANT_COLLECTION_<DOMAIN>` config override (set in `app/__init__.py`); aliases (`food`, `environment`, `state`, `municipal`, `penal`) |
| Per-act section registry | `app/rag/legal_sections.py` | `ACT_SECTION_RANGES` (FSS 1–104, Air 1–54, Water 1–64, EP 1–26, Companies 1–470, Contract 1–238, Sale of Goods 1–66, Partnership 1–74, LLP 1–81, Limitation 1–32, Specific Relief 1–44, CPA 1–107, **BNS 1–358**); length-guarded normalization; unknown act → `None` (never a false negative) |
| Payload `act_name` | `app/rag/chunker.py` | New §5.1 field stamped from the manifest; flows to enrichment store + crossref known-ness |
| Act-aware crossrefs | `app/rag/crossref_adapter.py` | `known` flag resolves against `chunk.act_name` (FSS default for legacy) |
| Enrichment act resolution | `app/rag/enrichment/deterministic.py` | `legal_act_of()`: manifest `act_name` → act's own title → FSS only when the doc is recognisably FSS; otherwise `unknown` (no guessing) |
| Domain prompts | `app/rag/generation/prompt_template.py` | `DOMAIN_SYSTEM_PROMPTS` (fssai/env/commercial/animal/wb_state/criminal/general); unknown domain → FSSAI fallback |
| Claim verification | `app/rag/verification/claim_extractor.py` | Generic statute regex (any `Act/Rules/Regulations`, parenthetical names ok) alongside FSS alternates |
| Collection threading | `app/rag/ingestion.py` + `app/rag/qdrant_indexer.py` | `make_ingestion_pipeline(collection=...)` → `QdrantIndexer(collection_name=...)` → `QdrantStore` |

## 4. Phase plan

### Done
- **Phase 0 — Corpus org + manifest** ✅ — 27 docs / 5 domains, all content-verified, drafts/skips flagged.
- **Phase 1 — de-FSSAI pipeline** ✅ — registry + collections + `act_name` + act-aware enrichment/crossrefs + domain prompts + generic claims + collection threading. `tests/test_multidomain_phase1.py` (37) green; 694 RAG-related tests green.

### Core (next)
- **Phase 2 — Manifest-driven ingestion** — ✅ **implemented 2026-08-10**: `scripts/ingest_multidomain.py` (honors `ingest: false` / `duplicate_of` / drafts; `--domain`/`--only`/`--dry-run`/`--skip-ocr`/`--reindex`; per-domain `pipeline.indexer.ensure_collection()` — fixes the indexer's no-auto-create gap found in the readiness benchmark; Devanagari strip pre-chunk via `make_ingestion_pipeline(cleaner=...)`; spaCy entity extraction; per-domain + master JSON under `reports/`). Validated end-to-end: `env_legal_768` created, EP Act 154/154 chunks upserted with full §5.1 payloads. Usage:
  ```
  python scripts/ingest_multidomain.py --dry-run    # pre-flight, no writes
  python scripts/ingest_multidomain.py --skip-ocr   # pass 1: 24 text docs (~1.5–2.5 h)
  python scripts/ingest_multidomain.py --reindex    # fresh replace on re-runs
  python scripts/ingest_multidomain.py              # pass 2 incl. OCR (~+47 min)
  ```
  **Executed 2026-08-10 — full corpus (26/26 docs) ingested, 0 failures** across 5 collections (14,524 points total): pass 1 text (66 min) → `env_legal_768` 2,465 · `commercial_legal_768` 7,584 · `animal_legal_768` 275 · `wb_state_legal_768` 1,735 · `criminal_legal_768` 1,260; pass 2 OCR (30 min, ~100 s/page warm) → `animal_legal_768` +1,205 (Cruelty Rules 1,100 + WB Infectious Diseases 105, both English OCR clean enough for retrieval). `fssai_legal_768` untouched. Per-domain + master summaries in `reports/ingest_multidomain_*.json`.
  **Post-ingest payload audit** via `scripts/enrichment/audit_chunks.py` per collection (resolves the audit's §3.2 schema-coverage uncertainty) is queued with Phase 3.
- **Phase 3 — Enrichment for new collections** — parameterize `backfill_registry.py` / `enrich_pipeline.py` by collection; per-domain `reports/enrichment_<domain>.json`.
- **Phase 4 — Domain router + abstention** — extend `QueryClassifier` with domain routing (food/env/commercial/animal/wb_state/criminal); **abstain when no domain matches or top score is below threshold** (closes the audit's "deliberate abstention" gap).
- **Phase 5 — Per-domain evaluation** — extend the offline 53-Q harness to any collection; per-domain ablation decides the re-ranking config per domain.
- **Phase 6 — Generation generalization** — thread domain through `GroundedGenerationService`/`ContextBuilder` (PromptTemplate already supports it); full regression.

### Audit-derived (evidence-first)
- **Phase 7 — Temporal-aware retrieval** — date filters (`effective_date`/`enactment_date`), `is_current` gating, superseded-document handling (PWM 2016 → 2022 amendments). Data now manifest-populated.
- **Phase 8 — Source-hierarchy tie-break candidate** — statute > rule > notification/circular; keep only if the per-domain ablation proves it.
- **Phase 9 — Persistent claim ledger** (optional) — store verified/unverified claims per query for cross-domain audit; reuse `ClaimExtractor`/`EvidenceVerifier`.

### Explicitly out of scope
LangChain/LangGraph/IRAC/game-theory/Talebian analysis, Pydantic structured LLM output, human-in-the-loop review gate — the audit agrees these are speculative enhancements, not requirements of this stack.

## 5. Knowledge-Graph track (2026-08-11) — rebuild + semantics + hybrid

The 2026-08-10 readiness audit (`KG_READINESS_AUDIT.md`) scored the pilot Neo4j graph
**32/100 (Nascent, NOT READY)**. The recommended fix was **Option B: rebuild the KG from
the real multi-domain corpus**, which is now complete (`KG_READINESS_AUDIT_POST_REBUILD.md`):

| Layer | Where | What |
| --- | --- | --- |
| Corpus KG rebuild | `kg/corpus_ingestion.py` + `scripts/build_kg_corpus.py` | Manifest + live Qdrant payloads (read-only) + FSS `LegalChunk` DB → batched `UNWIND` MERGE rebuild: 58 instruments, 1,861 provisions, 27,343 chunks, every provision with a `BELONGS_TO_DOMAIN` edge + `SOURCE_OF` provenance + temporal status; supersession edges (`REPLACES`/`REPEALS`/`AMENDS`) with evidence |
| Semantic enrichment | `kg/enrichment.py` + `scripts/enrich_kg_semantics.py` | Deterministic rule-based tagging (no LLM): `IMPOSES_DUTY`/`PROHIBITS`/`CREATES_OFFENCE`/`PRESCRIBES_PENALTY`/`GRANTS_POWER_TO`/`GRANTS_PERMISSION`/`PRESCRIBES` → typed `LegalConcept` nodes, evidence fragment + confidence per edge. 751 edges written 2026-08-11 (idempotent `MERGE`); `--min-confidence` gates rules (generic `shall` duty = 0.7, token-scoped prohibition precedence) |
| Hybrid expansion | `kg/hybrid.py` (`KGContextExpander`) | Qdrant chunk IDs → Neo4j provisions/instrument/domain/temporal status/authorities/provenance + related cross-refs. Wired into `run_generation_pipeline` behind **`RAG_KG_EXPANSION`** (default off; `app/rag/tasks.py` + `app/__init__.py`) |
| Scoring | `KG_READINESS_AUDIT_POST_REBUILD.md` | **69/100 (Operational, READY for controlled hybrid retrieval)** — all 6 retrieval tests green (municipal 0 → 284; slaughterhouse via real WB Meat Order 1966 s.3) |

**KG usage:**
```
python scripts/build_kg_corpus.py --dry-run     # pre-flight, no writes
python scripts/build_kg_corpus.py               # full rebuild (clear + ingest)
python scripts/enrich_kg_semantics.py --dry-run # planned semantic edges, no writes
python scripts/enrich_kg_semantics.py           # write semantic edges (idempotent)
# enable graph expansion in the RAG pipeline:
#   RAG_KG_EXPANSION=true  → /api/rag/query responses include kg_expansion
```

## 6. Reconciliation notes (doc drift fixed 2026-08-10)

| Doc | Issue | Fix |
| --- | --- | --- |
| `RAG_AUDIT_REPORT.md` §6.2 | `RAG_COLLECTION_NAME` (not a real env var) | Corrected to `RAG_QDRANT_COLLECTION` |
| `RAG_AUDIT_REPORT.md` §3.2 | Payload table lists fields the code doesn't emit (`act_number`, `year`, `cited_sections`, `cited_acts`, `chunk_quality_grade`, `language`, `source_hash`) | Annotated: pre-2026-08-09 snapshot; current §5.1 payload verified against `Chunk.to_payload()` |
| `RAG_AUDIT_REPORT.md` §2 | "695 RAG tests", "1,733 total" | Updated: verified collect 2026-08-10 → **1,757 total / 694 RAG-related** |
| `agents.md` | "437 RAG tests" (subset accounting), "~700+ tests" | Header + inventory annotated with the reconciled totals |

## 7. Guardrails (binding)

1. `fssai_legal_768` and all existing payloads/vectors untouched.
2. `original_text` immutable — enrichment is additive, beside the payload.
3. Per-domain rollback via collection isolation.
4. Drafts (`is_current: false`) are never surfaced as current law.
5. Every retrieval feature ships only if the per-domain ablation proves it.
6. Every phase keeps the 694 RAG-related + full 1,757-suite tests green.

_End of MULTIDOMAIN_INTEGRATION.md_

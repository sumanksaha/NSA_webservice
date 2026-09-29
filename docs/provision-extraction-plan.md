# NLP-based Statutory Provision Extraction & Isolation — Implementation Plan

Status: **planned, re-scoped 2026-09-27** after review against
`docs/RAG_AUTORESEARCH_RESEARCH.md` and `docs/RAG_EFFECTIVENESS_EVALUATION.md`.
Date: 2026-09-27.

Scoped decisions (confirmed via questionnaire):
- **Technique:** hybrid — rules + statistical ML (scikit-learn; rules-only fallback).
- **Integration:** new pipeline stage + corpus backfill (ingestion-time adapter + `scripts/` backfill over existing indexed text).
- **Granularity:** section + subsection (subsections as child records matching gold grammar `<family>:s<section>[((sub))]`).

## 1. Background

Provision boundary detection today is regex/rule-first:
`legal_paragraph_detection_engine` section patterns + L4 any-position header regex
(`chunker.py:211`) gated by `ACT_SECTION_RANGES` (`app/rag/legal_sections.py`).
Known failure modes (documented): page numbers, cross-references, year-like numbers,
merged/inline headings (`scripts/strip_reg_section_noise.py` fixed 1,518+36 bogus
stamps). Coverage post-P1/P2: 82.4% of substantive chunks
(`docs/archive/COVERAGE_COMPLETENESS.md`).

Environment constraints at planning time:
- Project-local `./venv`: `numpy`/`scipy` only (no torch/st/sklearn/pytest deps).
- System Python (`/home/suman_saha/.local/bin/python`, also `/usr/bin/python3`):
  torch 2.14.0+cpu, sentence-transformers 6.1.0, **scikit-learn 1.9.1**,
  transformers 5.17.0, Flask, qdrant-client, joblib — CPU only, no CUDA.
- Established test/ML runner: `/home/suman_saha/.venv-nsa`
  (created with `--system-site-packages`, inherits the above; `pytest` installed
  in-venv). sklearn imports stay lazy in app code with a rules-only fallback.
- No spaCy, no LLM key anywhere.
- Raw statute PDFs are **absent** from this checkout (only 4 scanned PDFs in `other domain/`);
  corpus-scale text exists only as indexed payloads
  (`evaluation/out/cache/payload_index.jsonl`, 27,361 chunks) + live Qdrant.
  → backfill works over payload text, ordered by `(document_id, chunk_index)`.

Gold identifier grammar to satisfy: `<family>:s<section>[(<sub>)]`,
families per `benchmark/benchmark_v1.0.jsonl` + `gold_provisions_v1.0.json` (97 records),
parsed by `evaluation/benchmark.py::_section_from_id` and resolved via
`evaluation/resolution.py` (`FamilyMap`/`_FAMILY_ALIASES`, `matches_gold`).

## 2. Architecture

New package `app/rag/provision_extractor/` with a candidate → feature →
disambiguate → isolate pipeline:

```
candidates → features → disambiguator (sklearn | rules fallback) → isolator → payload/KG writers
```

### 2.1 Candidate generation (`candidates.py`)
Grammar-aware boundary proposal over ordered document text:
- Reuse `SECTION_PATTERNS` (`legal_paragraph_detection_engine/src/parsers/section_parser.py`),
  L4 any-position regex (`chunker.py:211`), dotted-clause regex (`chunker.py:361`),
  EPA-style ALL-CAPS and `NN. Title.—` em-dash forms, chapter/schedule banners.
- Output: boundary candidates (position, source pattern, raw number token).

### 2.2 Feature extraction (`features.py`)
Per-candidate numeric/categorical features:
in-line vs line-start position; capitalization/punctuation shape (`.—`, `.-`, `:`,
`CHAPTER`); page-number likelihood; gazette running-head detection
(`Act 29 of 1986 … 269`); year-token check (1900–2100); cross-ref context
(`section 31` mid-sentence); monotonicity vs previous accepted number; membership in
`ACT_SECTION_RANGES` (fail-closed for unknown acts); first-occurrence vs mid-text.

### 2.3 Disambiguator (`disambiguator.py`)
Logistic regression (sklearn) over features → P(boundary).
Config `PROVISION_EXTRACTOR_MODE=rules|hybrid` (declared in `app/shared/config.py` +
documented in `.env.example`):
- **hybrid**: load `models/provision_boundaries.joblib`; absent/corrupt → auto-fallback
  to rules (never blocks ingestion). sklearn imported lazily (existing adapter pattern).
- **rules**: pure feature weights, no sklearn import at runtime.

### 2.4 Isolator (`isolate.py`)
Stitch a document's chunks in `chunk_index` order, split at accepted boundaries, then:
- Strip noise within each span (page numbers, running heads, chapter banners —
  banners recorded as parents, not content).
- Emit `ProvisionRecord` (`models.py`):
  `{instrument_id, family, provision_number, subsection, title, text,
  parent_id (chapter/schedule), source_chunk_ids, char_span, confidence}`.
  Subsections (`31(1)`) are child records under `31`.
- Provenance `source` field (`candidate_l4|engine|ml`), mirroring the `L4_override`
  convention.

### 2.5 Registry (`registry.py`)
Single source of truth mapping act/document title → family token, following
`evaluation/resolution.py::FamilyMap`/`_FAMILY_ALIASES` conventions so emitted ids
satisfy the gold layer.

## 3. Integration points

- **Ingestion (future ingests):** optional adapter stage in
  `IngestionPipeline.ingest_text()` after the chunker (`app/rag/ingestion.py:300`),
  gated by `PROVISION_EXTRACTOR_ENABLED`. Writes new payload fields
  (`provision_spans`: chunk_id → provision id/subsection; `provision_confidence`);
  existing `section_number`/`provision_id` semantics intact.
  **chunk_ids stay stable** (retrieval caches, `gold_source_chunks`, stash reuse
  depend on them).
- **Corpus backfill (main deliverable):** `scripts/backfill_provision_extraction.py`
  reads `payload_index.jsonl` (or scrolls Qdrant), groups by `document_id`, orders by
  `chunk_index`, extracts + isolates, validates, then idempotent payload writes
  (pattern: `scripts/stamp_qdrant_payload_identity.py`; dry-run, per-collection,
  rebuilds `payload_index.jsonl`).
- **KG:** extracted `ProvisionRecord`s feed `kg/corpus_ingestion.py::build_provisions`
  (replacing its grouping heuristic), keeping its fail-closed `_valid_section` checks.
- **ML training:** `scripts/train_provision_boundaries.py` — silver labels from
  high-confidence existing stamps (`provision_id` present, `sections_covered`,
  non-`L4_override` sources) + hard negatives from `strip_reg_section_noise` findings;
  optional hand-labeled correction file. Writes joblib artifact + metrics JSON.

## 4. Evaluation gates (before any live backfill)

`evaluation/provision_extraction_eval.py` over a held-out document set vs
`gold_provisions_v1.0.json`:
- Boundary precision/recall per numbering family (Acts / dotted regulations / EPA all-caps).
- Noise-stamp rate: bogus section stamps → target ≈ 0 on the strip-noise corpus.
- Gold resolution: output parsed by `_section_from_id` matches gold for the 97
  provisions; multi-primary questions (Q068/Q102/Q124/Q145) resolve instead of the
  current fetch misses.
- Gate: no regression in existing suites (350 passing baseline) + new fixture tests green.

## 5. Tests

- Unit: candidates (each grammar), features (noise cases), isolator golden tests using
  verbatim payload texts (fixtures in `tests/fixtures/provisions/`).
- Contract: emitted ids satisfy gold grammar + `_section_from_id` round-trip.
- Fallback: rules mode works with sklearn absent (monkeypatched import failure).
- `tests/test_provision_extractor.py`.

## 6. Execution order

1. `registry.py` + `ProvisionRecord` models + config flags.
2. Candidates + features + rules-mode disambiguator (works with zero ML) → isolator →
   unit fixtures. (Already fixes merged-heading/page-number defects.)
3. Evaluation script + baseline numbers (rules mode).
4. Training script + artifact + hybrid mode → re-run eval, keep only if it beats rules.
5. Pipeline adapter + backfill CLI (dry-run full-corpus diff) → gated live write.
6. KG `build_provisions` wiring.

KG wiring (step 6) stays **out of the first execution**. The effectiveness
evaluation treats a statute tree as a lookup aid, not the next measured
intervention. Wire `build_provisions` only after the dry-run diff shows
stable ids.

## 7. Risks

- Scanned/image PDFs unaffected (extraction operates on already-extracted text; OCR
  is a separate path).
- Unknown acts stay fail-closed (`ACT_SECTION_RANGES`); ranges extensible per manifest.
- Live Qdrant writes are the risky step → dry-run diff report first; reversible via
  existing re-stamp scripts.

## 8. Evaluation against the two research notes (2026-09-27)

Reviewed against `docs/RAG_EFFECTIVENESS_EVALUATION.md` (retrieval ceiling,
step-0 labels, F abstentions) and `docs/RAG_AUTORESEARCH_RESEARCH.md`
(corpus discovery vs extraction). Code check: universal multi-hop is already
live (`MULTIHOP_MAX_FOLLOWUPS`, type gate removed in `linear.py`). The
research note's "multi-hop only for cross_reference/case_law" gap is closed.
Do not spend this plan reopening it.

### What this plan is

A **boundary isolator over text that is already indexed**. It proposes
section/subsection spans on ordered payload chunks, stamps
`provision_spans`, and emits gold-grammar ids. That matches the
effectiveness note's allowed use of a section index: a lookup aid for
corpus completion and for gold resolution (`matches_gold`, multi-primary
Q068/Q102/Q124/Q145).

### What this plan is not

It does **not** acquire missing instruments. Experiment F's justified
abstentions name texts absent from the O3 payload (Water Act ss.25–29,
Rule 63, Order 12, WB Meat Order, KMC water-connection rules, PCA Rules
schedules). Re-segmenting 27,361 existing chunks cannot create those
spans. Binary-correctness targets in the autoresearch note (+5%, evidence-
missing 14/124 → <5) are **fill** outcomes, not extraction outcomes.

Autoresearch's "Provision Extraction Engine" (read source PDFs, NLP new
documents into the corpus) is a different component. This checkout has no
raw statute PDFs. Web scrape / PDF ingest stays a later phase and is not
part of steps 1–5.

### Revised success criteria

Keep the gates in §4 (boundary P/R, noise-stamp ≈ 0, gold id round-trip,
no suite regression). Drop any claim that a backfill alone moves benchmark
binary correctness. A backfill is successful when:

- emitted ids parse through `_section_from_id` and resolve where the text
  is already in the payload;
- bogus stamps on the strip-noise corpus do not return;
- `chunk_id`s are unchanged.

Report gold misses that are **absent text** separately from misses that are
**bad boundaries**. Only the second class is in scope here.

### Revised order relative to the effectiveness path

1. This plan, rules mode through dry-run (steps 1–3, then 5 dry-run).
   Hybrid ML only if it beats rules on the held-out boundary metrics.
2. Use the resulting id map as a lookup when labeling residuals
   (`evidence_missing` vs `model_wrong` vs `reference_narrow`). Do not
   start a generation budget for this work.
3. Corpus fill of instruments named as absent (separate ingestion work,
   human-approved, `step3_em_fill_approved.json`) — outside this plan.
4. KG `build_provisions` replacement after the dry-run is accepted.

### Environment note

The sklearn/torch inventory in §1 was taken on a Linux host
(`/home/suman_saha/.venv-nsa`). On this Windows checkout, confirm that
runner before training. Rules mode does not need it. Lazy sklearn import
and the rules fallback stay as specified.

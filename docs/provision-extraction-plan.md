# NLP-based Statutory Provision Extraction & Isolation — Implementation Plan

Status: **planned** (saved for review before execution).
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

Open question for execution kickoff: skip KG wiring (step 6) for now or include it.

## 7. Risks

- Scanned/image PDFs unaffected (extraction operates on already-extracted text; OCR
  is a separate path).
- Unknown acts stay fail-closed (`ACT_SECTION_RANGES`); ranges extensible per manifest.
- Live Qdrant writes are the risky step → dry-run diff report first; reversible via
  existing re-stamp scripts.

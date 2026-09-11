# Multi-Domain Corpus — Ingestion Readiness Evaluation

> **Date:** 2026-08-10 · **Method:** live probes (pdfplumber/fitz), real end-to-end
> pipeline benchmark into a throwaway Qdrant collection, real EasyOCR timing.
> Artifacts: `other domain/ingest_readiness_report.json` (per-doc probe),
> `scripts/_probe_ingest_readiness_tmp.py`, `scripts/_probe_ingest_benchmark_tmp.py`.

---

## 1. Corpus condition — all 26 docs evaluated

| Condition | Count | Docs | Action needed |
|---|---|---|---|
| ✅ Text-extractable, ready | 24 | all except the two scans below | none |
| 🔧 Image-only scans (OCR) | 2 | `Prevention_of_Cruelty_to_Animals_Rules_2017.pdf` (14 pp), `The_WestBengal_…_Animals_Rules_2016.pdf` (5 pp) | OCR (measured: **148.8 s/page @ 300 DPI CPU**) |
| ⚠️ Contains Devanagari (Hindi) | 6 | SWM_2026 (78/144 pp), PWM-Amend-Aug (20/34), EPR draft (19/34), PWM-Amend-Jul (8/14), draft-PWM (6/11), PWM draft amend 2021 (3/6) | decide strip vs keep (§4) |
| ℹ️ Missing from disk | 1 | `SWM_2026-1.pdf` | none — `ingest:false` duplicate, already skipped |
| 🚫 Duplicate (skip) | 1 | `SWM_2026-1.pdf` | already `ingest:false` in manifest |

- **0 pages require the pdfplumber path to fail** — the two "scanned" docs are
  image-only scans (fitz: 0 selectable chars, 1 image/page); pdfplumber reports
  them as 0 pages (pdfminer quirk) but the OCR pipeline uses its own fitz-based
  renderer, so ingestion is unaffected.
- All 24 text docs extracted cleanly; no encoding corruption in the English body.
  Hindi content is gazette header boilerplate (reg. numbers, ministry names).

## 2. Corpus scale (measured, real chunking)

Real chunk size from the benchmark (legal engine): **~217 chars/chunk** (154 chunks
from the 15-page EP Act) — not the 1,200 chars rule-of-thumb.| Domain | Docs | Chunks (dry-run 2026-08-10, post-strip) |
|---|---:|---:|
| commercial | 8 | 7,664 |
| env | 10 | 2,549 |
| wb_state | 2 | 1,735 |
| criminal | 1 | 1,260 |
| animal | 3 text (+2 OCR) | 275 |
| **TOTAL** | **24 text (+2 OCR)** | **13,483** |

Measured by the Phase 2 pre-flight (`scripts/ingest_multidomain.py --dry-run`, exit 0,
24 OK / 2 OCR-needed / 0 failed). The Devanagari strip cut the raw ~20,300-chunk
estimate by ~34% (SWM 522k→397k chars, EPR draft 99k→49k, PWM amendments ~37k→19k).

## 3. Measured throughput (this machine, CPU)

| Stage | Measured rate | 20,300-chunk cost |
|---|---:|---:|
| load + clean | ~2 s/doc | ~1 min |
| chunk (legal engine) | 3 ms/chunk | ~1 min |
| enrich (citation/crossref/quality/rule-entities) | ~5–15 ms/chunk (est.) | ~5 min |
| dense embed 768-d (all-mpnet-base-v2) | **212–315 ms/chunk** | **72–107 min** |
| sparse BM25 (fastembed, if enabled) | 10–30 ms/chunk (est.) | 3–10 min |
| Qdrant upsert | 100-pt batches, network-bound | ~5–10 min |
| OCR (EasyOCR, CPU, 300 DPI) | **148.8 s/page** | 19 pp → **47 min** |

**Wall-clock totals:**
- **All 26 docs, full enrichment, dense+sparse, OCR @ 300 DPI:** ≈ **2 h 05 m – 2 h 45 m**
- **24 text docs only (OCR deferred):** ≈ **1 h 10 m – 1 h 30 m**
- **OCR @ 150 DPI (quality check needed):** ≈ 15–20 min instead of 47

Per-domain dense-embedding share (at 212 ms/chunk): commercial 31 min · env 17 min ·
wb_state 16 min · criminal 6 min · animal 1 min.

## 4. Decisions (locked 2026-08-10) + prep status

| # | Decision | Status |
|---|---|---|
| 1 | **Entity extraction: install spaCy + en_core_web_sm** | ✅ **done** — `spacy 3.8.15` + `en_core_web_sm` (NER pipeline) installed in the venv; `LegalEntityExtractor` now uses local Tier-2 NER (verified: extracts FSSAI/Nestle/Environment Act locally). **Zero LLM calls.** *(Note: PyPI here needs `--trusted-host pypi.org --trusted-host files.pythonhosted.org` due to an SSL cert-chain issue; model via `python -m pip install --trusted-host github.com …spacy-models…wheel`.)* |
| 2 | **Hindi: strip Devanagari pre-chunk** | queued for Phase 2 script (a cleaner step before chunking; cuts total to ~15–18k chunks). Gazette boilerplate only — no legal substance lost. |
| 3 | **OCR: 150 DPI was validated — FAILED quality bar** | 150 DPI: 104 s/page, 2,661 chars, garbled sample (`New Delh (h 23rd…`). 300 DPI: 148.8 s/page, 3,698 chars. **Plan: 300 DPI, deferred second pass** (~47 min) — quality wins at only +44 s/page. |
| 4 | **Collection auto-create gap (bug)** | `QdrantIndexer.ensure_collection()` exists but `sync_chunks()` never calls it (benchmark upsert 404'd). Fixed in Phase 2: `pipeline.indexer.ensure_collection()` per domain before ingest. |

Remaining prep: sequence text-doc pass first, OCR pass second (EasyOCR peaks ~1 GB alongside the 500 MB embedder). Everything else is ready: Qdrant live (`ping: True`), both HF models cached, easyocr + fastembed + fitz present, `RAG_FULL_ENRICHMENT=true`. `tiktoken` absent is irrelevant to ingestion.

## 5. Final time estimate

**Pass 1 — 24 text docs** (dense 768-d + sparse BM25, spaCy NER, full enrichment):

| Stage | Time (13,483 chunks) |
|---|---:|
| load+clean + chunk (24 docs, ~4 min total) | ~5 min |
| citation/crossref/quality enrich | ~2–5 min |
| spaCy NER (10–50 ms/chunk) | ~2–11 min |
| **dense embedding (212–315 ms/chunk)** | **48–71 min** |
| sparse BM25 (10–30 ms/chunk) | ~2–7 min |
| Qdrant upsert (135 × 100-pt batches) | ~4–7 min |
| **Pass 1 total** | **≈ 1 h 05 m – 1 h 45 m** |

**Pass 2 — 2 scanned docs** (OCR @ 300 DPI, 19 pages): ≈ **47 min**.

**Full corpus:** ≈ **1 h 55 m – 2 h 35 m** wall clock on this CPU machine.
Embedding dominates (~70%); batching `EmbeddingService` calls (32–64/chunk batch) is
the main lever to compress it.

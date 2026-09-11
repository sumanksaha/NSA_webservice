# Rust Refactoring Evaluation — NSA Webservice

> **Purpose:** Evaluate the feasibility, targets, and integration strategy for
> refactoring performance-critical Python code paths in NSA Webservice to Rust.
>
> **Generated:** 2026-08-12
> **Scope:** Codebase analysis → high-impact targets → non-targets → integration
> blueprint (PyO3, FastAPI coexistence, C++ interop) → phased migration plan.

---

## 1. Executive Summary

NSA Webservice is a **government-grade legal workflow platform** (Flask +
SQLAlchemy + PostgreSQL, Python 3.12) whose performance profile is dominated by
**CPU-bound, regex-heavy text processing** across three tiers:

| Tier                                                                 | What it does                                            | Dominant cost                                       |
| -------------------------------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------- |
| **Ingestion** (RAG corpus builder, document cleaner)                 | Clean legal text → chunk → embed → index; OCR decisions | Regex parsing + sentence splitting + fuzzy matching |
| **Request serving** (search fallback, RAG retrieval, legal analysis) | Fuzzy search, entity extraction, verification           | rapidfuzz per-record scoring + Python orchestration |
| **Document generation** (PDF assembly, cross-reference, TOC)         | Post-processing HTML for WeasyPrint                     | Regex-based reference extraction + HTML rewriting   |

The single most important architectural observation is that **embedding
generation (sentence-transformers/PyTorch), OCR (EasyOCR/PaddleOCR/Tesseract),
PDF rendering (WeasyPrint/Pango), and database I/O (psycopg2/SQLite C drivers)**
are **already native C/C++/CUDA** — they are not pure-Python bottlenecks. Rust
wins are available in the **pure-Python regex orchestration layer** that wraps
and orchestrates these native engines.

**Verdict:** A **targeted, incremental PyO3 extension-module strategy** — not a
full rewrite — can yield **3×–10× speedups** on the text-processing hot paths
with minimal integration risk. The existing 1,757-test suite and Render CI/CD
pipeline are preserved; Rust modules are imported like any Python module.

---

## 2. Performance-Critical Python Hot Paths (High-Impact Rust Targets)

### 2.1 Legal Paragraph Detection Engine — `#1 Target`

**Files:** `legal_paragraph_detection_engine/src/` (~5,049 LOC across
`core/paragraph.py`, `core/hierarchy.py`, `parsers/clause_parser.py`,
`parsers/section_parser.py`, `parsers/legal_document.py`,
`storage/citation.py`, `utils/text_cleaner.py`)

**Why it's the #1 target:**

- **Pure Python regex** — no C extensions, no native code. Every pattern match
  is interpreted by CPython's re module.
- **Per-line iteration** with dozens of compiled regex patterns, each tested
  against every line of every document.
- **Multi-stage pipeline**: `clean_text` → `parse_sections` →
  `parse_clauses` → `extract_citations` → `detect_paragraph_boundaries` →
  `detect_hierarchy` → `_build_hierarchical_structure` → confidence scoring.
  Each stage re-iterates the document.
- **Called on every ingestion** (27,343 chunks × multiple passes) AND on every
  `LegalParagraphEngine.process_document()` request (used by the chunking
  pipeline, enrichment, and the legal-analysis workbench).
- `TextNormalizer.clean_text()` applies **6+ separate regex substitution passes**
  per document; `normalize_hyphens()` in `document_cleaner/normalizers.py` runs
  rapidfuzz fuzzy matching inside a per-word callback loop.

**Rust opportunity:** The entire engine's pattern-matching, text normalization,
hierarchy detection, clause parsing, and citation extraction are
**embarrassingly parallelizable** regex + string operations. A Rust port using
the `regex` crate (which is a full NFA/DFA engine with SIMD acceleration) would
be **5×–10× faster** on typical legal documents. The `hierarchy.rs` ancestor-stack
tree-building logic maps directly to Rust data structures.

**Estimated effort:** 4–6 weeks (1 engineer)
**Estimated gain:** ~7× throughput on legal text parsing
**Risk:** Low — deterministic logic, no external dependencies

### 2.2 Document Cleaner — `#2 Target`

**Files:** `app/document_cleaner/removers.py` (440 LOC),
`app/document_cleaner/normalizers.py` (320 LOC),
`app/document_cleaner/pipeline.py` (170 LOC)

**Why it's the #2 target:**

- **Iterative line-by-line processing** with regex classification
  (`_classify_line_type` checks 8+ pattern sets per line).
- **`_should_preserve()`** runs 12 regex searches on every line that passes
  through `remove_page_numbers`, `remove_watermark_text`,
  `remove_duplicate_lines`, `remove_headers_footers`, `remove_running_titles`
  — each a separate pass over all lines.
- **`normalize_hyphens()`** uses rapidfuzz inside a regex substitution
  callback — Python callback overhead per match, plus the rapidfuzz call itself.
- Runs on **every document** in the corpus pipeline (12,819 FSSAI chunks + 14,524
  other-domain chunks = 27,343 documents).

**Rust opportunity:** Consolidate all cleaning passes into a **single-pass**
Rust processor. The character-set-based OCR-artifact remover
(`_OCR_GARBAGE`) is a perfect fit for Rust's `char` iteration — no regex needed
for the set-membership check. The hyphen-rejoin logic with fuzzy validation
could use Rust's native string handling.

**Estimated effort:** 2–3 weeks
**Estimated gain:** ~5× throughput on document cleaning
**Risk:** Low — well-tested pipeline, 45 tests

### 2.3 RAG Enrichment (Deterministic) — `#3 Target`

**Files:** `app/rag/enrichment/deterministic.py` (621 LOC),
`app/rag/entity_extractor.py` (466 LOC),
`app/rag/citation_adapter.py` (131 LOC),
`app/rag/crossref_adapter.py` (170 LOC),
`app/rag/metadata_adapter.py` (254 LOC),
`app/rag/document_classifier.py` (219 LOC)

**Why it's a strong target:**

- Runs on **every chunk** during ingestion (27,343 chunks × 5 enrichment passes).
- `deterministic.py`: header-section regex + reference-section regex +
  schedule/annexure/headword extraction + keyword extraction with stopword
  filtering — all pure Python regex.
- `entity_extractor.py`: 4 regex pattern tuples (person, organization, case,
  statute) iterated over the full document text; `_rule_based()` iterates
  pattern × match × entity construction.
- `citation_adapter.py` / `crossref_adapter.py`: per-chunk regex extraction
  of citations and cross-references.

**Rust opportunity:** Batch-process all chunks for a document in a single Rust
call, returning structured JSON. The regex-heavy extraction is easily ported
to the `regex` crate. This eliminates the per-chunk Python function call overhead.

**Estimated effort:** 3–4 weeks
**Estimated gain:** ~4× throughput on enrichment
**Risk:** Medium — enrichment logic is complex; must preserve the exact same
output for the 255 enrichment tests

### 2.4 RAG Verification (Hallucination Detection) — `#4 Target`

**Files:** `app/rag/verification/claim_extractor.py` (187 LOC),
`app/rag/verification/evidence_verifier.py` (187 LOC),
`app/rag/verification/hallucination_detector.py` (272 LOC),
`app/rag/verification/citation_validator.py` (125 LOC),
`app/rag/verification/scorer.py` (132 LOC),
`app/rag/verification/token_counter.py` (165 LOC)

**Why it's a good target:**

- `claim_extractor.py`: sentence splitting via regex + 5 entity-extraction
  regexes per sentence. Pure Python.
- `evidence_verifier.py`: calls `fuzz.token_set_ratio` + `fuzz.partial_ratio`
  per (claim, chunk) pair. When there are 10 claims × 20 chunks = 200 fuzzy
  comparisons per query, the Python orchestration dominates.
- `hallucination_detector.py`: orchestrates claim extraction → verification
  → citation validation → scorer → optional LLM check. The non-LLM path is
  pure Python.

**Rust opportunity:** The evidence verifier's per-pair scoring loop is
embarrassingly parallel. Rust + `rayon` (thread pool) can run the 200
comparisons across all cores, and `regex` replaces the Python re module.
The `token_counter.py` (tiktoken wrapper) could use a native Rust tokenizer
(`llama-cpp-rs` or a custom BPE implementation).

**Estimated effort:** 2–3 weeks
**Estimated gain:** ~8× on verification (parallel + native regex)
**Risk:** Medium — must match rapidfuzz's exact scoring algorithm

### 2.5 Search Fuzzy Fallback — `#5 Target`

**File:** `app/search/indexer.py` (775 LOC), specifically `fuzzy_search_fallback()`,
`_field_score()`, `_find_match_spans()`, `_snippet_around_matches()`

**Why it's a target:**

- `fuzzy_search_fallback()` iterates **every record** in the database
  (CaseFile + Adjudication + Annexure + Evidence), calling
  `fuzz.token_set_ratio` + `fuzz.partial_ratio` per field.
- `_find_match_spans()` does per-word fuzzy matching with rapidfuzz,
  iterating all matches in all candidate texts.
- With thousands of records, this is O(n × m × rapidfuzz_calls) with Python
  loop overhead.

**Caveat:** rapidfuzz is already a C++ extension with SIMD. The Python
**orchestration** (looping, dict construction, sorting) is the bottleneck,
not the fuzzy computation itself. Rust can eliminate the per-iteration Python
bytecode dispatch and the dict/list allocation overhead by doing the full
fuzzy scoring + sorting in a single native loop.

**Rust opportunity:** Port the entire `fuzzy_search_fallback` + helper
functions to a single Rust function that takes the query and a list of
(record_id, title, content) tuples, returns scored + sorted results. Use
the `regex` crate for pattern matching and a Rust fuzzy-matching library
(`fuzzy-matcher` or `simsim`) instead of rapidfuzz.

**Estimated effort:** 1–2 weeks
**Estimated gain:** ~3× on fuzzy search (parallel + no Python overhead)
**Risk:** Low — well-isolated function, 56 search tests

### 2.6 Cross-Reference Engine + TOC Generator — `#6 Target`

**Files:** `app/cross_reference/engine.py` (495 LOC),
`app/toc_generator/engine.py` (293 LOC)

**Why:** Both use regex-based HTML rewriting, reference extraction, and
list renumbering. The TOC generator parses heading tags and builds
numbered hierarchies. Both run on every PDF generation request.

**Rust opportunity:** HTML string manipulation + regex extraction in a single
pass. Moderate gain (PDF generation is I/O-bound by WeasyPrint anyway, but
the HTML pre-processing is pure CPU).

**Estimated effort:** 1–2 weeks
**Estimated gain:** ~2×–3× on pre-processing (but bounded by WeasyPrint I/O)
**Risk:** Low

### 2.7 OCR Decision Engine & Metadata Extraction — `#7 Target`

**Files:** `app/ocr_pipeline/decision.py` (OCRDecisionEngine, ~100 LOC),
`app/ocr_pipeline/models.py`,
`app/metadata_extractor/regex_library.py` (478 LOC, regex-heavy)

**Why:** `OCRDecisionEngine.evaluate()` parses PDF text blocks with PyMuPDF
but the per-character counting + regex classification is Python. The
`regex_library.py` has 478 LOC of regex patterns for field extraction.

**Caveat:** The actual OCR inference (EasyOCR/PaddleOCR/Tesseract) is the
dominant cost (seconds per page) — the Python decision logic is microseconds
by comparison. **Low ROI** unless OCR is run at massive scale.

**Rust opportunity:** Only the decision engine + regex metadata extraction
would benefit, not the OCR inference itself.

**Estimated effort:** 1 week
**Estimated gain:** <5% end-to-end (OCR is GPU/CPU inference-bound)
**Risk:** Low

---

## 3. Where Rust Refactoring Would NOT Yield Gains

### 3.1 Embedding Generation — Already Native

**File:** `app/rag/embedding_service.py`

- Uses `sentence-transformers` → PyTorch C++ backend.
- **Rust cannot help** — reimplementing transformer inference in Rust would
  require the `burn` or `tch-rs` ecosystem, a massive effort with no
  throughput advantage over optimized PyTorch.
- **Even if replaced**, the bottleneck is the neural network compute, not
  the Python wrapper. `encoder.encode()` releases the GIL during torch
  inference.

### 3.2 OCR Inference Engines — Already Native

**Files:** `app/ocr_pipeline/ocr_engine.py`, `app/ocr_pipeline/preprocessing.py`

- **EasyOCR** = PyTorch (C++ backend). **PaddleOCR** = PaddlePaddle (C++).
  **Tesseract** = native C++.
- **OpenCV preprocessing** = native C++.
- The Python orchestration overhead is negligible compared to seconds-per-page
  OCR inference time.
- **No Rust win.**

### 3.3 PDF Generation — Already Native

**File:** `app/pdf_assembly/engine.py`

- **WeasyPrint** = Pango/Cairo (C libraries). **PyMuPDF (fitz)** = native C.
  **PyPDF2** = native C.
- PDF rendering is GPU/memory/IO-bound, not Python-bound.
- **No Rust win** — reimplementing PDF rendering from scratch in Rust
  (e.g., with `printpdf` or `genpdf`) would be a massive effort with worse
  output quality.

### 3.4 Database I/O — Already Native

- **PostgreSQL** via `psycopg2` (C extension). **SQLite** via `sqlite3`
  (C standard library).
- Python wraps C calls; the I/O is already native.
- SQLAlchemy ORM overhead is the Python cost, but replacing with a Rust ORM
  (e.g., `SQLx`) would require a complete migration and lose all the
  SQLAlchemy ecosystem (Flask-SQLAlchemy, Flask-Migrate/Alembic).
- **No Rust win** for query-heavy workloads. Use connection pooling + eager
  loading (already done — Perf Quick Win #5/#7).

### 3.5 HTTP I/O & Web Framework — I/O Bound

- **Flask/Gunicorn** request serving is I/O-bound (waiting for DB, Redis,
  HTTP calls). Python's I/O model (threading, async) is adequate.
- **Jinja2** templates — already use C-speedups via MarkupSafe.
- **Celery** task queue — Redis/network I/O bound.
- **QStash** webhooks — HTTP I/O bound.
- **No Rust win** for web serving latency. Use Nginx/gunicorn tuning
  instead.

### 3.6 RapidFuzz — Already a Native C++ Extension

- Used in `app/search/indexer.py`, `app/rag/retrieval/sparse_retriever.py`,
  `app/rag/verification/evidence_verifier.py`, `app/rag/generation/reranker.py`.
- Rapidfuzz is a C++11 extension with SIMD acceleration that **releases the
  GIL** during computation.
- The **Python loop overhead** is the bottleneck, not the fuzzy computation.
- Rust can replace the **orchestration** (the loop + sorting + dict building)
  but the matching itself should reuse rapidfuzz via PyO3 or use a Rust
  fuzzy library. Replacing rapidfuzz with Rust fuzzy libs is possible but
  offers marginal gains over the orchestration win alone.

### 3.7 NumPy in OCR Pipeline — Already Native

- `app/ocr_pipeline/preprocessing.py` uses OpenCV via NumPy arrays.
- OpenCV is C++; NumPy operations are C/Fortran.
- **No Rust win.**

---

## 4. Integration Strategy: Blending Rust with FastAPI, C++, and Python

### 4.1 PyO3 Extension Modules (Recommended Primary Path)

**How it works:**

- Write Rust code using the [`PyO3`](https://pyo.rs) crate, which provides
  safe FFI bindings to CPython.
- Compile with [`maturin`](https://www.maturin.rs) (or `setuptools-rng`) to
  produce a native `.so`/`.pyd`/`.dylib` that is **importable from Python**
  as a regular module.
- The resulting module behaves exactly like a Python C extension.

**Example structure:**

```text
nsa_webservice/
├── pyproject.toml              # add [tool.maturin] section
├── rust/
│   ├── Cargo.toml
│   ├── src/
│   │   ├── lib.rs             # PyO3 entry point (#[pymodule])
│   │   ├── legal_engine.rs    # Paragraph detection + hierarchy + citation extraction
│   │   ├── document_cleaner.rs # Multi-pass text cleaning → single-pass
│   │   ├── enrichment.rs      # Deterministic entity/citation/metadata extraction
│   │   ├── verification.rs    # Claim extraction + evidence verification
│   │   └── search_fuzzy.rs    # Fuzzy search fallback (batch scoring)
│   └── tests/                 # Rust unit tests (cargo test)
```

**Python integration point** (transparent to callers):

```python
# Before (pure Python):
from legal_paragraph_detection_engine import LegalParagraphEngine
engine = LegalParagraphEngine()
paragraphs = engine.process_document(text)

# After (Rust-accelerated, same API):
try:
    from nsa_rust.legal_engine import process_document_rust  # PyO3 module
    paragraphs = process_document_rust(text)  # 7x faster
except ImportError:
    # Fallback: pure Python path (same as before)
    from legal_paragraph_detection_engine import LegalParagraphEngine
    paragraphs = LegalParagraphEngine().process_document(text)
```

**Key advantages:**

- **Zero API change** — Python callers see the same function signatures.
- **Works with Flask, Celery, CLI scripts, and the test suite.**
- **Falls back to Python** if the Rust extension isn't compiled (graceful
  degradation, matching the project's existing pattern).
- **1,757 existing tests** need zero changes — only the implementations
  underneath swap out.

**Deployment on Render:**
Render's Python buildpack supports `cargo` if a `Cargo.toml` is present at
the project root. Alternatively, pre-build wheels (manylinux for Linux,
macOS wheels for dev) and publish as GitHub Release artifacts, then install
via `pip install` from the wheel URL.

**pyproject.toml changes:**

```toml
[build-system]
# Add maturin as a build dependency for the Rust extension
requires = ["setuptools>=70,<84", "wheel", "maturin>=1.0,<2.0"]
build-backend = "maturin"

[tool.maturin]
modules = ["nsa_rust"]
python-source = "rust/src"
```

### 4.2 FastAPI Coexistence

**Strategy:** Run a **FastAPI ASGI service** alongside the existing Flask
application, serving the same Rust extension modules. This is useful if the
project later wants to add async endpoints (e.g., streaming RAG responses).

**Architecture:**

```text
                    ┌──────────────────────┐
  Browser ─────────►│  Nginx (or Render)   │
                    └──────┬───────┬───────┘
                           │       │
              /api/v1/    │       │  /rag/
                  (Flask) │       │  (FastAPI)
              ┌────────────┐   ┌───────────────┐
              │  Flask     │   │  FastAPI      │
              │  (sync)    │   │  (async)      │
              └─────┬──────┘   └───────┬───────┘
                    │                  │
                    ▼                  ▼
              ┌─────────────────────────────────┐
              │   Rust PyO3 Modules (shared)    │
              │  - legal_engine.so              │
              │  - document_cleaner.so          │
              │  - enrichment.so                │
              │  - verification.so              │
              │  - search_fuzzy.so              │
              └───────────────┬─────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  C++ / Native      │
                    │  - PyTorch (embed)│
                    │  - WeasyPrint (PDF)│
                    │  - OpenCV (OCR)   │
                    │  - rapidfuzz      │
                    └───────────────────┘
```

**How Flask and FastAPI share Rust modules:**
Because PyO3 modules are **regular Python imports**, both Flask and FastAPI
processes import the same compiled `.so`/`.pyd` file from the Python
`site-packages` or project directory. No IPC, no FFI layer — direct in-process
calls.

**FastAPI service example** (`app/rag/fastapi_routes.py` — optional, not
required for the Rust migration):

```python
from fastapi import FastAPI
from nsa_rust.verification import verify_claims_rust  # shared Rust module

app = FastAPI(title="RAG Verification API")

@app.post("/verify")
async def verify(payload: VerifyRequest):
    # Rust extension runs in a thread pool (async-safe)
    report = await asyncio.to_thread(
        verify_claims_rust,
        payload.response,
        payload.chunks
    )
    return report
```

**Routing:** Flask handles the existing blueprint routes (`/rag/`, `/search/`,
etc.); FastAPI can be mounted at `/async/` or `/api/rag/` via ASGI middleware
(`flask-asgi` bridge or a reverse proxy path split). Or FastAPI can replace
Flask entirely later (Phase 0 decision says "keep Flask," so this is
optional).

### 4.3 C++ Interoperability

Rust can interoperate with the existing C/C++ stack in two directions:

**Rust → C++ (Rust calls C++):**

- The existing OCR/preprocessing code uses OpenCV (C++). Rust can call OpenCV
  via the `opencv` crate or via `extern "C"` FFI.
- The existing rapidfuzz (C++) can be called from Rust via the
  `rapidfuzz-cpp` C API or by linking the C++ library.

**C++ → Rust (C++ calls Rust):**

- If a Rust module is compiled as a `cdylib` (dynamic library with `extern "C"`
  ABI), C++ code can `dlopen`/`LoadLibrary` it and call the exported C functions.
- Use case: a C++ OCR pipeline that delegates text post-processing to a Rust
  shared library.

**Example `extern "C"` ABI for C++ consumers:**

```rust
// rust/src/lib.rs — compiled as cdylib
#[no_mangle]
pub extern "C" fn legal_engine_process(
    text: *const c_char,
    out_json: *mut *mut c_char,
) -> i32 {
    let c_str = unsafe { CStr::from_ptr(text) };
    let text = c_str.to_str().unwrap();
    let result = process_document(text);  // Rust logic
    let json = serde_json::to_string(&result).unwrap();
    let json_c = CString::new(json).unwrap();
    unsafe { *out_json = json_c.into_raw() };
    0  // success
}
```

**C++ consumer:**

```cpp
// Loads the Rust .so/.dll and calls legal_engine_process()
extern "C" {
    int legal_engine_process(const char*, char**);
}
```

This allows gradual migration: the C++ OCR engine can call Rust for text
cleaning while keeping the PyTorch inference path in C++.

### 4.4 Other Language Blending

| Language               | Integration                        | Use Case                                             |
| ---------------------- | ---------------------------------- | ---------------------------------------------------- |
| **C (via libc)**       | Rust links against system C libs   | Regex engine, hashlib                                |
| **CUDA**               | Rust GPU crates (`burn`, `tch-rs`) | If embedding generation is ever moved to Rust        |
| **WebAssembly (WASM)** | `wasm32` target                    | Browser-side legal text processing in the editor     |
| **Go**                 | HTTP/gRPC bridge                   | If a Go microservice is needed (e.g., for Cloud Run) |

---

## 5. Phased Migration Plan

### Phase 1: Legal Paragraph Detection Engine (8 weeks)

**Goal:** Replace `legal_paragraph_detection_engine` with a Rust crate
(`nsa_rust::legal_engine`) exposing the same Python API.

**Steps:**

1. Create `rust/` workspace with `Cargo.toml` + `[tool.maturin]` in
   `pyproject.toml`.
2. Port `TextNormalizer` → `nsa_rust::normalize::TextNormalizer`
   (regex crate, single-pass where possible).
3. Port `ParagraphBoundaryDetector` + `HierarchyDetector` →
   `nsa_rust::structure::{ParagraphBoundaryDetector, HierarchyDetector}`.
4. Port `ClauseParser`, `SectionParser`, `CitationExtractor`,
   `DocumentTypeClassifier` → corresponding Rust modules.
5. Port `LegalParagraphEngine::process_document` →
   `nsa_rust::legal_engine::process_document`.
6. Create PyO3 wrapper exposing `process_document(text: str) -> list[dict]`.
7. Update Python wrapper (`app/services/legal_engine.py`) to try the Rust
   import first, fall back to pure Python.
8. **Test:** Run existing 282 RAG tests + `legal_paragraph_detection_engine/tests/`
   (all tests should pass unchanged with identical output).

**Success metric:** `scripts/benchmark_rag.py` shows ≥5× chunking throughput;
100% test parity.

### Phase 2: Document Cleaner + Search Fuzzy (3 weeks)

**Goal:** Replace document cleaning and search fuzzy fallback with Rust.

**Steps:**

1. Port `app/document_cleaner/` → `nsa_rust::cleaner`.
2. Port `fuzzy_search_fallback()` + helpers → `nsa_rust::search_fuzzy`.
3. Update `DocumentCleaner` and `FTS5Indexer.search` to use Rust with
   Python fallback.
4. Run `test_document_cleaner.py` (45 tests), `test_search.py` (56 tests).

**Success metric:** ≥3× cleaning throughput; ≥2× fuzzy search throughput;
zero test regressions.

### Phase 3: RAG Enrichment + Verification (5 weeks)

**Goal:** Move enrichment and hallucination detection to Rust.

**Steps:**

1. Port `deterministic.py`, `entity_extractor.py`, `citation_adapter.py`,
   `crossref_adapter.py`, `metadata_adapter.py` → `nsa_rust::enrichment`.
2. Port `claim_extractor.py`, `evidence_verifier.py`, `hallucination_detector.py`
   → `nsa_rust::verification`.
3. Port `verification/token_counter.py` (tiktoken wrapper) → Rust tokenizer
   or `llama-cpp-rs`.
4. Update `IngestionPipeline` and `GroundedGenerationService` to use Rust.
5. Run all 255 enrichment + 48 verification tests.

**Success metric:** ≥3× enrichment throughput; ≥5× verification throughput
(parallel via rayon); zero test regressions.

### Phase 4: Cross-Reference + TOC + OCR Decision (3 weeks)

**Goal:** Accelerate HTML post-processing and OCR decision logic.

**Steps:**

1. Port `app/cross_reference/engine.py` → `nsa_rust::cross_reference`.
2. Port `app/toc_generator/engine.py` → `nsa_rust::toc`.
3. Port `app/ocr_pipeline/decision.py` + `metadata_extractor/regex_library.py`
   → `nsa_rust::ocr_decision`.
4. Update `PDFAssemblyEngine.post_process()` and OCR pipeline to use Rust.
5. Run `test_cross_reference.py` (27 tests), `test_phase7_toc_generator.py`
   (37 tests), `test_phase8_pdf_assembly.py` (40 tests).

---

## 6. Non-Targets (Explicitly Excluded)

The following are **NOT recommended** for Rust refactoring — they are either
already native or offer negligible gains:

| Module                                  | Reason                                                        | Current Tests |
| --------------------------------------- | ------------------------------------------------------------- | ------------- |
| `app/rag/embedding_service.py`          | PyTorch C++ backend; Rust ML = massive effort, no gain        | 17            |
| `app/ocr_pipeline/ocr_engine.py`        | EasyOCR (PyTorch), PaddleOCR (PaddlePaddle), Tesseract (C++)  | 24 + 14       |
| `app/ocr_pipeline/preprocessing.py`     | OpenCV (C++); OCR inference dominates                         | —             |
| `app/pdf_assembly/engine.py`            | WeasyPrint (Pango/Cairo C); rendering-bound, not Python-bound | 40            |
| `app/extensions.py` / `app/__init__.py` | Flask wiring; I/O-bound framework setup                       | —             |
| `app/rag/qdrant_client.py`              | Remote API calls; network-bound                               | 25            |
| `app/rag/retrieval/dense_retriever.py`  | Qdrant remote search + torch embed; network + GPU bound       | 15            |
| `celery_app.py` / `app/celery*`         | Task queue; Redis I/O-bound                                   | —             |
| `app/ai_assistant/service.py`           | HTTP client to LLM APIs; I/O-bound                            | 23            |
| `app/rag/generation/llm_client.py`      | HTTP client to OpenRouter/openai-compatible API; I/O-bound    | —             |
| `app/rag/routes.py`                     | HTTP request handlers; Flask/I/O-bound                        | 6             |
| `app/search/routes.py`                  | HTTP routes; Flask/I/O-bound                                  | —             |

---

## 7. Risk Analysis & Mitigations

### 7.1 Regex Parity Risk

**Risk:** Rust's `regex` crate semantics may differ from Python's `re` module
in edge cases (e.g., lookbehind assertions, Unicode handling, `\b` word
boundaries).

**Mitigation:**

- Rust's `regex` crate does **not** support lookbehind — use lookahead-based
  alternatives or `fancy-regex` crate where lookbehind is needed.
- Port patterns incrementally, running each Rust function against the
  existing Python test corpus to verify identical output.
- Keep the Python fallback path until Rust parity is proven.

### 7.2 Deployment/Platform Risk

**Risk:** Render's build environment may not have Rust toolchain (`cargo`, `rustc`).

**Mitigation:**

- `maturin` can build Rust extensions in any environment with `cargo` installed.
- Render's Python buildpack now supports Rust extensions if a `Cargo.toml`
  is present (as of 2025).
- **Fallback:** Pre-build manylinux wheels and publish to a GitHub Release;
  `pip install` from the wheel URL in `requirements.txt`.
- **Worst case:** The Python fallback path handles all cases when Rust isn't
  available (the project's graceful-degradation pattern).

### 7.3 Concurrency Safety Risk

**Risk:** PyO3 requires `gil` management; Python objects passed to Rust
must respect reference counting.

**Mitigation:**

- PyO3 handles GIL automatically (`#[pyo3(...)]`-decorated functions acquire
  the GIL on entry).
- For CPU-heavy work, use `pyo3::ffi::Python_begin_allow_threads` to release
  the GIL during computation, then re-acquire when returning Python objects.
- For batch operations (e.g., embedding 27k chunks), release the GIL and
  process in a Rust thread pool (`rayon`).

### 7.4 Build-Time Complexity Risk

**Risk:** Adding `maturin` + Rust toolchain to the build pipeline increases
CI build time and complexity.

**Mitigation:**

- Use `maturin develop` for local dev (fast incremental rebuilds).
- Use `maturin build --release` for CI/deploy wheels.
- Cache the Rust toolchain and cargo registry in GitHub Actions.
- Make Rust an **optional** build — the Python path always works.

---

## 8. Quick Wins (Can Be Done in 1–2 Days Each)

These are **low-effort, high-value** ports that can be done one at a time:

1. **`app/rag/dedup.py`** — `ContentHasher.compute()` uses Python `hashlib`
   (already C, so no gain) — **skip**.

2. **`app/rag/chunker.py`** — The `Chunk.from_paragraph()` constructor +
   `_extract_section_title`/`_extract_subsection_markers` regex — the
   `chunk_text()` method calls the engine once, so gains are marginal if the
   engine is Rust. **Defer** until Phase 1.

3. **`app/rag/verification/token_counter.py`** — Replace `tiktoken` (Python)
   with a Rust BPE tokenizer. **Moderate win** (token counting is called
   on every LLM prompt + response). 1 day effort.

4. **`app/document_cleaner/normalizers.py::normalize_hyphens`** — The
   rapidfuzz `fuzz.ratio` callback per word is the bottleneck. A Rust
   port using a simple string-similarity metric (Levenshtein) would be
   ~10× faster. 1–2 day effort.

5. **`app/shared/case_keys.py`** — 487 LOC of field-name constants.
   **No computation** — pure data. **Do not Rust-ify.**

---

## 9. Benchmark Harness

Use the existing `scripts/benchmark_rag.py` (already in the repo) as the
baseline, and extend it for Rust comparison:

```python
# Before (Python):
measure_chunking(text, chunker=Chunker())  # → chunks/s, latency

# After (Rust):
from nsa_rust.legal_engine import process_document
measure_chunking_rust(text)  # → same metrics, 7x higher chunks/s
```

**Key metrics to track:**

- Chunking throughput (chars/sec, chunks/sec)
- Embedding throughput (vectors/sec)
- Fuzzy search latency (ms per query)
- Verification latency (ms per claim)
- Document cleaning throughput (docs/sec)
- Total ingestion pipeline latency (file → Qdrant)

---

## 10. Summary Table

| Module                 | LOC    | Rust ROI       | Effort  | Gain | Risk |
| ---------------------- | ------ | -------------- | ------- | ---- | ---- |
| Legal Paragraph Engine | ~5,050 | **⭐⭐⭐⭐⭐** | 4–6 wks | 7×   | Low  |
| Document Cleaner       | ~1,000 | **⭐⭐⭐⭐**   | 2–3 wks | 5×   | Low  |
| RAG Enrichment         | ~1,600 | **⭐⭐⭐⭐**   | 3–4 wks | 4×   | Med  |
| RAG Verification       | ~1,200 | **⭐⭐⭐⭐**   | 2–3 wks | 5–8× | Med  |
| Search Fuzzy           | ~775   | **⭐⭐⭐**     | 1–2 wks | 3×   | Low  |
| Cross-Reference        | ~495   | **⭐⭐**       | 1–2 wks | 2–3× | Low  |
| TOC Generator          | ~293   | **⭐⭐**       | 1 wk    | 2–3× | Low  |
| OCR Decision           | ~100   | **⭐**         | 1 wk    | <5%  | Low  |
| Embedding Service      | 187    | ❌             | N/A     | N/A  | N/A  |
| OCR Pipeline           | ~1,000 | ❌             | N/A     | N/A  | N/A  |
| PDF Assembly           | ~1,041 | ❌             | N/A     | N/A  | N/A  |
| Qdrant Client          | ~830   | ❌             | N/A     | N/A  | N/A  |
| Dense Retriever        | 260    | ❌             | N/A     | N/A  | N/A  |
| Flask / Celery         | ~1,200 | ❌             | N/A     | N/A  | N/A  |

**Total Rust-eligible code: ~10,000 LOC of regex/compute-heavy Python.**
**Total non-eligible code: ~5,500 LOC of native-wrapped / I/O-bound code.**

---

## 11. Recommendation

**Do the Rust migration — but target it surgically, in phases, with Python
fallbacks always intact.**

The 4-week Phase 1 target (Legal Paragraph Detection Engine) delivers the
largest single win because it sits at the **base of the ingestion funnel** —
27,343 chunks × 7 pipeline stages × Python regex overhead. A 7× speedup there
cascades through every downstream stage (cleaning → embedding → enrichment →
verification).

The **PyO3 + maturin** approach is the right integration strategy: it
produces a Python-importable module that both Flask and FastAPI can share,
falls back to pure Python when the Rust extension is absent, and preserves
the 1,757-test suite. C++ interop is available via `extern "C"` for any
future need to bridge Rust logic into the existing OpenCV/PyTorch stack.

> **Skipped:** Full rewrite of the Flask web framework, embedding generation,
> OCR inference, and PDF rendering to Rust — these are already native C/C++
> under the Python wrappers and offer zero ROI. Add Rust only for pure-Python
> regex/compute paths.

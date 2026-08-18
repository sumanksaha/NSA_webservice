import sys


def main():
    path = 'task.md'
    with open(path, encoding='utf-8') as f:
        content = f.read()

    start_marker = "### Part 2"
    end_marker = "## Completed Milestones"
    start_idx = content.index(start_marker)
    end_idx = content.index(end_marker)

    new_section = """### Part 1 (continued) - Compilation Fix (prerequisite for maturin build)

**Status:** Code ported (Steps 1.3-1.5). Build blocked on Windows 10 SDK.
**New blocker found (2026-08-12):** `rust/src/lib.rs` declares
`mod legal_engine;` and exposes 3 `#[pyfunction]` wrappers (`detect_paragraphs`,
`extract_citations`, `classify_document`) that call `legal_engine::detect_paragraphs()`,
`legal_engine::extract_citations()`, `legal_engine::classify_document()` -- **but
`rust/src/legal_engine.rs` has 0 `pub fn` implementations** (83 lines, only struct
definitions: `DetectionConfig`, `LegalCitation`, `ParagraphInfo`). The crate will
NOT compile even if the Windows 10 SDK is installed.

**Fix (do before Part 1 build):** Remove the 3 broken `# [pyfunction]` stubs +
`mod legal_engine;` from `lib.rs` (they belong to Part 5, not Part 1). Keep
`rust/src/legal_engine.rs` on disk -- it holds the struct definitions Part 5 will
use. This makes the crate compile for Part 1's maturin build.

- **lib.rs lines to remove:** `mod legal_engine;` (L27) + the 3 `# [pyfunction]`
  blocks for `detect_paragraphs`, `extract_citations`, `classify_document`
  (L96-L118) + their `m.add_function(wrap_pyfunction!(...))` lines (L145-L147).
- **After fix:** crate compiles with only `normalizers` + `removers` (Part 1 scope).
  Part 5 will re-add `mod legal_engine;` + implementations when ready.

---

### Part 2 - Search Fuzzy Helpers Port (`nsa_rust::search_fuzzy`)

- **Goal:** Accelerate the pure helper functions behind fuzzy search without
  touching the DB/ORM-coupled `fuzzy_search_fallback()` itself.
- **Targets** (`app/search/indexer.py`):
  | Function | Lines | Responsibility |
  |---|---|---|
  | `_field_score(query, text)` | L364-L377 | `fuzz.token_set_ratio` + `fuzz.partial_ratio` blended scoring |
  | `_expand_to_word(text, start, end)` | L379-L386 | Expand a match span to word boundaries |
  | `_find_match_spans(query, text, fuzzy_word_threshold=60.0)` | L388-L444 | Per-word fuzzy matching -> span list |
  | `_apply_marks(text, spans)` | L446-L456 | Inject `<mark>` HTML around spans |
  | `_snippet_around_matches(query, text, width=80, fuzzy_word_threshold=60.0)` | L458-L531 | Build truncated snippet with marks |
  | `fuzzy_search_fallback()` | L533-end | **Stays in Python** (DB-coupled) |
- **Steps:**
  1. Create `rust/src/search_fuzzy.rs` with PyO3 wrappers: `field_score(query, text) -> float`, `find_match_spans(query, text) -> list[tuple[int,int]]`, `apply_marks(text, spans) -> str`, `snippet_around_matches(query, text, width, threshold) -> str`.
  2. Add `# [pyfunction]` exports + `m.add_function(...)` calls in `lib.rs`.
  3. Add `_rust` hooks in a new `app/search/rust_fuzzy.py` module (mirror the `_rust_normalize` pattern in `pipeline.py`): lazy-import `nsa_rust`, return `None` on `ImportError`.
  4. Wire into `_field_score`, `_find_match_spans`, `_apply_marks`, `_snippet_around_matches` -- try Rust first, fall back to Python helpers.
  5. Build with `maturin develop --manifest-path rust/Cargo.toml`.
  6. Run `tests/test_search.py` (56 tests, `TestFuzzySearch` = 19, `TestSearchAPI` = 9).
- **Parity risk:** `_field_score` must reproduce rapidfuzz `token_set_ratio` +
  `partial_ratio` exactly. Rust alternatives: `fuzzy-matcher` crate (Levenshtein)
  or port the token-set algorithm manually using `regex` + `HashSet`. The
  `tests/test_search.py::TestFuzzySearch` golden-output tests will catch any
  drift.
- **Acceptance:** 2x fuzzy search latency (Python orchestrator overhead
  eliminated); 56/56 tests identical output; Python fallback works when
  `nsa_rust` is absent.

---

### Part 3 - TOC Generator + Cross-Reference Port (`nsa_rust::toc`, `nsa_rust::cross_reference`)

- **Goal:** Accelerate HTML pre-processing for PDF generation (TOC extraction,
  heading annotation, reference linking, renumbering).
- **Targets:**
  - `app/toc_generator/engine.py` (293 LOC) -- `TOCHtmlParser` (L56-91,
    `handle_starttag`/`handle_endtag`/`handle_data`), `extract_toc` (L105),
    `build_toc_html` (L139), `annotate_headings` (L197), `annotate_html` (L236),
    `generate_toc_data` (L267). Uses `re.Match` callbacks for HTML injection.
  - `app/cross_reference/engine.py` (495 LOC) -- `CrossReferenceExtractor` (L147+,
    `extract_references`, `_extract_annexures`, `_extract_sections`,
    `_extract_paragraphs`, `_dedupe`), `link_references` (L308),
    `renumber_paragraphs` (L360), `renumber_html_lists` (L383),
    `renumber_annexures` (L429), `build_enclosures_html` (L453), `annotate_html` (L472).
  - `PDFAssemblyEngine.post_process()` (`app/pdf_assembly/__init__.py`) -- the
    caller that invokes TOC + cross-ref.
- **Steps:**
  1. Create `rust/src/toc.rs` -- port `extract_toc(html) -> list[TocEntry]`,
    `build_toc_html(entries) -> str`, `annotate_html(html) -> str` as PyO3
    functions returning JSON/serialized data.
  2. Create `rust/src/cross_reference.rs` -- port
    `extract_references(text) -> list[CrossReference]`,
    `link_references(text, refs) -> str`, `renumber_paragraphs(text) -> str`,
    `annotate_html(html) -> str`.
  3. Add `# [pyfunction]` exports in `lib.rs`.
  4. Wire into `app/toc_generator/engine.py::TOCEngine` +
    `app/cross_reference/engine.py::CrossReferenceEngine` -- try `nsa_rust.toc`
    / `nsa_rust.cross_reference` first, fall back to Python.
  5. Build + run `tests/test_phase7_toc_generator.py` (37) +
    `tests/test_cross_reference.py` (27) = 64 tests.
- **Acceptance:** 2-3x pre-processing throughput; 64/64 tests identical; fallback
  works when `nsa_rust` absent.

---

### Part 4 - RAG Enrichment + Verification Port (`nsa_rust::enrichment`, `nsa_rust::verification`)

- **Goal:** Accelerate ingestion-time enrichment and request-time hallucination
  detection. The evidence-verifier per-pair scoring loop uses `rayon` for
  parallelism.
- **Targets:**
  - **Enrichment** (`app/rag/enrichment/deterministic.py`, 621 LOC):
    `attribute_sections` (L125), `extract_crossref_candidates` (L165),
    `resolve_cross_references` (L217), `extract_keywords` (L293),
    `legal_act_of` (L317), `structural_flags_of` (L396),
    `build_deterministic_record` (L419), `enrich_document` (L502).
  - `app/rag/entity_extractor.py` (466 LOC): `_rule_based` (L290),
    `_score` (L406), `_dedupe` (L420).
  - `app/rag/citation_adapter.py` (131 LOC), `app/rag/crossref_adapter.py`
    (170 LOC), `app/rag/metadata_adapter.py` (254 LOC).
  - **Verification** (`app/rag/verification/`):
    `claim_extractor.py` -- `extract` (L103), `_split_sentences` (L139),
    `_is_claim` (L146), `_extract_entities` (L155); `evidence_verifier.py` --
    `verify_claim` (L79), `verify_claims` (L135), `_best_text_match` (L158);
    `hallucination_detector.py` -- `detect` (L110), `_partition` (L197),
    `_llm_verify_claims` (L212); `citation_validator.py` -- `validate` (L67);
    `scorer.py` -- `score` (L83); `token_counter.py` -- `estimate` (L80),
    `estimate_usage` (L91).
  - **Callers:** `IngestionPipeline` (`app/rag/tasks.py`) +
    `GroundedGenerationService` (`app/rag/generation/grounded_service.py`).
- **Steps:**
  1. Create `rust/src/enrichment.rs` -- port the deterministic enrichment
    pipeline (`build_deterministic_record`, `enrich_document`,
    `attribute_sections`, `extract_keywords`, etc.) returning JSON.
  2. Create `rust/src/verification.rs` -- port `ClaimExtractor.extract`,
    `EvidenceVerifier.verify_claims`, `HallucinationDetector.detect`. Use
    `rayon`'s parallel iterators for the per-(claim, chunk) scoring loop in
    `verify_claim`/`_best_text_match` (currently sequential rapidfuzz calls).
  3. Add `# [pyfunction]` exports in `lib.rs`.
  4. Wire into `app/rag/enrichment/deterministic.py` (try `nsa_rust.enrichment`
    first) + `app/rag/verification/claim_extractor.py` +
    `evidence_verifier.py` (try `nsa_rust.verification` first, fall back).
  5. Build + run parity tests: `test_enrichment_deterministic.py` (23) +
    `test_enrichment_audit.py` (10) + `test_enrichment_eval.py` (21) =
    54 enrichment tests; `test_hallucination_detector.py` (28) +
    `test_citation_validator.py` (6) + `test_token_counter.py` (10) +
    `test_rag_e2e_verification.py` (6) = 50 verification tests. (Note: AGENTS.md
    cites "255 enrichment + 48 verification" counting parametrized expansions;
    raw test functions = 54 + 50.)
- **Acceptance:** 3x enrichment throughput; 5x verification throughput
    (parallel via rayon); zero test regressions across all 54 + 50 tests.

---

### Part 5 - Legal Paragraph Engine Port (`nsa_rust::legal_engine`) - Highest ROI, hardest

- **Goal:** Replace `legal_paragraph_detection_engine` with Rust equivalents for
  the regex/compute-heavy sub-modules, exposed via `nsa_rust::legal_engine::*`.
  The Python `LegalParagraphEngine.process_document()` wrapper tries Rust first,
  falls back to pure Python.
- **Targets** (176 test functions across 15 files in `legal_paragraph_detection_engine/tests/unit/`):
  | Sub-module | File | Key methods |
  |---|---|---|
  | `TextNormalizer` | `src/core/paragraph.py` L68-269 | `clean_text` (L107), `find_legal_sections` (L144), `extract_citations_from_text` (L160), `split_into_paragraphs` (L190), `is_paragraph_boundary` (L220) |
  | `ParagraphBoundaryDetector` | `src/core/paragraph.py` L275-648 | `detect_paragraph_boundaries` (L353), `_detect_hierarchy_level` (L430), `_classify_paragraph_type` (L459), `_extract_section_number` (L587) |
  | `HierarchyDetector` | `src/core/hierarchy.py` L61-484 | `detect_hierarchy` (L189), `_detect_section` (L268), `_build_hierarchy` (L466) |
  | `CitationExtractor` | `src/storage/citation.py` L64-422 | `extract_citations` (L171), `_extract_statutory` (L266), `_extract_section` (L353) |
  | `SectionParser` | `src/parsers/section_parser.py` | Section number/title extraction |
  | `ClauseParser` | `src/parsers/clause_parser.py` | Clause/subclause boundary detection |
  | `DocumentTypeClassifier` | `src/parsers/legal_document.py` L212 | `classify_document(text) -> LegalDocument` |
  | `LegalParagraphEngine` | `src/legal_engine.py` L86-448 | `process_document` (L123), `_build_hierarchical_structure` (L224), `_calculate_confidence_scores` (L376) |
- **Steps:**
  1. Fix the Part 1 compilation blocker: remove the 3 broken `# [pyfunction]`
     stubs (`detect_paragraphs`, `extract_citations`, `classify_document`) +
     `mod legal_engine;` from `lib.rs` so Part 1 builds cleanly. Re-add in this step.
  2. Implement `rust/src/legal_engine.rs` -- populate the struct types already
     defined (`DetectionConfig`, `LegalCitation`, `ParagraphInfo`) with the
     ported logic from `ParagraphBoundaryDetector.detect_paragraph_boundaries`,
     `HierarchyDetector.detect_hierarchy`, `CitationExtractor.extract_citations`,
     `DocumentTypeClassifier.classify_document`, and
     `LegalParagraphEngine.process_document`.
  3. Expose PyO3 functions: `process_document(text, config_json) -> str` (JSON),
     `extract_citations(text) -> str` (JSON), `classify_document(text) -> str` (JSON).
  4. Wire `app/services/legal_engine.py::get_legal_engine()` to try
     `nsa_rust.legal_engine.process_document` first, fall back to
     `LegalParagraphEngine` (pure Python).
  5. Build + run parity: `legal_paragraph_detection_engine/tests/` (176 tests)
     + RAG chunking tests.
- **Parity risk:** `_make_citation_pattern` (legal_engine.py L41) uses
  `(?<!\\w)` lookbehind -- Rust `regex` crate does not support lookbehind; switch
  to `fancy-regex` crate or restructure to lookahead-based patterns. The
  `CitationExtractor._compile_patterns()` regex catalog (L156) must be ported
  faithfully.
- **Acceptance:** 5x chunking throughput; 100% test parity across 176 engine
    tests + RAG chunking tests; Python fallback works when `nsa_rust` absent.

"""

    new_content = content[:start_idx] + new_section + content[end_idx:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    sys.stderr.write(f"OK: replaced {len(old_section)} chars\n")

if __name__ == '__main__':
    main()

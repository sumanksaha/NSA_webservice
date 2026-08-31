//! `nsa_rust` — PyO3 acceleration module for NSA Webservice.
//!
//! Strategy (see docs/RUST_REFACTORING_EVALUATION.md):
//! pure-Python regex/compute hot paths are re-implemented here as native Rust
//! functions exposed to Python via PyO3, with a pure-Python fallback kept in
//! the app so behaviour is identical whether or not the compiled extension is
//! present.

mod cross_reference;
mod enrichment;
mod legal_engine;
mod normalizers;
mod removers;
mod search_fuzzy;
mod toc;
mod verification;

use pyo3::prelude::*;

// --- Document Cleaner normalizers (Phase 2 complete) -----------------------

#[pyfunction]
fn normalize_text(text: &str, apply_hyphens: bool) -> String {
    normalizers::normalize_text(text, apply_hyphens)
}
#[pyfunction]
fn normalize_unicode(text: &str) -> String {
    normalizers::normalize_unicode(text)
}
#[pyfunction]
fn normalize_encoding(text: &str) -> String {
    normalizers::normalize_encoding(text)
}
#[pyfunction]
fn normalize_bullets(text: &str) -> String {
    normalizers::normalize_bullets(text)
}
#[pyfunction]
fn normalize_quotes(text: &str) -> String {
    normalizers::normalize_quotes(text)
}
#[pyfunction]
fn normalize_tabs(text: &str) -> String {
    normalizers::normalize_tabs(text)
}
#[pyfunction]
fn normalize_hyphens(text: &str) -> String {
    normalizers::normalize_hyphens(text)
}
#[pyfunction]
fn normalize_spaces(text: &str) -> String {
    normalizers::normalize_spaces(text)
}
#[pyfunction]
fn normalize_trailing_whitespace(text: &str) -> String {
    normalizers::normalize_trailing_whitespace(text)
}
#[pyfunction]
fn normalize_linebreaks(text: &str) -> String {
    normalizers::normalize_linebreaks(text)
}

// --- Document Cleaner removers (Phase 2 complete) --------------------------

#[pyfunction]
fn run_removers(lines: Vec<String>, config_json: &str) -> PyResult<(Vec<String>, String)> {
    let cfg: removers::RemoverConfig = serde_json::from_str(config_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let (kept, removed) = removers::run_removers(&lines, &cfg);
    let removed_json = serde_json::to_string(&removed)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    Ok((kept, removed_json))
}

#[pyfunction]
fn remove_ocr_artifacts(text: &str) -> PyResult<(String, String)> {
    let (cleaned, removed) = removers::remove_ocr_artifacts(text);
    let removed_json = serde_json::to_string(&removed)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    Ok((cleaned, removed_json))
}

// --- Legal Paragraph Detection Engine (Phase 1 stub) -----------------------

#[pyfunction]
fn detect_paragraphs(text: &str, config_json: &str) -> PyResult<String> {
    // Empty config `{}` falls back to DetectionConfig::default().
    let config: legal_engine::DetectionConfig =
        serde_json::from_str(config_json).unwrap_or_default();
    let result = legal_engine::detect_paragraphs(text, &config);
    serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

#[pyfunction]
fn extract_citations(text: &str) -> PyResult<String> {
    let result = legal_engine::extract_citations(text);
    serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

#[pyfunction]
fn classify_document(text: &str) -> PyResult<String> {
    let result = legal_engine::classify_document(text);
    serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

// --- Search fuzzy helpers (Phase 2 complete) ------------------------------

#[pyfunction]
fn ratio(s1: &str, s2: &str) -> f64 {
    search_fuzzy::ratio(s1, s2)
}
#[pyfunction]
fn partial_ratio(s1: &str, s2: &str) -> f64 {
    search_fuzzy::partial_ratio(s1, s2)
}
#[pyfunction]
fn partial_ratio_alignment(s1: &str, s2: &str) -> Option<(usize, usize, f64)> {
    search_fuzzy::partial_ratio_alignment(s1, s2)
}
#[pyfunction]
fn token_set_ratio(s1: &str, s2: &str) -> f64 {
    search_fuzzy::token_set_ratio(s1, s2)
}
#[pyfunction]
fn expand_to_word(text: &str, start: usize, end: usize) -> (usize, usize) {
    search_fuzzy::expand_to_word(text, start, end)
}
#[pyfunction]
fn find_match_spans(query: &str, text: &str, fuzzy_word_threshold: f64) -> String {
    search_fuzzy::find_match_spans(query, text, fuzzy_word_threshold)
}
#[pyfunction]
fn apply_marks(text: &str, spans_json: &str) -> String {
    search_fuzzy::apply_marks(text, spans_json)
}
#[pyfunction]
fn snippet_around_match(query: &str, text: &str, width: usize) -> String {
    search_fuzzy::snippet_around_match(query, text, width)
}
#[pyfunction]
fn snippet_around_matches(
    query: &str,
    text: &str,
    width: usize,
    fuzzy_word_threshold: f64,
) -> String {
    search_fuzzy::snippet_around_matches(query, text, width, fuzzy_word_threshold)
}
#[pyfunction]
fn field_score(query: &str, text: &str) -> f64 {
    search_fuzzy::field_score(query, text)
}
#[pyfunction]
fn highlight_text(query: &str, text: &str, fuzzy_word_threshold: f64) -> String {
    search_fuzzy::highlight_text(query, text, fuzzy_word_threshold)
}

// --- RAG Enrichment (Phase 3 placeholder) -----------------------------------

#[pyfunction]
fn enrich_document_chunks(input_json: &str) -> PyResult<String> {
    Ok(input_json.to_string())
}
#[pyfunction]
fn extract_entities(_text: &str) -> PyResult<String> {
    Ok("[]".to_string())
}

// --- RAG Verification (Phase 3 placeholder) --------------------------------

#[pyfunction]
fn extract_claims(text: &str) -> PyResult<String> {
    let sentences: Vec<&str> = text
        .split(['.', '\n'])
        .filter(|s| !s.trim().is_empty())
        .collect();
    let claims: Vec<String> = sentences
        .into_iter()
        .map(|s| s.trim().to_string())
        .collect();
    serde_json::to_string(&claims)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}
#[pyfunction]
fn verify_claims(_claims_json: &str, _chunks_json: &str) -> PyResult<String> {
    Ok("[]".to_string())
}
#[pyfunction]
fn count_tokens(text: &str) -> PyResult<usize> {
    Ok(text.split_whitespace().count())
}

// --- Cross-Reference + TOC (Phase 4 placeholder) --------------------------

#[pyfunction]
fn process_cross_references(html_text: &str) -> PyResult<String> {
    Ok(cross_reference::process_cross_references(html_text))
}
#[pyfunction]
fn generate_toc(html_text: &str) -> PyResult<String> {
    Ok(toc::generate_toc(html_text))
}

/// The PyO3 extension module entry point (imported in Python as `nsa_rust`).
#[pymodule]
fn nsa_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(normalize_text, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_unicode, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_encoding, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_bullets, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_quotes, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_tabs, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_hyphens, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_spaces, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_trailing_whitespace, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_linebreaks, m)?)?;
    m.add_function(wrap_pyfunction!(run_removers, m)?)?;
    m.add_function(wrap_pyfunction!(remove_ocr_artifacts, m)?)?;
    m.add_function(wrap_pyfunction!(detect_paragraphs, m)?)?;
    m.add_function(wrap_pyfunction!(extract_citations, m)?)?;
    m.add_function(wrap_pyfunction!(classify_document, m)?)?;
    m.add_function(wrap_pyfunction!(ratio, m)?)?;
    m.add_function(wrap_pyfunction!(partial_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(partial_ratio_alignment, m)?)?;
    m.add_function(wrap_pyfunction!(token_set_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(expand_to_word, m)?)?;
    m.add_function(wrap_pyfunction!(find_match_spans, m)?)?;
    m.add_function(wrap_pyfunction!(apply_marks, m)?)?;
    m.add_function(wrap_pyfunction!(snippet_around_match, m)?)?;
    m.add_function(wrap_pyfunction!(snippet_around_matches, m)?)?;
    m.add_function(wrap_pyfunction!(field_score, m)?)?;
    m.add_function(wrap_pyfunction!(highlight_text, m)?)?;
    m.add_function(wrap_pyfunction!(enrich_document_chunks, m)?)?;
    m.add_function(wrap_pyfunction!(extract_entities, m)?)?;
    m.add_function(wrap_pyfunction!(extract_claims, m)?)?;
    m.add_function(wrap_pyfunction!(verify_claims, m)?)?;
    m.add_function(wrap_pyfunction!(count_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(process_cross_references, m)?)?;
    m.add_function(wrap_pyfunction!(generate_toc, m)?)?;
    Ok(())
}

//! `nsa_rust` — PyO3 acceleration module for NSA Webservice.
//!
//! Strategy (see docs/RUST_REFACTORING_EVALUATION.md + task.md Part 1):
//! pure-Python regex/compute hot paths are re-implemented here as native Rust
//! functions exposed to Python via PyO3, with a pure-Python fallback kept in
//! the app so behaviour is identical whether or not the compiled extension is
//! present.

mod normalizers;
mod removers;
mod legal_engine;  // Phase 1: Legal Paragraph Detection Engine (#1 target)

use pyo3::prelude::*;

// --- Document Cleaner normalizers (Part 1) ---------------------------------
// Each mirrors `app/document_cleaner/normalizers.py` exactly.

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

// --- Document Cleaner removers (Part 1) ------------------------------------
// Mirrors `app/document_cleaner/removers.py` + `pipeline.py::_run_removers`.

#[pyfunction]
fn run_removers(lines: Vec<String>, config_json: &str) -> PyResult<(Vec<String>, String)> {
    let cfg: removers::RemoverConfig =
        serde_json::from_str(config_json).map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
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

// --- Legal Paragraph Detection Engine (Phase 1) ---------------------------
// Mirrors `legal_paragraph_detection_engine/src/core/` modules.

#[pyfunction]
fn detect_paragraphs(text: &str, config_json: &str) -> PyResult<String> {
    let config: legal_engine::DetectionConfig =
        serde_json::from_str(config_json).map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
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
    Ok(())
}
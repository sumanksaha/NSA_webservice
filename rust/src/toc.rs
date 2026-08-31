//! TOC Generator — Phase 4 Rust stub.
//!
//! Per docs/RUST_REFACTORING_EVALUATION.md Section 5 Phase 4:
//! Port `app/toc_generator/engine.py` (293 LOC, heading tag parsing + numbering).
//! Bounded gain (~2–3× pre-processing, WeasyPrint I/O-bound overall). Full port: 1 week.

/// `generate_toc` — parse HTML headings and build numbered hierarchy.
pub fn generate_toc(html_text: &str) -> String {
    // TODO Phase 4: heading tag extraction + numbered list building
    html_text.to_string()
}

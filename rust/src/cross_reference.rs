//! Cross-Reference Engine — Phase 4 Rust stub.
//!
//! Per docs/RUST_REFACTORING_EVALUATION.md Section 5 Phase 4:
//! Port `app/cross_reference/engine.py` (495 LOC, regex-based HTML rewriting).
//! Bounded gain (~2–3×, WeasyPrint I/O-bound overall). Full port: 1–2 weeks.

/// `process_cross_references` — extract cross-references from HTML/text.
pub fn process_cross_references(html_text: &str) -> String {
    // TODO Phase 4: regex-based reference extraction + renumbering
    html_text.to_string()
}

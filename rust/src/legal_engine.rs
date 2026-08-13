//! Legal Paragraph Detection Engine — Rust port.
//!
//! Mirrors the Python implementation in
//! `legal_paragraph_detection_engine/src/` with the following modules:
//! - TextNormalizer (core/paragraph.py)
//! - ParagraphBoundaryDetector (core/paragraph.py)
//! - HierarchyDetector (core/hierarchy.py)
//! - SectionParser (parsers/section_parser.py)
//! - ClauseParser (parsers/clause_parser.py)
//! - CitationExtractor (storage/citation.py)
//! - DocumentTypeClassifier (parsers/legal_document.py)
//!
//! This module exposes high-level PyO3 functions that the Python wrapper
//! calls, falling back to pure-Python when the compiled extension is absent.

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// --- Data structures mirroring Python dataclasses ---

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct DetectionConfig {
    pub mode: String,
    pub max_depth: i32,
    pub confidence_threshold: f64,
    pub preserve_citations: bool,
    pub normalize_text: bool,
    pub detect_special_patterns: bool,
    pub output_format: String,
    pub export_path: String,
    pub cache_size: i32,
    pub paragraph_boundary_chars: i32,
    pub content_quality_word_curve: f64,
}

impl Default for DetectionConfig {
    fn default() -> Self {
        DetectionConfig {
            mode: "accurate".to_string(),
            max_depth: 10,
            confidence_threshold: 0.7,
            preserve_citations: true,
            normalize_text: true,
            detect_special_patterns: true,
            output_format: "json".to_string(),
            export_path: "output".to_string(),
            cache_size: 1000,
            paragraph_boundary_chars: 100,
            content_quality_word_curve: 150.0,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct LegalCitation {
    pub citation_type: String,
    pub normalized_text: String,
    pub details: HashMap<String, String>,
    pub confidence: f64,
    pub context: String,
    pub source_text: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ParagraphInfo {
    pub id: String,
    pub text: String,
    pub paragraph_type: String,
    pub start_line: i32,
    pub end_line: i32,
    pub section: Option<String>,
    pub clause: Option<String>,
    pub subclause: Option<String>,
    pub hierarchy_depth: i32,
    pub word_count: i32,
    pub parent_id: Option<String>,
    pub children: Vec<String>,
    pub citations: Vec<HashMap<String, String>>,
    pub confidence_scores: HashMap<String, f64>,
    pub meets_confidence_threshold: bool,
    pub metadata: HashMap<String, serde_json::Value>,
}

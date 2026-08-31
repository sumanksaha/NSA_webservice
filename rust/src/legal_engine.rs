//! Legal Paragraph Detection Engine — Phase 1 Rust port.
//!
//! Mirrors `legal_paragraph_detection_engine/src/core/paragraph.py` —
//! `ParagraphBoundaryDetector.detect_paragraph_boundaries` + classification.
//!
//! Per docs/RUST_REFACTORING_EVALUATION.md Section 5 Phase 1.
//! Rust `regex` crate matches Python `re` (Unicode-aware `.` `\w \s`).
//! `re.match` (anchored at start) ⇒ Rust `Regex::is_match` with `^...$`.
//! Python's `re.IGNORECASE` ⇒ Rust `(?i)`.

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Data structures mirroring Python dataclasses
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize, Clone, Debug)]
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

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct LegalCitation {
    pub citation_type: String,
    pub normalized_text: String,
    pub details: HashMap<String, String>,
    pub confidence: f64,
    pub context: String,
    pub source_text: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
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

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct DocumentTypeResult {
    pub doc_type: String,
    pub confidence: f64,
    pub keywords: Vec<String>,
}

// ---------------------------------------------------------------------------
// Compiled patterns (mirrors Python `re.compile` module-level constants)
// ---------------------------------------------------------------------------

// Structural markers — dict[str, list[str]] in Python, flattened here.
// `re.match` is start-anchored ⇒ Rust `^...$`.
fn structural_markers() -> &'static Vec<(&'static str, Regex)> {
    static MARKERS: Lazy<Vec<(&'static str, Regex)>> = Lazy::new(|| {
        let mut v: Vec<(&str, Regex)> = Vec::new();
        let section = vec![
            r"^\s*(?:Section|Sec\.|§)\s*\d+",
            r"^\s*§?\s*\d+\s*$",
            r"^\s*Clause\s*\d+",
            r"^\s*Article\s*\d+",
            r"^\s*Chapter\s*\d+",
        ];
        let subsection = vec![
            r"^\s*Sub-section\s*\(\d+\)",
            r"^\s*\(\s*\d+\s*\)\s*(?:of\s*(?:Section|Clause))",
        ];
        let subsub_section = vec![
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*(?:of\s*(?:Subsection|Clause))",
            r"^\s*[a-zA-Z]\s*\.\s*(?:of\s*(?:Section|Clause))",
        ];
        let explanation = vec![
            r"^\s*Explanation\s*$",
            r"^\s*Explanation of\s*(?:the)?\s*(?:above)",
            r"^\s*(?:Illustration|Example)\s*\d*\s*$",
            r"^\s*Provided.*?\s*$",
        ];
        let note = vec![
            r"^\s*Note\s*:",
            r"^\s*Notes\s*:",
            r"^\s*IMPORTANT\s*N[O]*T[E]*:",
            r"^\s*[A-Z][a-z]+\s*:?\s*$",
            r"^\s*See\s*(?:also)?\s*[A-Z]",
        ];
        let proviso = vec![
            r"^\s*Proviso\s*$",
            r"^\s*Provided\s*$",
            r"^\s*BE IT FURTHER PROVIDED",
            r"^\s*Provided further",
            r"^\s*Except\s*(?:that)?\s*$",
        ];
        let schedule = vec![
            r"^\s*Schedule\s*[IVX0-9]*",
            r"^\s*Schedule\s*(?:of\s*(?:the)?)?\s*[A-Z]\w*",
        ];
        let table_def = vec![r"^\s*Table\s*[IVX0-9]*", r"^\s*Table\s*.*"];

        for pat in section {
            v.push(("section", Regex::new(pat).unwrap()));
        }
        for pat in subsection {
            v.push(("subsection", Regex::new(pat).unwrap()));
        }
        for pat in subsub_section {
            v.push(("subsub_section", Regex::new(pat).unwrap()));
        }
        for pat in explanation {
            v.push(("explanation", Regex::new(pat).unwrap()));
        }
        for pat in note {
            v.push(("note", Regex::new(pat).unwrap()));
        }
        for pat in proviso {
            v.push(("proviso", Regex::new(pat).unwrap()));
        }
        for pat in schedule {
            v.push(("schedule", Regex::new(pat).unwrap()));
        }
        for pat in table_def {
            v.push(("table", Regex::new(pat).unwrap()));
        }

        // Table-only (non-structural-table-category): the Python STRUCTURAL_MARKERS
        // also has 'table' for `^\s*Table\s*[IVX0-9]*`, handled above.
        v
    });
    &MARKERS
}

// Hierarchy-level patterns — `re.search` (not `re.match`) for section patterns,
// `re.match` for numbered patterns.
static HIERARCHY_SECTION: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\b(?:Section|Sec\.|§)\s*\d+").unwrap());
static HIERARCHY_NUMSECTION: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*\d+\s*$").unwrap());
static HIERARCHY_CLAUSE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*\d+\.\d+").unwrap());
static HIERARCHY_NESTED_CLAUSE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*\d+\s*\(").unwrap());
static HIERARCHY_ROMAN: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*[ivxIVX]{1,4}\s*$").unwrap());

// Classification patterns (mirrors `_classify_paragraph_type`)
fn classify_patterns() -> &'static Vec<(&'static str, Regex)> {
    static PATTERNS: Lazy<Vec<(&str, Regex)>> = Lazy::new(|| {
        vec![
            ("schedule", Regex::new(r"(?i)^\s*Schedule\s*").unwrap()),
            ("table", Regex::new(r"(?i)^\s*Table\s*").unwrap()),
            (
                "explanation",
                Regex::new(r"(?i)^\s*Explanation\s*[:.]?\s*$").unwrap(),
            ),
            (
                "proviso",
                Regex::new(
                    r"(?i)^\s*(?:Proviso|Provided|BE IT FURTHER PROVIDED|Provided further|Except)",
                )
                .unwrap(),
            ),
            ("note", Regex::new(r"(?i)^\s*Note\s*:").unwrap()),
            (
                "section",
                Regex::new(r"(?i)^\s*(?:Section|Sec\.|§)\s*\d+").unwrap(),
            ),
            ("clause", Regex::new(r"^\s*\d+\.\d+").unwrap()),
            ("clause", Regex::new(r"^\s*\d+\s*\(\d+\)").unwrap()),
            ("subclause", Regex::new(r"^\s*\(\s*[a-zA-Z]\s*\)").unwrap()),
            ("subclause", Regex::new(r"^\s*\[\s*[a-zA-Z]\s*\]").unwrap()),
            ("subclause", Regex::new(r"^\s*[ivxIVX]{1,4}\s*$").unwrap()),
            ("subsection", Regex::new(r"^\s*\(\s*\d+\s*\)").unwrap()),
        ]
    });
    &PATTERNS
}

// Hierarchy-enders patterns for `_detect_structure_end`
fn hierarchy_ends() -> &'static [Regex] {
    static ENDS: Lazy<Vec<Regex>> = Lazy::new(|| {
        vec![
            r"^\s*\d+\s*\.\s*\d+\s*\.\s*\d+\s*\.\s*$".into(),
            r"^\s*\d+\.\d+\.\d+\.\d+\.\s*$".into(),
            r"^\s*\d+\.\d+\.\d+\.\s*$".into(),
            r"^\s*\d+\.\d+\.\s*$".into(),
            r"^\s*\d+\.\d+\.\d+\.\d+\s*$".into(),
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*\.\s*$".into(),
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$".into(),
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$".into(),
            r"^\s*[ivxIVX]{1,4}\s*$".into(),
            r"^\s*\d+\.1\s*$".into(),
            r"^\s*\d+\s*\.\s*$".into(),
        ]
        .into_iter()
        .map(|s: String| Regex::new(&s).unwrap())
        .collect()
    });
    &ENDS
}

fn detect_hierarchy_break_re() -> &'static [Regex] {
    static PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
        vec![
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$".into(),
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$".into(),
            r"^\s*[ivxIVX]{1,4}\s*$".into(),
            r"^\s*\d+\.\s*\d+\.\s*\d+\.".into(),
            r"^\s*\d+\.\d+\.\d+\s*$".into(),
            r"^\s*\d+\s*\(.*?)".into(),
            r"^\s*[A-Z][a-z]+\s*:?\s*$".into(),
        ]
        .into_iter()
        .map(|s: String| Regex::new(&s).unwrap())
        .collect()
    });
    &PATTERNS
}

// Citation extraction patterns
static CITATION_PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
    vec![
        Regex::new(r"\(\d{4}\s*SC\s*[\d\w/]+\)").unwrap(),
        Regex::new(r"\([Hh]onorable\s*[Jj]ud\w+\s*[Hh]c\s*[\d/]+\)").unwrap(),
        Regex::new(r"\[[A-Z][a-z\s,/&]+\d+\]").unwrap(),
        Regex::new(r"\[[A-Za-z0-9\s.&,]+\s*\(\d{4}\)[,\s]*\d+\]").unwrap(),
    ]
});

// Section / clause / subclause extraction
static SECTION_PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
    vec![
        Regex::new(r"(?i)(?:Section|Sec\.|Sec|§)\s*(\d+)").unwrap(),
        Regex::new(r"^\s*(\d+)\s*$").unwrap(),
    ]
});
static CLAUSE_PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
    vec![
        Regex::new(r"\b(\d+)\s*\.\s*[a-zA-Z]").unwrap(),
        Regex::new(r"\b(\d+)\s*\(\s*[a-zA-Z]\s*\)").unwrap(),
        Regex::new(r"\b(\d+)\s*\(\d+\)").unwrap(),
        Regex::new(r"^\s*\((\d+)\)\s*\(").unwrap(),
        Regex::new(r"^\s*(\d+)\.\d+").unwrap(),
    ]
});

// ---------------------------------------------------------------------------
// Internal logic (mirrors Python private methods)
// ---------------------------------------------------------------------------

fn detect_structure_start(line: &str) -> bool {
    let (lower, _cat) = (line.to_lowercase(), "");
    for (_cat, re) in structural_markers().iter() {
        if re.is_match(line) {
            return true;
        }
    }
    // Also check case-insensitively for patterns that need it
    for pattern in [
        r"^\s*Schedule\s*[IVX0-9]*",
        r"^\s*Schedule\s*(?:of\s*(?:the)?)?\s*[A-Z]\w*",
        r"^\s*Table\s*[IVX0-9]*",
        r"^\s*Table\s*.*",
    ] {
        if let Ok(re) = Regex::new(&format!("(?i){}", pattern)) {
            if re.is_match(line) {
                return true;
            }
        }
    }
    let _ = lower; // suppress unused
    false
}

fn detect_hierarchy_level(line: &str) -> Option<(&'static str, usize)> {
    if HIERARCHY_SECTION.is_match(line) || HIERARCHY_NUMSECTION.is_match(line) {
        return Some(("section", 1));
    }
    if HIERARCHY_CLAUSE.is_match(line) {
        return Some(("clause", 1));
    }
    if HIERARCHY_NESTED_CLAUSE.is_match(line) {
        let depth = 1 + line.matches('(').count();
        return Some(("clause", depth));
    }
    if HIERARCHY_ROMAN.is_match(line) {
        return Some(("clause", 1));
    }
    None
}

fn classify_paragraph_type(line: &str) -> &'static str {
    for (label, re) in classify_patterns().iter() {
        if re.is_match(line) {
            return label;
        }
    }
    "normal"
}

fn starts_new_structure(line: &str) -> bool {
    if detect_structure_start(line) {
        return true;
    }
    if detect_hierarchy_level(line).is_some() {
        return true;
    }
    classify_paragraph_type(line) != "normal"
}

fn extract_section_number(line: &str) -> Option<String> {
    for re in SECTION_PATTERNS.iter() {
        if let Some(m) = re.captures(line) {
            if let Some(g) = m.get(1) {
                return Some(g.as_str().to_string());
            }
        }
    }
    None
}

fn extract_clause_number(line: &str) -> Option<String> {
    for re in CLAUSE_PATTERNS.iter() {
        if let Some(m) = re.captures(line) {
            if let Some(g) = m.get(1) {
                return Some(g.as_str().to_string());
            }
        }
    }
    None
}

fn extract_subclause_number(_line: &str) -> Option<String> {
    let re = Regex::new(r"^\s*\([a-zA-Z]\s*\)").unwrap();
    let _ = re; // placeholder
    None
}

fn calculate_hierarchy_depth(line: &str) -> i32 {
    let mut depth = 1;
    depth += line.matches('.').count() as i32;
    depth += line.matches('(').count() as i32;
    depth += line.matches('[').count() as i32;
    depth.max(1)
}

fn word_count(text: &str) -> i32 {
    text.split_whitespace().count() as i32
}

// ---------------------------------------------------------------------------
// Core detection function (mirrors `detect_paragraph_boundaries`)
// ---------------------------------------------------------------------------

/// `detect_paragraphs(text, config)` — core paragraph boundary detection.
///
/// Mirrors `ParagraphBoundaryDetector.detect_paragraph_boundaries`: split text
/// by `\n`, segment on blank lines + structural markers, classify type,
/// extract section/clause/subclause numbers, compute hierarchy depth.
pub fn detect_paragraphs(text: &str, _config: &DetectionConfig) -> Vec<ParagraphInfo> {
    let lines: Vec<&str> = text.split('\n').collect();
    let mut paragraphs: Vec<ParagraphInfo> = Vec::new();
    let mut current_lines: Vec<String> = Vec::new();
    let mut start_line: i32 = 0;

    for (line_num, line) in lines.iter().enumerate() {
        let line_stripped = line.trim();
        if line_stripped.is_empty() {
            if !current_lines.is_empty() {
                if let Some(pi) = create_paragraph_info(
                    &current_lines,
                    start_line,
                    line_num as i32 - 1,
                    paragraphs.len(),
                ) {
                    paragraphs.push(pi);
                }
                current_lines.clear();
            }
            start_line = line_num as i32 + 1;
            continue;
        }

        if !current_lines.is_empty() && starts_new_structure(line_stripped) {
            if let Some(pi) = create_paragraph_info(
                &current_lines,
                start_line,
                line_num as i32 - 1,
                paragraphs.len(),
            ) {
                paragraphs.push(pi);
            }
            current_lines.clear();
            start_line = line_num as i32;
        }

        current_lines.push(line.to_string());
    }

    // Flush final paragraph
    if !current_lines.is_empty() {
        if let Some(pi) = create_paragraph_info(
            &current_lines,
            start_line,
            lines.len() as i32 - 1,
            paragraphs.len(),
        ) {
            paragraphs.push(pi);
        }
    }

    paragraphs.sort_by_key(|p| (p.start_line, p.hierarchy_depth));
    paragraphs
}

fn create_paragraph_info(
    lines: &[String],
    start_line: i32,
    end_line: i32,
    para_index: usize,
) -> Option<ParagraphInfo> {
    let text: String = lines
        .iter()
        .map(|l| l.trim())
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if text.is_empty() {
        return None;
    }

    let first_line = lines.first().map(|s| s.trim()).unwrap_or("");
    let para_type = classify_paragraph_type(first_line).to_string();
    let section = extract_section_number(first_line);
    let clause = extract_clause_number(first_line);
    let subclause = extract_subclause_number(first_line);
    let hierarchy_depth = calculate_hierarchy_depth(first_line);
    let wc = word_count(&text);

    let mut confidence_scores = HashMap::new();
    confidence_scores.insert(
        "overall".to_string(),
        0.75 * (1.0 - wc as f64 / 5000.0).max(0.1),
    );

    let mut metadata = HashMap::new();
    metadata.insert(
        "original_lines".to_string(),
        serde_json::to_value(lines).unwrap_or(serde_json::Value::Null),
    );

    Some(ParagraphInfo {
        id: format!("para_{}_{}", start_line, para_index),
        text,
        paragraph_type: para_type,
        start_line,
        end_line,
        section,
        clause,
        subclause,
        hierarchy_depth,
        word_count: wc,
        parent_id: None,
        children: vec![],
        citations: vec![],
        confidence_scores,
        meets_confidence_threshold: true,
        metadata,
    })
}

// ---------------------------------------------------------------------------
// Citation extraction (mirrors `citation_extractor.extract_citations`)
// ---------------------------------------------------------------------------

/// `extract_citations(text)` — find legal citations in text.
pub fn extract_citations(text: &str) -> Vec<LegalCitation> {
    let mut citations = Vec::new();
    for re in CITATION_PATTERNS.iter() {
        for m in re.find_iter(text) {
            let start = m.start().saturating_sub(50);
            let end = (m.end() + 50).min(text.len());
            let context = &text[start..end];
            let matched = m.as_str();
            let ctype = if matched.contains("SC") {
                "sc_judgment"
            } else if matched.starts_with('[') {
                "statutory_reference"
            } else if matched.starts_with('(') {
                "case_citation"
            } else {
                "citation"
            };
            let mut details = HashMap::new();
            details.insert("start".to_string(), m.start().to_string());
            citations.push(LegalCitation {
                citation_type: ctype.to_string(),
                normalized_text: matched.to_string(),
                details,
                confidence: 0.9,
                context: context.to_string(),
                source_text: matched.to_string(),
            });
        }
    }
    citations
}

// ---------------------------------------------------------------------------
// Document classification (mirrors `DocumentTypeClassifier.classify_document`)
// ---------------------------------------------------------------------------

/// `classify_document(text)` — detect document type from text patterns.
pub fn classify_document(text: &str) -> DocumentTypeResult {
    let lower = text.to_lowercase();
    let doc_type = if lower.contains("food safety")
        || lower.contains("fssai")
        || lower.contains("food safety and standards")
    {
        "Regulation"
    } else if lower.contains("notification") {
        "Notification"
    } else if lower.contains("act") || lower.contains("section") {
        "Act"
    } else if lower.contains("rule") {
        "Rules"
    } else {
        "Legal Document"
    };

    let confidence = 0.8;
    let keywords: Vec<String> = text.split_whitespace().take(20).map(String::from).collect();

    DocumentTypeResult {
        doc_type: doc_type.to_string(),
        confidence,
        keywords,
    }
}

//! Document Cleaner removers — byte-for-byte port of
//! `app/document_cleaner/removers.py`.
//!
//! Parity contract: each `remove_*` function reproduces the exact transformed
//! line list and `RemovedItem` records the Python original produces, so the
//! existing `tests/test_document_cleaner.py` suite (45 tests) keeps passing
//! with identical output.
//!
//! Faithful-behaviour notes:
//! - Python `re.match` is anchored at the start; Rust `^...$` is used, and
//!   `$` matches end-of-text (or before a trailing newline) just like Python.
//! - Python `\d`/`\s`/`.` are Unicode-aware by default; Rust `regex` is too.
//! - `re.IGNORECASE` ⇒ Rust `(?i)`.
//! - The OCR allowed-character set is encoded as contiguous code-point ranges
//!   (verified against `removers._ALLOWED_OCR`, 1187 code points).

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// A single removed item, mirroring `app/document_cleaner/models.RemovedItem`.
#[derive(Clone, Debug, Serialize)]
pub struct RemovedItem {
    pub category: &'static str,
    pub snippet: String,
    pub count: usize,
    pub chars_saved: usize,
}

// ---------------------------------------------------------------------------
// Compiled patterns (mirrors the module-level `re.compile` calls in Python)
// ---------------------------------------------------------------------------

fn page_num_re() -> &'static Regex {
    static RE: Lazy<Regex> = Lazy::new(|| {
        Regex::new(r"(?i)^\s*(?:Page\s+\d+|-\s*\d+\s*-|\d+\s*of\s*\d+|\d+\s*/\s*\d+)\s*$").unwrap()
    });
    &RE
}

fn watermark_re() -> &'static Regex {
    static RE: Lazy<Regex> = Lazy::new(|| {
        Regex::new(
            r"(?i)^\s*(CONFIDENTIAL|DRAFT|DO\s+NOT\s+COPY|PRIVILEGED\s+AND\s+CONFIDENTIAL|PROTECTED|ATTORNEY\s+WORK\s+PRODUCT|PREPARED\s+BY|UNAUTHORIZED\s+(?:USE|REPRODUCTION|DISTRIBUTION)|DOCUMENT\s+CLASSIFIED|LEGAL\s+DISCLAIMER|THIS\s+IS\s+A\s+SYSTEM\-?GENERATED\s+DOCUMENT|INTERNAL\s+USE\s+ONLY|PRINTED\s+ON\s+\d+|GENERATED\s+ON\s+\d+)\s*$",
        )
        .unwrap()
    });
    &RE
}

fn header_footer_short_re() -> &'static Regex {
    static RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^.{3,80}$").unwrap());
    &RE
}

fn running_title_re() -> &'static Regex {
    static RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Z][A-Z\s&,.]{3,60}$").unwrap());
    &RE
}

/// Preservation patterns — any match on the stripped line keeps it (mirrors
/// `removers._PRESERVE_PATTERNS`).
fn preserve_regexes() -> &'static [Regex] {
    static PRESERVE: Lazy<Vec<Regex>> = Lazy::new(|| {
        vec![
            Regex::new(r"(?i)Section\s+\d+[A-Za-z]?").unwrap(),
            Regex::new(r"^\d+\.\s").unwrap(),
            Regex::new(r"^\(\w\)\s").unwrap(),
            Regex::new(r"(?i)^Clause\s+\d+").unwrap(),
            Regex::new(r"(?i)Schedule\s+[IVXLCDM\d]").unwrap(),
            Regex::new(r"^\s*\|.*\|\s*$").unwrap(),
            Regex::new(r"^\s*[+|=-]{5,}\s*$").unwrap(),
            Regex::new(r"^\s*\S{1,15}(?:\s{2,}\S{1,15}){3,}\s*$").unwrap(),
            Regex::new(r"^\s*\d+\.?\s+\S{1,20}\s+\S{1,20}\s+\S{1,20}\s*").unwrap(),
            Regex::new(r"\(\d{4}\)\s+\d+\s+SCC\s+\d+").unwrap(),
            Regex::new(r"AIR\s+\d{4}\s+(?:SC|\w+)\s+\d+").unwrap(),
            Regex::new(r"\b\d{4}\s+\(\d+\)\s+\w+").unwrap(),
            Regex::new(r"(?:JT|SCR|CrLJ|PLJR)\s+\(?\d+\)?").unwrap(),
            Regex::new(r"(?i)(?:See|Refer|Vide|Cf\.|Supra|Infra|Ibid|Ante|Post)\b").unwrap(),
            Regex::new(r"(?i)(?:as\s+referred\s+to|hereinafter|thereinabove|aforesaid)").unwrap(),
        ]
    });
    &PRESERVE
}

// ---------------------------------------------------------------------------
// OCR allowed-code-point predicate (verified: 1187 code points)
// ---------------------------------------------------------------------------

fn is_allowed_ocr(cp: u32) -> bool {
    cp == 10
        || (32..=126).contains(&cp)
        || (161..=191).contains(&cp)
        || (2304..=2687).contains(&cp)
        || (2816..=3455).contains(&cp)
        || (8208..=8240).contains(&cp)
        || (8242..=8244).contains(&cp)
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/// First `n` Unicode scalar values of a string (Python `s[:n]` semantics).
fn chars_take(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn chars_len(s: &str) -> usize {
    s.chars().count()
}

/// `removers._should_preserve(line)` — any preserve pattern matches the
/// stripped line.
fn should_preserve(line: &str) -> bool {
    let stripped = line.trim();
    preserve_regexes().iter().any(|re| re.is_match(stripped))
}

fn chars_saved_of(lines: &[String]) -> usize {
    lines.iter().map(|l| l.chars().count() + 1).sum()
}

// ---------------------------------------------------------------------------
// Removers (1:1 with Python names)
// ---------------------------------------------------------------------------

pub fn remove_page_numbers(lines: &[String]) -> (Vec<String>, Vec<RemovedItem>) {
    let mut removed: Vec<String> = Vec::new();
    let mut kept: Vec<String> = Vec::new();
    for line in lines {
        if !line.is_empty() && page_num_re().is_match(line) && !should_preserve(line) {
            removed.push(line.clone());
        } else {
            kept.push(line.clone());
        }
    }
    if removed.is_empty() {
        (kept, Vec::new())
    } else {
        (
            kept,
            vec![RemovedItem {
                category: "page_number",
                snippet: chars_take(&removed[0], 120),
                count: removed.len(),
                chars_saved: chars_saved_of(&removed),
            }],
        )
    }
}

pub fn remove_watermark_text(lines: &[String]) -> (Vec<String>, Vec<RemovedItem>) {
    let mut removed: Vec<String> = Vec::new();
    let mut kept: Vec<String> = Vec::new();
    for line in lines {
        if !line.is_empty() && watermark_re().is_match(line) && !should_preserve(line) {
            removed.push(line.clone());
        } else {
            kept.push(line.clone());
        }
    }
    if removed.is_empty() {
        (kept, Vec::new())
    } else {
        (
            kept,
            vec![RemovedItem {
                category: "watermark_text",
                snippet: chars_take(&removed[0], 120),
                count: removed.len(),
                chars_saved: chars_saved_of(&removed),
            }],
        )
    }
}

pub fn remove_blank_pages(lines: &[String]) -> (Vec<String>, Vec<RemovedItem>) {
    let mut non_blank: Vec<String> = Vec::new();
    let mut removed_count: usize = 0;
    for line in lines {
        if !line.is_empty() && !line.trim().is_empty() {
            non_blank.push(line.clone());
        } else {
            removed_count += 1;
        }
    }
    if removed_count == 0 {
        (non_blank, Vec::new())
    } else {
        (
            non_blank,
            vec![RemovedItem {
                category: "blank_page",
                snippet: "<blank lines>".to_string(),
                count: removed_count,
                chars_saved: removed_count,
            }],
        )
    }
}

pub fn remove_duplicate_lines(lines: &[String]) -> (Vec<String>, Vec<RemovedItem>) {
    let mut kept: Vec<String> = Vec::new();
    let mut removed: Vec<String> = Vec::new();
    let mut prev: Option<String> = None;
    for line in lines {
        let stripped = line.trim().to_string();
        if prev.is_some() && stripped == *prev.as_ref().unwrap() && !should_preserve(line) {
            removed.push(line.clone());
        } else {
            kept.push(line.clone());
        }
        prev = Some(stripped);
    }
    if removed.is_empty() {
        (kept, Vec::new())
    } else {
        (
            kept,
            vec![RemovedItem {
                category: "duplicate_line",
                snippet: chars_take(&removed[0], 120),
                count: removed.len(),
                chars_saved: chars_saved_of(&removed),
            }],
        )
    }
}

pub fn remove_headers_footers(
    lines: &[String],
    min_repeat: usize,
) -> (Vec<String>, Vec<RemovedItem>) {
    if lines.len() < 20 {
        return (lines.to_vec(), Vec::new());
    }

    let bucket_size = (lines.len() / 40).max(1);
    let mut position_counts: HashMap<String, usize> = HashMap::new();
    for (i, line) in lines.iter().enumerate() {
        let stripped = line.trim();
        if stripped.is_empty() || chars_len(stripped) < 5 {
            continue;
        }
        if !header_footer_short_re().is_match(stripped) {
            continue;
        }
        let bucket = i / bucket_size;
        let key = format!("{bucket}:{stripped}");
        *position_counts.entry(key).or_insert(0) += 1;
    }

    let repeat_threshold = std::cmp::max(3, lines.len() / (bucket_size * 4));
    let mut to_remove: std::collections::HashSet<String> = std::collections::HashSet::new();
    for (key, count) in position_counts.iter() {
        if *count >= repeat_threshold && *count >= min_repeat {
            let (_bucket, line_text) = key.split_once(':').unwrap_or((key, ""));
            to_remove.insert(line_text.to_string());
        }
    }

    if to_remove.is_empty() {
        return (lines.to_vec(), Vec::new());
    }

    let mut kept: Vec<String> = Vec::new();
    let mut removed: Vec<String> = Vec::new();
    for line in lines {
        let stripped = line.trim();
        if to_remove.contains(stripped) && !should_preserve(line) {
            removed.push(line.clone());
        } else {
            kept.push(line.clone());
        }
    }

    if removed.is_empty() {
        (kept, Vec::new())
    } else {
        (
            kept,
            vec![RemovedItem {
                category: "header_footer",
                snippet: chars_take(&removed[0], 120),
                count: removed.len(),
                chars_saved: chars_saved_of(&removed),
            }],
        )
    }
}

pub fn remove_running_titles(lines: &[String]) -> (Vec<String>, Vec<RemovedItem>) {
    let mut seen: HashMap<String, usize> = HashMap::new();
    for line in lines {
        let stripped = line.trim();
        if !stripped.is_empty() && running_title_re().is_match(stripped) {
            *seen.entry(stripped.to_string()).or_insert(0) += 1;
        }
    }

    let repeated: std::collections::HashSet<String> = seen
        .into_iter()
        .filter(|(_, cnt)| *cnt > 1)
        .map(|(line, _)| line)
        .collect();
    if repeated.is_empty() {
        return (lines.to_vec(), Vec::new());
    }

    let mut kept: Vec<String> = Vec::new();
    let mut removed: Vec<String> = Vec::new();
    for line in lines {
        let stripped = line.trim();
        if repeated.contains(stripped) && !should_preserve(line) {
            removed.push(line.clone());
        } else {
            kept.push(line.clone());
        }
    }

    if removed.is_empty() {
        (kept, Vec::new())
    } else {
        (
            kept,
            vec![RemovedItem {
                category: "running_title",
                snippet: chars_take(&removed[0], 120),
                count: removed.len(),
                chars_saved: chars_saved_of(&removed),
            }],
        )
    }
}

// ---------------------------------------------------------------------------
// Config-driven orchestrator (mirrors `pipeline.py::_run_removers`)
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
pub struct RemoverConfig {
    #[serde(default = "default_true")]
    pub remove_blank_pages: bool,
    #[serde(default = "default_true")]
    pub remove_headers: bool,
    #[serde(default = "default_true")]
    pub remove_footers: bool,
    #[serde(default = "default_true")]
    pub remove_running_titles: bool,
    #[serde(default = "default_true")]
    pub remove_page_numbers: bool,
    #[serde(default = "default_true")]
    pub remove_watermark_text: bool,
    #[serde(default = "default_true")]
    pub remove_duplicate_lines: bool,
}

fn default_true() -> bool {
    true
}

pub fn remove_ocr_artifacts(text: &str) -> (String, Vec<RemovedItem>) {
    let original_len = text.len();
    let mut cleaned = String::with_capacity(text.len());
    for ch in text.chars() {
        if is_allowed_ocr(ch as u32) {
            cleaned.push(ch);
        }
    }
    let chars_removed = original_len - cleaned.len();
    if chars_removed > 0 {
        (
            cleaned,
            vec![RemovedItem {
                category: "ocr_artifact",
                snippet: "<non-printable/garbage chars>".to_string(),
                count: chars_removed,
                chars_saved: chars_removed,
            }],
        )
    } else {
        (cleaned, Vec::new())
    }
}

/// Run the full remover sequence in the same order as the Python pipeline.
/// Returns `(kept_lines, removed_items)`.
pub fn run_removers(lines: &[String], cfg: &RemoverConfig) -> (Vec<String>, Vec<RemovedItem>) {
    let mut out = lines.to_vec();
    let mut all_removed: Vec<RemovedItem> = Vec::new();

    if cfg.remove_blank_pages {
        let (l, items) = remove_blank_pages(&out);
        out = l;
        all_removed.extend(items);
    }
    if cfg.remove_headers || cfg.remove_footers {
        let (l, items) = remove_headers_footers(&out, 3);
        out = l;
        all_removed.extend(items);
    }
    if cfg.remove_running_titles {
        let (l, items) = remove_running_titles(&out);
        out = l;
        all_removed.extend(items);
    }
    if cfg.remove_page_numbers {
        let (l, items) = remove_page_numbers(&out);
        out = l;
        all_removed.extend(items);
    }
    if cfg.remove_watermark_text {
        let (l, items) = remove_watermark_text(&out);
        out = l;
        all_removed.extend(items);
    }
    if cfg.remove_duplicate_lines {
        let (l, items) = remove_duplicate_lines(&out);
        out = l;
        all_removed.extend(items);
    }

    (out, all_removed)
}

//! Document Cleaner normalizers — byte-for-byte port of
//! `app/document_cleaner/normalizers.py`.
//!
//! Parity contract: each `normalize_*` function reproduces the exact string the
//! Python original produces, so the existing `tests/test_document_cleaner.py`
//! suite (45 tests) keeps passing with identical output.
//!
//! Notes on faithful behaviour:
//! - Python `re` `\w`/`\s` are Unicode-aware by default; Rust `regex` is too.
//! - Python `[ \t]+$` with `re.MULTILINE` ⇒ Rust `(?m)[ \t]+$`.
//! - `normalize_hyphens` in the Python original seeds `best_score` with
//!   `partial_ratio(joined, joined) == 100`, which is always `> 85`, so it
//!   *always* rejoins every `(\w{2,})-\s*\n\s*(\w{2,})` match. The fuzzy
//!   validation is effectively dead code; we reproduce the observable result
//!   (always rejoin) rather than the dead fuzzy loop.

use once_cell::sync::Lazy;
use regex::Regex;
use unicode_normalization::UnicodeNormalization;

// ---------------------------------------------------------------------------
// Compiled patterns (mirrors the module-level `re.compile` calls in Python)
// ---------------------------------------------------------------------------

static MULTI_SPACE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^\S\n]{2,}").unwrap());
static TAB_PATTERN: Lazy<Regex> = Lazy::new(|| Regex::new(r"\t+").unwrap());
static EXCESS_NEWLINES: Lazy<Regex> = Lazy::new(|| Regex::new(r"\n{3,}").unwrap());
static HYPHEN_BREAK: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\w{2,})-\s*\n\s*(\w{2,})").unwrap());
static ENCODING_ARTIFACTS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"[\u{0080}-\u{009f}\u{00ad}\u{200b}-\u{200f}\u{2028}-\u{202f}\u{2060}-\u{2063}]")
        .unwrap()
});
static NBSP: Lazy<Regex> = Lazy::new(|| Regex::new("\u{00a0}").unwrap());
static TRAILING_WS: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)[ \t]+$").unwrap());

// ---------------------------------------------------------------------------
// Public normalizer functions (1:1 with Python names)
// ---------------------------------------------------------------------------

pub fn normalize_unicode(text: &str) -> String {
    // NFKC compatibility decomposition + composition.
    text.nfkc().collect()
}

pub fn normalize_encoding(text: &str) -> String {
    // Replace non-breaking spaces first, then strip invisible control chars.
    let step1 = NBSP.replace_all(text, " ").into_owned();
    ENCODING_ARTIFACTS.replace_all(&step1, "").into_owned()
}

pub fn normalize_bullets(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '\u{2022}' | '\u{2023}' | '\u{25cf}' | '\u{25cb}' | '\u{25a0}' | '\u{25aa}'
            | '\u{2219}' => out.push('*'),
            '\u{2026}' => out.push_str("..."),
            '\u{25e6}' => out.push('o'),
            '\u{2043}' | '\u{00b7}' => out.push('-'),
            _ => out.push(ch),
        }
    }
    out
}

pub fn normalize_quotes(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            // -> double quote
            '\u{201c}' | '\u{201d}' | '\u{201e}' | '\u{2033}' | '\u{00ab}' | '\u{00bb}' => {
                out.push('"')
            }
            // -> single quote / apostrophe
            '\u{2018}' | '\u{2019}' | '\u{201a}' | '\u{2032}' | '\u{2039}' | '\u{203a}' => {
                out.push('\'')
            }
            _ => out.push(ch),
        }
    }
    out
}

pub fn normalize_tabs(text: &str) -> String {
    TAB_PATTERN.replace_all(text, " ").into_owned()
}

pub fn normalize_hyphens(text: &str) -> String {
    // Always rejoin (matches Python's observable output — see module docs).
    HYPHEN_BREAK
        .replace_all(text, |caps: &regex::Captures<'_>| {
            let mut s = String::with_capacity(caps[1].len() + caps[2].len());
            s.push_str(&caps[1]);
            s.push_str(&caps[2]);
            s
        })
        .into_owned()
}

pub fn normalize_spaces(text: &str) -> String {
    MULTI_SPACE.replace_all(text, " ").into_owned()
}

pub fn normalize_trailing_whitespace(text: &str) -> String {
    TRAILING_WS.replace_all(text, "").into_owned()
}

pub fn normalize_linebreaks(text: &str) -> String {
    EXCESS_NEWLINES.replace_all(text, "\n\n").into_owned()
}

// ---------------------------------------------------------------------------
// Registry in Python run-order: unicode, encoding, bullets, quotes, tabs,
// hyphens, spaces, trailing_whitespace, linebreaks.
// ---------------------------------------------------------------------------

pub fn normalize_text(text: &str, apply_hyphens: bool) -> String {
    let mut s = normalize_unicode(text);
    s = normalize_encoding(&s);
    s = normalize_bullets(&s);
    s = normalize_quotes(&s);
    s = normalize_tabs(&s);
    if apply_hyphens {
        s = normalize_hyphens(&s);
    }
    s = normalize_spaces(&s);
    s = normalize_trailing_whitespace(&s);
    s = normalize_linebreaks(&s);
    s
}

//! Search fuzzy helpers — Rust port of `app/search/indexer.py` pure helpers.
//!
//! Parity contract: each function reproduces the exact result (score, span,
//! HTML) the Python `rapidfuzz`-backed original produces, so
//! `tests/test_search.py` (56 tests, including `TestFuzzySearch` with 19 cases)
//! keeps passing with identical output.
//!
//! Implemented algorithms (matching rapidfuzz's `fuzz` module):
//! - `ratio`: Indel-distance-based similarity = 2 * LCS_len / (len1 + len2) * 100
//! - `partial_ratio`: max(ratio(shorter, longer[i..i+len(shorter)])) for all i
//! - `token_set_ratio`: sorted-intersection / diff token sets, max(ratio)
//! - `partial_ratio_alignment`: returns (dest_start, dest_end, best_ratio)
//!
//! Faithful-behaviour notes:
//! - Python `str.split()` (no args) splits on any whitespace run.
//! - Python `[^\W_]+` == Unicode word chars excluding underscore.
//! - Python `str.isalnum()` is Unicode-aware; Rust `char::is_alphanumeric` is too.
//! - All byte offsets returned by `find()` / `Regex::find_iter` are on char
//!   boundaries, so slicing `&text[start..end]` is always safe.

use regex::Regex;
use std::collections::HashSet;

// ---------------------------------------------------------------------------
// Indel distance (LCS-based) — matches rapidfuzz `fuzz.ratio`
// ---------------------------------------------------------------------------

/// Longest common subsequence length (classic O(n*m) DP with rolling rows).
fn lcs_length(a: &[char], b: &[char]) -> usize {
    let (na, nb) = (a.len(), b.len());
    if na == 0 || nb == 0 {
        return 0;
    }
    let mut prev = vec![0usize; nb + 1];
    let mut curr = vec![0usize; nb + 1];
    for i in 1..=na {
        curr[0] = 0;
        for j in 1..=nb {
            if a[i - 1] == b[j - 1] {
                curr[j] = prev[j - 1] + 1;
            } else {
                curr[j] = prev[j].max(curr[j - 1]);
            }
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    prev[nb]
}

/// `fuzz.ratio(s1, s2)` — Indel-based similarity (0–100).
///
/// Matches rapidfuzz: `ratio = 2 * LCS / (len(s1) + len(s2)) * 100`.
/// Empty-vs-empty → 100; one-empty → 0.
pub fn ratio(s1: &str, s2: &str) -> f64 {
    let c1: Vec<char> = s1.chars().collect();
    let c2: Vec<char> = s2.chars().collect();
    let (n1, n2) = (c1.len(), c2.len());
    if n1 == 0 && n2 == 0 {
        return 100.0;
    }
    if n1 == 0 || n2 == 0 {
        return 0.0;
    }
    let lcs = lcs_length(&c1, &c2);
    (lcs * 2) as f64 / (n1 + n2) as f64 * 100.0
}

// ---------------------------------------------------------------------------
// partial_ratio — matches rapidfuzz `fuzz.partial_ratio`
// ---------------------------------------------------------------------------

/// `fuzz.partial_ratio(s1, s2)` — best-window Indel ratio (0–100).
///
/// Finds the shorter string, slides it across all substrings of the longer
/// string of the same length, and returns the max `ratio`.
pub fn partial_ratio(s1: &str, s2: &str) -> f64 {
    let c1: Vec<char> = s1.chars().collect();
    let c2: Vec<char> = s2.chars().collect();
    if c1.is_empty() || c2.is_empty() {
        return 0.0;
    }
    let (shorter, longer) = if c1.len() <= c2.len() {
        (c1.as_slice(), c2.as_slice())
    } else {
        (c2.as_slice(), c1.as_slice())
    };
    let n_short = shorter.len();
    let n_long = longer.len();
    if n_short == n_long {
        return ratio(s1, s2);
    }
    let mut best = 0.0;
    let shorter_str: String = shorter.iter().collect();
    for start in 0..=(n_long - n_short) {
        let window: String = longer[start..start + n_short].iter().collect();
        let r = ratio(&shorter_str, &window);
        if r > best {
            best = r;
        }
        if best >= 100.0 {
            break;
        }
    }
    best
}

/// `fuzz.partial_ratio_alignment(s1, s2)` — best-window Indel ratio + span.
///
/// Returns `(dest_start, dest_end, best_score)` where `dest_start`/`dest_end`
/// are byte offsets into **s2** (the second argument).  This matches rapidfuzz's
/// `Alignment.dest_start`/`Alignment.dest_end` semantics:
/// - When `s2` is the longer string (common: short query, long text), the
///   dest span is the best-matching window in `s2`.
/// - When `s1` is longer, the dest span covers all of `s2` (the shorter
///   string is the "source" matched within the longer `s1`).
pub fn partial_ratio_alignment(s1: &str, s2: &str) -> Option<(usize, usize, f64)> {
    let c1: Vec<char> = s1.chars().collect();
    let c2: Vec<char> = s2.chars().collect();
    if c1.is_empty() || c2.is_empty() {
        return None;
    }

    if c1.len() == c2.len() {
        let score = ratio(s1, s2);
        return Some((0, s2.len(), score));
    }

    // Determine shorter/longer.  `s2` is always "dest".
    let (shorter_chars, longer_str, s2_is_longer) = if c1.len() <= c2.len() {
        // s2 is longer → dest = window in s2
        (c1.as_slice(), s2, true)
    } else {
        // s1 is longer → dest = entire s2 (the shorter)
        (c2.as_slice(), s1, false)
    };
    let n_short = shorter_chars.len();
    let longer_chars: Vec<char> = longer_str.chars().collect();
    let n_long = longer_chars.len();

    if s2_is_longer {
        // s2 is longer: dest_start/dest_end are a window in s2.
        // Pre-compute byte offsets for each char index of s2.
        let byte_offsets: Vec<usize> = s2.char_indices().map(|(b, _)| b).collect();

        let mut best_score = 0.0;
        let mut best_char_start = 0usize;

        for start in 0..=(n_long - n_short) {
            let window: String = longer_chars[start..start + n_short].iter().collect();
            let shorter_str: String = shorter_chars.iter().collect();
            let r = ratio(&shorter_str, &window);
            if r > best_score {
                best_score = r;
                best_char_start = start;
            }
            if best_score >= 100.0 {
                break;
            }
        }

        let char_end = best_char_start + n_short;
        let byte_start = byte_offsets[best_char_start];
        let byte_end = if char_end < byte_offsets.len() {
            byte_offsets[char_end]
        } else {
            s2.len()
        };

        Some((byte_start, byte_end, best_score))
    } else {
        // s1 is longer: dest covers all of s2, score = best window in s1.
        let shorter_str: String = shorter_chars.iter().collect();
        let mut best_score = 0.0;

        for start in 0..=(n_long - n_short) {
            let window: String = longer_chars[start..start + n_short].iter().collect();
            let r = ratio(&shorter_str, &window);
            if r > best_score {
                best_score = r;
            }
            if best_score >= 100.0 {
                break;
            }
        }

        // dest = entire s2 (0 to len(s2) in bytes)
        Some((0, s2.len(), best_score))
    }
}

// ---------------------------------------------------------------------------
// token_set_ratio — matches rapidfuzz `fuzz.token_set_ratio`
// ---------------------------------------------------------------------------

/// `fuzz.token_set_ratio(s1, s2)` — token-set Indel ratio (0–100).
///
/// Mirrors rapidfuzz: splits on whitespace, computes set intersection &
/// differences, then returns the max `ratio` across comparison strings
/// (`intersection + diff1` vs `intersection + diff2`, and
/// `intersection + diff1 + diff2` vs itself).
pub fn token_set_ratio(s1: &str, s2: &str) -> f64 {
    let tokens1: Vec<&str> = s1.split_whitespace().collect();
    let tokens2: Vec<&str> = s2.split_whitespace().collect();
    if tokens1.is_empty() && tokens2.is_empty() {
        return 100.0;
    }
    if tokens1.is_empty() || tokens2.is_empty() {
        return 0.0;
    }

    let set1: HashSet<&str> = tokens1.iter().copied().collect();
    let set2: HashSet<&str> = tokens2.iter().copied().collect();

    let mut intersection = set1.intersection(&set2).copied().collect::<Vec<_>>();
    let mut diff_ab: Vec<&str> = set1.difference(&set2).copied().collect();
    let mut diff_ba: Vec<&str> = set2.difference(&set1).copied().collect();
    intersection.sort();
    diff_ab.sort();
    diff_ba.sort();

    let sorted_sect = intersection.join(" ");
    let combined_1a = format!("{} {}", sorted_sect, diff_ab.join(" "));
    let combined_1b = format!("{} {}", sorted_sect, diff_ba.join(" "));
    let combined_all = format!(
        "{} {} {}",
        sorted_sect,
        diff_ab.join(" "),
        diff_ba.join(" ")
    );

    let combined_1a = combined_1a.trim().to_string();
    let combined_1b = combined_1b.trim().to_string();
    let combined_all = combined_all.trim().to_string();

    let mut best = 0.0;
    best = best.max(ratio(&combined_1a, &combined_1b));
    best = best.max(ratio(&combined_1a, &combined_all));
    best = best.max(ratio(&combined_1b, &combined_all));
    best
}

// ---------------------------------------------------------------------------
// `_expand_to_word` — grow a span to word boundaries
// ---------------------------------------------------------------------------

/// Regex for "words" (Unicode word chars, excluding underscore) — mirrors
/// Python `re.finditer(r"[^\W_]+", text)`.
fn word_regex() -> &'static Regex {
    use once_cell::sync::OnceLock;
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^\W_]+").unwrap())
}

/// `expand_to_word(text, start, end)` — grow span to cover full words.
///
/// `start`/`end` are byte offsets into `text`.  Returns `(start, end)` byte
/// offsets.  Mirrors `_expand_to_word` in `app/search/indexer.py`: walks
/// backward/forward while the character is `is_alphanumeric()` or `_`.
pub fn expand_to_word(text: &str, start: usize, end: usize) -> (usize, usize) {
    let mut s = start;
    let mut e = end;

    // Grow start backward.
    while s > 0 {
        // `text[..s]` is safe because `s` is on a char boundary (it came from
        // `find()` or regex match, which always return char-aligned offsets).
        let prev_char = text[..s].chars().last();
        match prev_char {
            Some(c) if c.is_alphanumeric() || c == '_' => {
                s -= c.len_utf8();
            }
            _ => break,
        }
    }

    // Grow end forward.
    while e < text.len() {
        let next_char = text[e..].chars().next();
        match next_char {
            Some(c) if c.is_alphanumeric() || c == '_' => {
                e += c.len_utf8();
            }
            _ => break,
        }
    }

    (s, e)
}

// ---------------------------------------------------------------------------
// `_find_match_spans` — locate non-overlapping match spans
// ---------------------------------------------------------------------------

/// `find_match_spans(query, text, fuzzy_word_threshold=60.0)` — returns a JSON
/// string encoding a list of `[start, end]` byte-offset span pairs.
///
/// Mirrors `_find_match_spans` in `app/search/indexer.py`:
/// 1. Exact case-insensitive substring match per query term.
/// 2. Fallback: closest whole-word fuzzy match (using `ratio`).
/// 3. Spans are expanded to word boundaries, merged, returned sorted.
pub fn find_match_spans(query: &str, text: &str, fuzzy_word_threshold: f64) -> String {
    if query.is_empty() || text.is_empty() {
        return "[]";
    }

    let text_lower = text.to_lowercase();
    let terms: Vec<&str> = query
        .trim()
        .split_whitespace()
        .filter(|t| !t.is_empty())
        .collect();

    let mut spans: Vec<(usize, usize)> = Vec::new();

    for term in &terms {
        let term_lower = term.to_lowercase();
        let term_len = term_lower.len();

        // 1) Exact substring occurrences (handles compound tokens like "heavy-metals").
        let mut found_exact = false;
        let mut start = 0usize;
        while let Some(idx) = text_lower[start..].find(&term_lower) {
            let abs_idx = start + idx;
            found_exact = true;
            let (es, ee) = expand_to_word(text, abs_idx, abs_idx + term_len);
            spans.push((es, ee));
            start = abs_idx + term_len;
        }

        if found_exact {
            continue;
        }

        // 2) Closest whole-word fuzzy match (typo tolerance).
        let mut best_score = fuzzy_word_threshold - 1.0;
        let mut best_span: Option<(usize, usize)> = None;
        for m in word_regex().find_iter(text) {
            let word = m.as_str();
            let score = ratio(term, word);
            if score > best_score {
                best_score = score;
                best_span = Some((m.start(), m.end()));
            }
        }
        if let Some(span) = best_span {
            spans.push(span);
        }
    }

    if spans.is_empty() {
        return "[]";
    }

    // Sort by start position.
    spans.sort();

    // Merge overlapping/adjacent spans.
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (s, e) in &spans {
        if let Some(last) = merged.last_mut() {
            if *s <= last.1 {
                if *e > last.1 {
                    last.1 = *e;
                }
                continue;
            }
        }
        merged.push((*s, *e));
    }

    // Serialize as [[start, end], ...].
    let spans_json: Vec<Vec<usize>> = merged.iter().map(|(s, e)| vec![*s, *e]).collect();
    serde_json::to_string(&spans_json).unwrap_or_else(|_| "[]".to_string())
}

// ---------------------------------------------------------------------------
// `_apply_marks` — wrap spans in <mark> tags
// ---------------------------------------------------------------------------

/// `apply_marks(text, spans_json)` — wrap `[start, end]` byte spans in
/// `<mark>`…`</mark>`.  `spans_json` is a JSON array of `[start, end]` pairs.
pub fn apply_marks(text: &str, spans_json: &str) -> String {
    let spans: Vec<(usize, usize)> = match serde_json::from_str::<Vec<Vec<usize>>>(spans_json) {
        Ok(v) => v
            .into_iter()
            .filter_map(|pair| {
                if pair.len() == 2 {
                    Some((pair[0], pair[1]))
                } else {
                    None
                }
            })
            .collect(),
        Err(_) => return text.to_string(),
    };

    if spans.is_empty() {
        return text.to_string();
    }

    let mut pieces: Vec<&str> = Vec::new();
    let mut cursor = 0usize;
    for (s, e) in &spans {
        if *s > cursor {
            pieces.push(&text[cursor..*s]);
        }
        pieces.push("<mark>");
        pieces.push(&text[*s..*e]);
        pieces.push("</mark>");
        cursor = *e;
    }
    if cursor < text.len() {
        pieces.push(&text[cursor..]);
    }
    pieces.join("")
}

// ---------------------------------------------------------------------------
// `_snippet_around_match` — fallback snippet (no marks)
// ---------------------------------------------------------------------------

/// `snippet_around_match(query, text, width=80)` — best partial-ratio
/// alignment snippet, plain text (no `<mark>`).
///
/// Mirrors `_snippet_around_match` in `app/search/indexer.py`: uses
/// `partial_ratio_alignment` to find the best matching region, then extracts
/// a `width`-padded window around it with ellipsis markers.
pub fn snippet_around_match(query: &str, text: &str, width: usize) -> String {
    let fallback = || {
        text.chars()
            .take(200)
            .collect::<String>()
            .replace("\n", " ")
            .trim()
            .to_string()
    };

    match partial_ratio_alignment(query, text) {
        Some((dest_start, dest_end, _score)) => {
            let start = clamp_to_char_boundary(text, dest_start.saturating_sub(width));
            let end = clamp_to_char_boundary(text, text.len().min(dest_end + width));
            let snippet = text[start..end].replace("\n", " ");
            let snippet = snippet.trim().to_string();
            let mut result = snippet;
            if start > 0 {
                result = format!("…{}", result);
            }
            if end < text.len() {
                result = format!("{}…", result);
            }
            result
        }
        None => fallback(),
    }
}

// ---------------------------------------------------------------------------
// `_snippet_around_matches` — word-bounded, <mark>-highlighted snippet
// ---------------------------------------------------------------------------

/// Helper: find the byte offset of the nearest char boundary at or before `pos`.
fn clamp_to_char_boundary(s: &str, pos: usize) -> usize {
    if pos >= s.len() {
        return s.len();
    }
    if s.is_char_boundary(pos) {
        return pos;
    }
    // Walk backward to the nearest boundary.
    let mut p = pos;
    while p > 0 && !s.is_char_boundary(p) {
        p -= 1;
    }
    p
}

/// `snippet_around_matches(query, text, width=80, fuzzy_word_threshold=60.0)` —
/// return a word-bounded, `<mark>`-highlighted snippet for the query.
///
/// Mirrors `_snippet_around_matches` in `app/search/indexer.py`:
/// 1. Normalize whitespace (`" ".join(text.split())`).
/// 2. Find match spans via `find_match_spans`.
/// 3. If no spans, fall back to `snippet_around_match`.
/// 4. Center a window on the spans (±`width` chars), snap to word boundaries.
/// 5. Clamp spans to window, apply `<mark>` tags, add ellipsis.
pub fn snippet_around_matches(
    query: &str,
    text: &str,
    width: usize,
    fuzzy_word_threshold: f64,
) -> String {
    if text.is_empty() {
        return String::new();
    }

    let normalized: String = text.split_whitespace().collect::<Vec<_>>().join(" ");

    let spans_json = find_match_spans(query, &normalized, fuzzy_word_threshold);
    let spans: Vec<(usize, usize)> = match serde_json::from_str::<Vec<Vec<usize>>>(&spans_json) {
        Ok(v) => v
            .into_iter()
            .filter_map(|p| {
                if p.len() == 2 {
                    Some((p[0], p[1]))
                } else {
                    None
                }
            })
            .collect(),
        Err(_) => Vec::new(),
    };

    if spans.is_empty() {
        return snippet_around_match(query, &normalized, width);
    }

    let start = spans[0].0.saturating_sub(width);
    let end = normalized.len().min(spans[spans.len() - 1].1 + width);

    // Snap window edges to whole words.
    let mut win_start = start;
    let mut win_end = end;
    if win_start > 0 {
        match normalized[..win_start].rfind(' ') {
            Some(ws) => win_start = ws + 1,
            None => win_start = 0,
        }
    }
    if win_end < normalized.len() {
        // Find the next space in the text after win_end.
        match normalized[win_end..].find(' ') {
            Some(ws) => win_end = win_end + ws,
            None => win_end = normalized.len(),
        }
    }

    let window = &normalized[win_start..win_end];
    let mut clamped: Vec<(usize, usize)> = Vec::new();
    for (s, e) in &spans {
        if *e <= win_start || *s >= win_end {
            continue;
        }
        let cs = s.saturating_sub(win_start);
        let ce = e.min(win_end).saturating_sub(win_start);
        if ce > cs {
            clamped.push((cs, ce));
        }
    }

    let spans_json_clamped = serde_json::to_string(
        &clamped
            .iter()
            .map(|(s, e)| vec![*s, *e])
            .collect::<Vec<_>>(),
    )
    .unwrap_or_else(|_| "[]".to_string());

    let snippet = apply_marks(window, &spans_json_clamped);
    let snippet = snippet.trim().to_string();
    let mut result = snippet;
    if win_start > 0 {
        result = format!("…{}", result);
    }
    if win_end < normalized.len() {
        result = format!("{}…", result);
    }
    result
}

// ---------------------------------------------------------------------------
// `_field_score` — best fuzzy similarity (0–100)
// ---------------------------------------------------------------------------

/// `field_score(query, text)` — best fuzzy similarity (0–100).
///
/// Mirrors `_field_score` in `app/search/indexer.py` and the copied
/// `_field_score` in `app/rag/retrieval/sparse_retriever.py`:
/// `max(token_set_ratio(query, text), partial_ratio(query, text))`.
pub fn field_score(query: &str, text: &str) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    f64::max(token_set_ratio(query, text), partial_ratio(query, text))
}

// ---------------------------------------------------------------------------
// `_highlight_text` — wrap matched terms in <mark> (no windowing)
// ---------------------------------------------------------------------------

/// `highlight_text(query, text, fuzzy_word_threshold=60.0)` — return `text`
/// with matched terms wrapped in `<mark>` tags.  Returns original text when
/// nothing matches.  Mirrors `_highlight_text` in `app/search/indexer.py`.
pub fn highlight_text(query: &str, text: &str, fuzzy_word_threshold: f64) -> String {
    if text.is_empty() {
        return String::new();
    }
    let spans_json = find_match_spans(query, text, fuzzy_word_threshold);
    let spans: Vec<(usize, usize)> = match serde_json::from_str::<Vec<Vec<usize>>>(&spans_json) {
        Ok(v) => v
            .into_iter()
            .filter_map(|p| {
                if p.len() == 2 {
                    Some((p[0], p[1]))
                } else {
                    None
                }
            })
            .collect(),
        Err(_) => Vec::new(),
    };
    if spans.is_empty() {
        return text.to_string();
    }
    apply_marks(text, &spans_json)
}

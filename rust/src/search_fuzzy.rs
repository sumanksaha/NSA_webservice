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

/// Indel edit distance between two char slices (`len(a) + len(b) - 2 * LCS`).
fn indel_distance(a: &[char], b: &[char]) -> usize {
    a.len() + b.len() - 2 * lcs_length(a, b)
}

/// Normalized Indel similarity from a distance and length sum (0–100).
///
/// Matches rapidfuzz's `_norm_distance`: `100 - 100 * dist / lensum`
/// (100 when the length sum is 0).
fn norm_distance(dist: usize, lensum: usize) -> f64 {
    if lensum == 0 {
        return 100.0;
    }
    100.0 - 100.0 * dist as f64 / lensum as f64
}

/// Indel similarity of two char slices (0-100), the core of `fuzz.ratio`.
fn similarity_chars(a: &[char], b: &[char]) -> f64 {
    let (n1, n2) = (a.len(), b.len());
    if n1 == 0 && n2 == 0 {
        return 100.0;
    }
    if n1 == 0 || n2 == 0 {
        return 0.0;
    }
    norm_distance(indel_distance(a, b), n1 + n2)
}

// ---------------------------------------------------------------------------
// partial_ratio — matches rapidfuzz `fuzz.partial_ratio` (short needle)
// ---------------------------------------------------------------------------

/// Core of `partial_ratio`: best alignment of `shorter` inside `longer`.
///
/// Mirrors rapidfuzz's short-needle (`len ≤ 64`) implementation exactly —
/// every alignment family is scored, not just fixed-length windows:
/// 1. prefixes `longer[..i]` for `1 ≤ i < len(shorter)`,
/// 2. fixed windows `longer[i..i+len(shorter)]`,
/// 3. suffixes `longer[i..]`.
/// A candidate is skipped unless its boundary char occurs in `shorter`
/// (the charset pre-filter).  Returns `(score, char_start, char_end)` with
/// the span in `longer` coords.  Caller guarantees `shorter` is non-empty
/// and `len(shorter) <= len(longer)`.
fn partial_impl(shorter: &[char], longer: &[char]) -> (f64, usize, usize) {
    let n_short = shorter.len();
    let n_long = longer.len();
    let charset: HashSet<char> = shorter.iter().copied().collect();

    let mut best: f64 = 0.0;
    let mut best_start: usize = 0;
    let mut best_end: usize = n_short.min(n_long);

    // 1) Prefixes longer[..i].
    for i in 1..n_short {
        if !charset.contains(&longer[i - 1]) {
            continue;
        }
        let r = similarity_chars(shorter, &longer[..i]);
        if r > best {
            best = r;
            best_start = 0;
            best_end = i;
        }
        if best >= 100.0 {
            return (best, best_start, best_end);
        }
    }

    // 2) Fixed windows longer[i..i + n_short].
    for i in 0..(n_long - n_short) {
        if !charset.contains(&longer[i + n_short - 1]) {
            continue;
        }
        let r = similarity_chars(shorter, &longer[i..i + n_short]);
        if r > best {
            best = r;
            best_start = i;
            best_end = i + n_short;
        }
        if best >= 100.0 {
            return (best, best_start, best_end);
        }
    }

    // 3) Suffixes longer[i..].
    for i in (n_long - n_short)..n_long {
        if !charset.contains(&longer[i]) {
            continue;
        }
        let r = similarity_chars(shorter, &longer[i..]);
        if r > best {
            best = r;
            best_start = i;
            best_end = n_long;
        }
        if best >= 100.0 {
            return (best, best_start, best_end);
        }
    }

    (best, best_start, best_end)
}

/// `fuzz.partial_ratio(s1, s2)` — best-alignment Indel ratio (0–100).
///
/// Optimal alignment of the shorter string inside the longer one.  When
/// both strings have equal length and the forward score is not perfect,
/// the reverse direction is also tried (mirrors rapidfuzz).  Both empty →
/// 100; one empty → 0.
pub fn partial_ratio(s1: &str, s2: &str) -> f64 {
    let c1: Vec<char> = s1.chars().collect();
    let c2: Vec<char> = s2.chars().collect();
    if c1.is_empty() && c2.is_empty() {
        return 100.0;
    }
    if c1.is_empty() || c2.is_empty() {
        return 0.0;
    }
    if c1.len() <= c2.len() {
        let (score, _, _) = partial_impl(&c1, &c2);
        if score != 100.0 && c1.len() == c2.len() {
            let (rev_score, _, _) = partial_impl(&c2, &c1);
            if rev_score > score {
                return rev_score;
            }
        }
        score
    } else {
        let (score, _, _) = partial_impl(&c2, &c1);
        score
    }
}

/// `fuzz.partial_ratio_alignment(s1, s2)` — best-alignment Indel ratio + span.
///
/// Returns `(dest_start, dest_end, best_score)` where `dest_start`/`dest_end`
/// are byte offsets into **s2** (the second argument).  This matches rapidfuzz's
/// `ScoreAlignment` semantics:
/// - When `s1` is shorter-or-equal, the dest span is the winning alignment
///   (prefix, window, or suffix) in `s2`.
/// - When `s1` is longer, the dest span covers all of `s2` (the winning
///   alignment lives in `s1`; only its score is reported).
/// - Equal length with an imperfect forward score also tries the reverse
///   direction and keeps the winner.
/// Both empty → `(0, 0, 100.0)`; one empty → `(0, 0, 0.0)`.
pub fn partial_ratio_alignment(s1: &str, s2: &str) -> Option<(usize, usize, f64)> {
    let c1: Vec<char> = s1.chars().collect();
    let c2: Vec<char> = s2.chars().collect();
    if c1.is_empty() && c2.is_empty() {
        return Some((0, 0, 100.0));
    }
    if c1.is_empty() || c2.is_empty() {
        return Some((0, 0, 0.0));
    }

    // Byte offset of each char index in s2 (plus the end sentinel).
    let mut byte_offsets: Vec<usize> = s2.char_indices().map(|(b, _)| b).collect();
    byte_offsets.push(s2.len());
    let to_bytes = |char_start: usize, char_end: usize| -> (usize, usize) {
        (byte_offsets[char_start], byte_offsets[char_end])
    };

    if c1.len() <= c2.len() {
        let (score, cs, ce) = partial_impl(&c1, &c2);
        if score != 100.0 && c1.len() == c2.len() {
            let (rev_score, _, _) = partial_impl(&c2, &c1);
            if rev_score > score {
                return Some((0, s2.len(), rev_score));
            }
        }
        let (bs, be) = to_bytes(cs, ce);
        Some((bs, be, score))
    } else {
        // s1 is longer: dest covers all of s2, score from impl(s2, s1).
        let (score, _, _) = partial_impl(&c2, &c1);
        Some((0, s2.len(), score))
    }
}

// ---------------------------------------------------------------------------
// token_set_ratio — matches rapidfuzz `fuzz.token_set_ratio`
// ---------------------------------------------------------------------------

/// `fuzz.token_set_ratio(s1, s2)` — token-set Indel ratio (0–100).
///
/// Mirrors rapidfuzz exactly: tokenless either side → 0; a non-empty
/// intersection subsuming one side → 100; otherwise the max of the
/// length-normalized Indel distance between the sorted difference strings
/// (normalized by the sector-adjusted length sum) and the two
/// sector-vs-sector+diff ratios.
pub fn token_set_ratio(s1: &str, s2: &str) -> f64 {
    let tokens1: Vec<&str> = s1.split_whitespace().collect();
    let tokens2: Vec<&str> = s2.split_whitespace().collect();
    // FuzzyWuzzy compat: either side tokenless → 0 (even both-empty).
    if tokens1.is_empty() || tokens2.is_empty() {
        return 0.0;
    }

    let set1: HashSet<&str> = tokens1.iter().copied().collect();
    let set2: HashSet<&str> = tokens2.iter().copied().collect();

    let intersection = set1.intersection(&set2).copied().collect::<Vec<_>>();
    let mut diff_ab: Vec<&str> = set1.difference(&set2).copied().collect();
    let mut diff_ba: Vec<&str> = set2.difference(&set1).copied().collect();
    diff_ab.sort();
    diff_ba.sort();

    // One token set subsumes the other → perfect score (FuzzyWuzzy compat).
    if !intersection.is_empty() && (diff_ab.is_empty() || diff_ba.is_empty()) {
        return 100.0;
    }

    let diff_ab_joined = diff_ab.join(" ");
    let diff_ba_joined = diff_ba.join(" ");
    let ab_len: usize = diff_ab_joined.chars().count();
    let ba_len: usize = diff_ba_joined.chars().count();
    // Length of the space-joined intersection (order-independent).
    let sect_len: usize = intersection.iter().map(|t| t.chars().count()).sum::<usize>()
        + intersection.len().saturating_sub(1);
    let sect_ab_len = sect_len + usize::from(sect_len != 0) + ab_len;
    let sect_ba_len = sect_len + usize::from(sect_len != 0) + ba_len;

    let ab_chars: Vec<char> = diff_ab_joined.chars().collect();
    let ba_chars: Vec<char> = diff_ba_joined.chars().collect();
    let mut best = norm_distance(
        indel_distance(&ab_chars, &ba_chars),
        sect_ab_len + sect_ba_len,
    );
    best = best.max(norm_distance(
        usize::from(sect_len != 0) + ab_len,
        sect_len + sect_ab_len,
    ));
    best = best.max(norm_distance(
        usize::from(sect_len != 0) + ba_len,
        sect_len + sect_ba_len,
    ));
    best
}

// ---------------------------------------------------------------------------
// `_expand_to_word` — grow a span to word boundaries
// ---------------------------------------------------------------------------

/// Regex for "words" (Unicode word chars, excluding underscore) — mirrors
/// Python `re.finditer(r"[^\W_]+", text)`.
fn word_regex() -> &'static Regex {
    use once_cell::sync::Lazy;
    static RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^\W_]+").unwrap());
    &RE
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
        return "[]".to_string();
    }

    let text_lower = text.to_lowercase();
    let terms: Vec<&str> = query.split_whitespace().filter(|t| !t.is_empty()).collect();

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
        return "[]".to_string();
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
/// Forgiving slice semantics mirror `_apply_marks` (see body comments).
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
    // Mirror Python slice semantics: out-of-range indices clamp (slicing
    // never raises), and a reversed/empty span still emits an empty
    // `<mark></mark>` pair while moving the cursor.  Indices snap back to
    // the nearest char boundary so multi-byte text can never panic.
    for (s, e) in &spans {
        let cs = clamp_to_char_boundary(text, (*s).min(text.len()));
        let ce = clamp_to_char_boundary(text, (*e).min(text.len()));
        if cs > cursor {
            pieces.push(&text[cursor..cs]);
        }
        pieces.push("<mark>");
        pieces.push(if ce > cs { &text[cs..ce] } else { "" });
        pieces.push("</mark>");
        cursor = ce;
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
            Some(ws) => win_end += ws,
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
        let ce = e.min(&win_end).saturating_sub(win_start);
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

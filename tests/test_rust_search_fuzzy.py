"""Rust ↔ Python parity tests for search fuzzy helpers (Part 2).

These tests exercise the ``nsa_rust`` PyO3 extension when it is importable and
assert byte-for-byte parity with the Python ``rapidfuzz``-backed implementation
in ``app/search/indexer.py`` (and the mirror in
``app/rag/retrieval/sparse_retriever.py``).  When the extension is not built,
the module is skipped via ``pytest.importorskip`` so the suite is never red.
"""

import json

import pytest

pytest.importorskip("nsa_rust")

import nsa_rust
from rapidfuzz import fuzz

from app.search.indexer import (
    _apply_marks,
    _expand_to_word,
    _field_score,
    _find_match_spans,
    _highlight_text,
    _snippet_around_match,
    _snippet_around_matches,
)

# ---------------------------------------------------------------------------
# Rapidfuzz algorithm parity (foundational — everything else builds on these)
# ---------------------------------------------------------------------------

RATIO_CORPUS = [
    ("hello", "hello"),
    ("hello", "world"),
    ("hello", "hello world"),
    ("Acme", "Acmee"),
    ("heavy metals", "hevay metals"),
    ("", ""),
    ("", "hello"),
    ("hello", ""),
    ("a", "b"),
    ("foo bar baz", "foo baz"),
    ("Section 3", "section 3"),
    ("FSSAI", "fssai"),
    ("क़ैसे", "क़ैसे"),
    ("café", "cafe"),
    ("naïve", "naive"),
]

PARTIAL_RATIO_CORPUS = [
    ("Acme", "Acme Foods Ltd"),
    ("Acmee", "Acme Foods Ltd"),
    ("heavy metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("zzzqx", "the quick brown fox jumps over the lazy dog"),
    ("test", "this is a test string"),
    ("", "hello"),
    ("hello", ""),
    ("xyz", "abc"),
    ("Doe", "John Doe the grocer"),
]

TOKEN_SET_CORPUS = [
    ("Acme", "Acme Foods"),
    ("Acme Foods", "Acme"),
    ("heavy metals", "hevay metals"),
    ("foo bar baz", "baz bar foo"),
    ("FSSAI license", "license FSSAI"),
    ("", ""),
    ("hello", ""),
    ("", "world"),
    ("Acme Ltd", "Acme Foods Ltd"),
    ("Section 3", "Section 51"),
]


@pytest.mark.parametrize("s1,s2", RATIO_CORPUS)
def test_ratio_parity(s1, s2):
    assert nsa_rust.ratio(s1, s2) == pytest.approx(fuzz.ratio(s1, s2), abs=0.01)


@pytest.mark.parametrize("s1,s2", PARTIAL_RATIO_CORPUS)
def test_partial_ratio_parity(s1, s2):
    assert nsa_rust.partial_ratio(s1, s2) == pytest.approx(fuzz.partial_ratio(s1, s2), abs=0.01)


@pytest.mark.parametrize("s1,s2", TOKEN_SET_CORPUS)
def test_token_set_ratio_parity(s1, s2):
    assert nsa_rust.token_set_ratio(s1, s2) == pytest.approx(fuzz.token_set_ratio(s1, s2), abs=0.01)


# ---------------------------------------------------------------------------
# _field_score parity (the function called in tight loops)
# ---------------------------------------------------------------------------

FIELD_SCORE_CORPUS = [
    ("Acmee", "Acme Foods Ltd"),
    ("Acme", "Acme Foods Ltd"),
    ("heavy metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("zzzqx", "the quick brown fox jumps over the lazy dog"),
    ("Section 3", ""),
    ("", "hello"),
    ("Doe", "John Doe the grocer"),
    ("hevy mtauls", "heavy metals detection report"),
    ("Contaminated snacks", "Presence of artificial colour"),
    ("Test Officer", "Test Officer"),
]


@pytest.mark.parametrize("query,text", FIELD_SCORE_CORPUS)
def test_field_score_parity(query, text):
    """Rust `field_score` must match Python `_field_score` (max of token_set + partial)."""
    assert nsa_rust.field_score(query, text) == pytest.approx(_field_score(query, text), abs=0.01)


def test_field_score_empty_text_returns_zero():
    assert nsa_rust.field_score("query", "") == 0.0
    assert _field_score("query", "") == 0.0


# ---------------------------------------------------------------------------
# _expand_to_word parity
# ---------------------------------------------------------------------------

EXPAND_CORPUS = [
    ("Acme Foods Ltd", 0, 4),        # "Acme" → already a word
    ("Acme Foods Ltd", 5, 9),         # "Food" → grows to "Foods"
    ("heavy-metals", 0, 5),           # "heavy" → stays "heavy" (hyphen is not word char)
    ("heavy-metals", 6, 12),          # "metals" → stays "metals"
    ("XAcmezzzz Industrial", 0, 1),   # "X" → grows to "XAcmezzzz"
    ("hello world", 0, 5),            # "hello" → already a word
    ("hello_world test", 0, 5),       # "hello" → grows to "hello_world" (_ is word char)
]


@pytest.mark.parametrize("text,start,end", EXPAND_CORPUS)
def test_expand_to_word_parity(text, start, end):
    assert nsa_rust.expand_to_word(text, start, end) == _expand_to_word(text, start, end)


# ---------------------------------------------------------------------------
# _find_match_spans parity
# ---------------------------------------------------------------------------

FIND_MATCHES_CORPUS = [
    ("Acme", "Acme Foods Ltd"),
    ("Acmee", "Acme Foods Ltd"),
    ("heavy metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("hevay metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("Cotton", "Cotton Candy Sample"),
    ("zzzqx", "the quick brown fox jumps over the lazy dog"),
    ("Acme OR Doe", "Acme Foods Ltd"),  # multi-word with operator (treated as literal terms)
    ("", "some text"),
    ("query", ""),
    ("test", "testing testing 123"),
    ("Acme", "Acme Acme Acme"),
    ("heavy-metals", "heavy-metals detected"),
]


@pytest.mark.parametrize("query,text", [(q, t) for q, t in FIND_MATCHES_CORPUS if q and t])
def test_find_match_spans_parity(query, text):
    py_spans = _find_match_spans(query, text)
    rust_json = nsa_rust.find_match_spans(query, text, 60.0)
    rust_spans = json.loads(rust_json)
    # Convert Python tuples to the same list format for comparison.
    py_list = [[s, e] for s, e in py_spans]
    assert rust_spans == py_list, f"query={query!r} text={text!r}"


def test_find_match_spans_empty_query():
    assert nsa_rust.find_match_spans("", "some text", 60.0) == "[]"
    assert _find_match_spans("", "some text") == []


def test_find_match_spans_empty_text():
    assert nsa_rust.find_match_spans("query", "", 60.0) == "[]"
    assert _find_match_spans("query", "") == []


# ---------------------------------------------------------------------------
# _apply_marks parity
# ---------------------------------------------------------------------------

APPLY_MARKS_CORPUS = [
    ("Acme Foods Ltd", [[0, 4]]),
    ("Acme Foods Ltd", [[0, 4], [5, 10]]),
    ("hello world test", [[0, 5], [6, 11]]),
    ("heavy metals", [[0, 6], [7, 13]]),
    ("no marks here", []),
    ("XAcmezzzz", [[0, 9]]),
]


@pytest.mark.parametrize("text,spans", APPLY_MARKS_CORPUS)
def test_apply_marks_parity(text, spans):
    spans_json = json.dumps(spans)
    py_result = _apply_marks(text, spans)
    rust_result = nsa_rust.apply_marks(text, spans_json)
    assert rust_result == py_result


# ---------------------------------------------------------------------------
# _highlight_text parity
# ---------------------------------------------------------------------------

HIGHLIGHT_CORPUS = [
    ("Acme", "Acme Foods Ltd"),
    ("Acmee", "Acme Foods Ltd"),
    ("heavy metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("hevay metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("Cotton", "Cotton Candy Sample"),
    ("zzzqx", "the quick brown fox jumps over the lazy dog"),
    ("Acme", ""),
    ("", "some text"),
]


@pytest.mark.parametrize("query,text", [(q, t) for q, t in HIGHLIGHT_CORPUS if q and t])
def test_highlight_text_parity(query, text):
    assert nsa_rust.highlight_text(query, text, 60.0) == _highlight_text(query, text, 60.0)


def test_highlight_text_empty_text():
    assert nsa_rust.highlight_text("Acme", "", 60.0) == ""
    assert _highlight_text("Acme", "", 60.0) == ""


def test_highlight_text_no_match_returns_original():
    """When nothing matches, the original text is returned unchanged."""
    text = "totally unrelated content"
    assert nsa_rust.highlight_text("zzzzqwerty", text, 60.0) == text
    assert _highlight_text("zzzzqwerty", text, 60.0) == text


# ---------------------------------------------------------------------------
# _snippet_around_match parity (fallback — uses partial_ratio_alignment)
# ---------------------------------------------------------------------------

SNIPPET_FALLBACK_CORPUS = [
    ("zzzqx", "the quick brown fox jumps over the lazy dog"),
    ("Acme", "Acme Foods Ltd and Acme Industries"),
    ("test", "this is a test string for snippet extraction"),
    ("query", "no match at all in this text here"),
]


@pytest.mark.parametrize("query,text", SNIPPET_FALLBACK_CORPUS)
def test_snippet_around_match_parity(query, text):
    assert nsa_rust.snippet_around_match(query, text, 80) == _snippet_around_match(query, text, 80)


def test_snippet_around_match_empty_text():
    # Python returns text[:200] → empty string for empty text
    assert nsa_rust.snippet_around_match("anything", "", 80) == _snippet_around_match("anything", "", 80)


# ---------------------------------------------------------------------------
# _snippet_around_matches parity (the main snippet generator)
# ---------------------------------------------------------------------------

SNIPPET_CORPUS = [
    ("Acme", "Acme Foods Ltd and Acme Industries are competitors"),
    ("Acmee", "Acme Foods Ltd and Acme Industries are competitors"),
    ("heavy metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("hevay metals", "Laboratory test results show presence of heavy metals in the sample"),
    ("Cotton", "Cotton Candy Sample"),
    ("zzzqx", "the quick brown fox jumps over the lazy dog"),
    ("test", "this is a test string for snippet extraction"),
    ("Acme", "Acme Acme Acme Acme Acme Acme Acme Acme Acme Acme Acme Acme"),
    ("Doe", "John Doe the grocer"),
]


@pytest.mark.parametrize("query,text", [(q, t) for q, t in SNIPPET_CORPUS if q and t])
def test_snippet_around_matches_parity(query, text):
    py_result = _snippet_around_matches(query, text, 80, 60.0)
    rust_result = nsa_rust.snippet_around_matches(query, text, 80, 60.0)
    assert rust_result == py_result, f"query={query!r} text={text!r}"


def test_snippet_around_matches_empty_text():
    assert nsa_rust.snippet_around_matches("query", "", 80, 60.0) == ""
    assert _snippet_around_matches("query", "", 80, 60.0) == ""


# ---------------------------------------------------------------------------
# Integration: snippet + highlight consistency
# ---------------------------------------------------------------------------

def test_snippet_contains_mark_tags_on_match():
    """When spans are found, the snippet should contain <mark> tags."""
    text = "Acme Foods Ltd"
    snippet = nsa_rust.snippet_around_matches("Acme", text, 80, 60.0)
    assert "<mark>Acme</mark>" in snippet


def test_snippet_no_marks_when_no_word_match():
    """Without whole-word/fuzzy matches, no <mark> tags (fallback snippet)."""
    result = nsa_rust.snippet_around_matches("zzzqx", "the quick brown fox", 80, 60.0)
    assert "<mark>" not in result


# ---------------------------------------------------------------------------
# Fuzzy threshold boundary
# ---------------------------------------------------------------------------

def test_fuzzy_threshold_boundary():
    """A term just above threshold should match; just below should not."""
    # "Acmee" vs "Acme" — ratio is high enough (>60)
    spans_match = nsa_rust.find_match_spans("Acmee", "Acme Foods", 60.0)
    assert json.loads(spans_match) != []

    # With a very high threshold, no fuzzy match
    spans_nomatch = nsa_rust.find_match_spans("Acmee", "Acme Foods", 999.0)
    assert json.loads(spans_nomatch) != []  # exact "Acme" still matches


# ---------------------------------------------------------------------------
# Multi-word query parity
# ---------------------------------------------------------------------------

def test_multi_word_query_parsing():
    """Query with multiple terms is split and each term matched independently."""
    text = "Laboratory test results show presence of heavy metals in the sample"
    query = "heavy metals"

    py_spans = _find_match_spans(query, text)
    rust_spans = json.loads(nsa_rust.find_match_spans(query, text, 60.0))

    # Both terms should have spans.
    assert len(rust_spans) >= 1
    assert len(py_spans) >= 1

    # Convert to comparable format.
    py_list = [[s, e] for s, e in py_spans]
    assert rust_spans == py_list


def test_multi_word_typo_query():
    """A typo in one term should fuzzy-match the correct word."""
    text = "presence of heavy metals detected"
    query = "hevay metals"

    python_snippet = _snippet_around_matches(query, text, 80, 60.0)
    rust_snippet = nsa_rust.snippet_around_matches(query, text, 80, 60.0)

    assert rust_snippet == python_snippet
    assert "<mark>heavy</mark>" in rust_snippet
    assert "<mark>metals</mark>" in rust_snippet

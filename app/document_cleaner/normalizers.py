"""Normalization operations for the Legal Document Cleaning Pipeline.

Each function takes ``(text: str) -> str`` and returns normalized text.
Functions operate on the full text string (not line-by-line) for efficiency.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import rapidfuzz

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Common English word list for hyphenated-word validation
# (used by normalize_hyphens via rapidfuzz fuzzy matching)
# ---------------------------------------------------------------------------

_COMMON_WORDS = frozenset({
    # Legal document frequent words
    "therefore",
    "whereas",
    "hereinafter",
    "thereunder",
    "thereof",
    "thereto",
    "therein",
    "hereunder",
    "herein",
    "hereto",
    "hereby",
    "hereafter",
    "notwithstanding",
    "forthwith",
    "whatsoever",
    "wheresoever",
    # Common compounds
    "information",
    "communication",
    "administration",
    "interpretation",
    "implementation",
    "investigation",
    "identification",
    "classification",
    "distribution",
    "consolidation",
    "documentation",
    "registration",
    "certification",
    "authorization",
    "determination",
    "consideration",
    "circumstances",
    "establishment",
    "acknowledgement",
    "recommendation",
    # Multi-syllable words common in legal text
    "paragraph",
    "subparagraph",
    "subsection",
    "subclause",
    "subdivision",
    "legislation",
    "jurisdiction",
    "adjudication",
    "arbitration",
    "prosecution",
    "defendant",
    "plaintiff",
    "petitioner",
    "respondent",
    "affidavit",
    "testimony",
    "evidence",
    "exhibit",
    "appendix",
    "preliminary",
    "extraordinary",
    "parliament",
    "government",
    "authority",
    "regulatory",
    "statutory",
    "legislative",
    "judicial",
})

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Multiple spaces (but not newlines)
_MULTI_SPACE = re.compile(r"[^\S\n]{2,}")

# Tab characters
_TAB_PATTERN = re.compile(r"\t+")

# Excessive blank lines (3+ consecutive newlines -> 2)
_EXCESS_NEWLINES = re.compile(r"\n{3,}")

# Hyphenated word split across lines: "word-\\nword"
_HYPHEN_BREAK = re.compile(r"(\w{2,})-\s*\n\s*(\w{2,})")

# Smart/curly quotes and apostrophes
_CURLY_QUOTES = {
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u201e": '"',  # double low-9 quotation mark
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u201a": "'",  # single low-9 quotation mark
    "\u2032": "'",  # prime
    "\u2033": '"',  # double prime
    "\u2039": "'",  # single left-pointing angle quotation mark
    "\u203a": "'",  # single right-pointing angle quotation mark
    "\u00ab": '"',  # left-pointing double angle quotation mark
    "\u00bb": '"',  # right-pointing double angle quotation mark
}

# Build regex for all curly quotes
_CURLY_QUOTE_RE = re.compile("[" + "".join(_CURLY_QUOTES.keys()) + "]")

# Bullet characters to normalize to *
_BULLET_MAP = {
    "\u2022": "*",  # bullet
    "\u2023": "*",  # triangular bullet
    "\u25cf": "*",  # black circle
    "\u25cb": "*",  # white circle
    "\u25a0": "*",  # black square
    "\u25aa": "*",  # black small square
    "\u2026": "...",  # ellipsis -> three periods
    "\u25e6": "o",  # white bullet
    "\u2043": "-",  # hyphen bullet
    "\u2219": "*",  # bullet operator
    "\u00b7": "-",  # middle dot
}
_BULLET_RE = re.compile("[" + "".join(_BULLET_MAP.keys()) + "]")

# Encoding artifacts: common mojibake characters (NOT including NBSP which is replaced below)
_ENCODING_ARTIFACTS = re.compile(r"[\u0080-\u009f\u00ad\u200b-\u200f\u2028-\u202f\u2060-\u2063]")

# Non-breaking space (must be handled before other encoding artifacts to avoid double-removal)
_NBSP = re.compile("\u00a0")

# Line-ending trailing whitespace
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)

# Leading whitespace on each line (for normalization, not removal)
_LEADING_WS = re.compile(r"^[ \t]+", re.MULTILINE)


def normalize_unicode(text: str) -> str:
    """NFKC Unicode normalization (compatibility decomposition + composition)."""
    return unicodedata.normalize("NFKC", text)


def normalize_spaces(text: str) -> str:
    """Collapse multiple consecutive spaces (but not newlines) to a single space."""
    return _MULTI_SPACE.sub(" ", text)


def normalize_tabs(text: str) -> str:
    """Replace tab characters with a single space."""
    return _TAB_PATTERN.sub(" ", text)


def normalize_linebreaks(text: str) -> str:
    """Collapse 3+ consecutive newlines to 2 (preserving paragraph breaks)."""
    return _EXCESS_NEWLINES.sub("\n\n", text)


def normalize_hyphens(text: str) -> str:
    """Rejoin hyphenated words split across lines.

    Matches patterns like ``compu-\\nter`` and joins them to ``computer``.
    Uses rapidfuzz to validate the joined word against known patterns.
    """

    def _rejoin(match: re.Match) -> str:
        prefix: str = match.group(1)
        suffix: str = match.group(2)
        joined = prefix + suffix
        # Use rapidfuzz fuzzy matching to validate the joined word
        # If the joined word has high similarity to a known word, accept it
        best_score = rapidfuzz.fuzz.partial_ratio(joined, joined)  # baseline 100
        for known in _COMMON_WORDS:
            score = rapidfuzz.fuzz.ratio(joined.lower(), known)
            best_score = max(best_score, score)
        # Accept if score is high enough, or if it's a simple concatenation
        if best_score > 85:
            return joined
        # For low-score joins, check if it looks like a real word
        # (e.g., contains vowels and has reasonable letter patterns)
        if len(joined) > 3 and bool(re.search(r"[aeiouy]", joined.lower())):
            return joined
        # Not confident — return original with hyphen preserved
        return f"{prefix}-\n{suffix}"

    return _HYPHEN_BREAK.sub(_rejoin, text)


def normalize_quotes(text: str) -> str:
    """Replace curly/smart quotes with straight ASCII equivalents."""

    def _replace_quote(match: re.Match) -> str:
        ch: str = match.group(0)
        return _CURLY_QUOTES.get(ch, ch)

    return _CURLY_QUOTE_RE.sub(_replace_quote, text)


def normalize_bullets(text: str) -> str:
    """Normalize bullet characters to standard ASCII."""

    def _replace_bullet(match: re.Match) -> str:
        ch: str = match.group(0)
        return _BULLET_MAP.get(ch, ch)

    return _BULLET_RE.sub(_replace_bullet, text)


def normalize_encoding(text: str) -> str:
    """Remove or replace common encoding artifacts and control characters."""
    # Replace non-breaking spaces with regular spaces FIRST
    text = _NBSP.sub(" ", text)
    # Remove zero-width characters, soft hyphens, and other invisible chars
    text = _ENCODING_ARTIFACTS.sub("", text)
    return text


def normalize_trailing_whitespace(text: str) -> str:
    """Strip trailing whitespace from each line."""
    return _TRAILING_WS.sub("", text)


# ---------------------------------------------------------------------------
# All normalizers in run order
# ---------------------------------------------------------------------------

NORMALIZER_REGISTRY = [
    ("unicode", normalize_unicode),
    ("encoding", normalize_encoding),
    ("bullets", normalize_bullets),
    ("quotes", normalize_quotes),
    ("tabs", normalize_tabs),
    ("hyphens", normalize_hyphens),
    ("spaces", normalize_spaces),
    ("trailing_whitespace", normalize_trailing_whitespace),
    ("linebreaks", normalize_linebreaks),
]

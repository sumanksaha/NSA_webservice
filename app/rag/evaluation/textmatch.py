"""Deterministic text-matching helpers shared by evaluation metrics.

Used by both the RAGAS-style reference metrics (``ragas_metrics``) and
``CoverageMetrics`` (``metrics``).  Kept dependency-free (rapidfuzz only)
so tests and CI can score without any retrieval or LLM backend.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

#: Minimal stopword set for content-token extraction.  Deliberately small:
#  domain words like "section" or "penalty" must survive tokenization.
STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have", "in", "is", "it", "its", "of", "on", "or", "should", "so", "that", "the", "this", "to", "under", "was", "were", "what", "which", "who", "whom", "whose", "will", "with", "would", "about"]
)

_WORD_RE = re.compile(r"\w+")

#: rapidfuzz partial-ratio threshold (0-100) for a token to count as present.
TOKEN_MATCH_THRESHOLD = 75


def content_tokens(text: str) -> list[str]:
    """Lowercase content tokens: drop stopwords, keep digit-bearing tokens."""
    return [
        tok
        for tok in _WORD_RE.findall((text or "").lower())
        if tok not in STOPWORDS and (len(tok) >= 3 or any(c.isdigit() for c in tok))
    ]


def token_coverage(needles: list[str], haystack: str) -> float:
    """Fraction of *needles* fuzzily present in *haystack* text (0.0-1.0)."""
    if not needles:
        return 0.0
    lowered = (haystack or "").lower()
    hits = sum(
        1 for n in needles if fuzz.partial_ratio(n, lowered) >= TOKEN_MATCH_THRESHOLD
    )
    return hits / len(needles)


def chunk_text(chunk: object) -> str:
    """Chunk text whether we got a ``RetrievedChunk`` or a serialized dict."""
    if isinstance(chunk, dict):
        return str(chunk.get("text") or "")
    return str(getattr(chunk, "text", "") or "")


def chunk_id(chunk: object) -> str:
    """Chunk id whether we got a ``RetrievedChunk`` or a serialized dict."""
    if isinstance(chunk, dict):
        return str(chunk.get("chunk_id") or "")
    return str(getattr(chunk, "chunk_id", "") or "")

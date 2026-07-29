"""Confidence scoring engine for the Legal Metadata Extraction Engine.

Computes per-field and overall confidence based on:
- Extraction method (regex > NER > heuristic > default)
- Number of matching patterns
- Cross-field consistency
- Text quality heuristics
"""

from __future__ import annotations

from app.metadata_extractor.models import FieldConfidence

# Base confidence by method
_METHOD_BASE: dict[str, float] = {
    "regex": 0.85,
    "ner": 0.70,
    "hybrid": 0.90,
    "heuristic": 0.55,
    "default": 0.30,
}


def score_field(
    value: str,
    method: str,
    candidates: list[tuple],
    field_name: str = "",
    text_length: int = 0,
) -> FieldConfidence:
    """Compute confidence for a single extracted field.

    Args:
        value: The extracted value.
        method: Extraction method name.
        candidates: All candidate extractions for this field.
        field_name: Name of the field (for cross-field rules).
        text_length: Length of the input text (for quality scoring).

    Returns:
        A :class:`FieldConfidence` with the adjusted score.

    """
    base = _METHOD_BASE.get(method, 0.3)

    # Boost: multiple candidates that agree
    consensus_boost = 0.0
    if len(candidates) > 1:
        top_values = [c[0].lower().strip() for c in candidates[:3]]
        if top_values.count(top_values[0]) == len(top_values):
            consensus_boost = 0.10  # All top 3 agree
        elif len(set(top_values)) < len(top_values):
            consensus_boost = 0.05  # Some agreement

    # Boost: value length is reasonable for the field
    length_boost = 0.0
    if 3 < len(value) < 200:
        length_boost = 0.05
    elif len(value) > 200:
        length_boost = -0.10  # Too long, probably noisy

    # Boost: text length quality (longer text = more context)
    text_quality = min(0.05, text_length / 1_000_000 * 0.05)

    # Penalty: very short values (likely noise)
    if len(value) < 3:
        base -= 0.20
    # Penalty: value contains gibberish (non-alpha ratio too high)
    if value and sum(c.isalpha() for c in value) / max(len(value), 1) < 0.3:
        base -= 0.15

    final = min(1.0, max(0.0, base + consensus_boost + length_boost + text_quality))
    return FieldConfidence(
        value=value,
        score=round(final, 4),
        method=method,
        detail=f"base={base:.2f}, consensus={consensus_boost:.2f}, len={length_boost:.2f}",
    )

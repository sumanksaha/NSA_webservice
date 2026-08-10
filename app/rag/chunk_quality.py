"""Chunk quality validation (Agent A, Phase 2 — Day 7, §4).

Combines the R2 confidence scorer (:func:`score_field`) and the R2 cross-field
:class:`Validator` (``app/metadata_extractor``) with structural chunk rules
into a single :class:`ChunkQualityValidator` that grades a :class:`Chunk`
(``app/rag/chunker.py``) or a §5.1 payload dict on a 0.0–1.0 scale (A–F).

What is scored:

- **Structural rules** — empty text (error), too-short / too-long chunks
  (warnings), missing ``content_hash`` (warning), missing ``document_id``
  (error), and per-field confidence from ``score_field``.
- **Cross-field consistency** — ``Validator.validate_all`` boosts/penalizes
  ``document_type``/``authority``/dates; the validator's before/after score
  deltas flow into the final quality score.

The validator is injectable (mock-injection pattern) and imported lazily so
the module boots without the metadata-extractor stack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Structural thresholds (characters).
MIN_CHUNK_CHARS = 20
MAX_CHUNK_CHARS = 2000


@dataclass
class ChunkQuality:
    """Quality verdict for a single chunk."""

    score: float = 0.0
    grade: str = "F"  # A–F
    issues: list[dict[str, Any]] = field(default_factory=list)
    field_scores: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when no error-severity issues exist and the score is ≥ 0.5."""
        return not any(i.get("severity") == "error" for i in self.issues) and self.score >= 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "grade": self.grade,
            "ok": self.ok,
            "issues": list(self.issues),
            "field_scores": dict(self.field_scores),
        }


def _value(chunk: Any, key: str, default: Any = "") -> Any:
    """Read a field off a :class:`Chunk` or a §5.1 payload dict."""
    if isinstance(chunk, dict):
        return chunk.get(key, default)
    return getattr(chunk, key, default)


class ChunkQualityValidator:
    """Grade a chunk's quality: structural rules + R2 confidence/cross-field checks.

    Args:
        validator: Optional pre-built ``Validator`` (injected for tests; the
            real R2 one is built lazily).
        scorer: Optional ``score_field`` callable (injected for tests).
    """

    def __init__(self, validator: Any | None = None, scorer: Any | None = None) -> None:
        self._validator = validator
        self._scorer = scorer

    # ------------------------------------------------------------------ #
    # Lazy accessors
    # ------------------------------------------------------------------ #

    def _get_validator(self) -> Any:
        if self._validator is None:
            from app.metadata_extractor.validation import Validator

            self._validator = Validator()
        return self._validator

    def _get_scorer(self) -> Any:
        if self._scorer is None:
            from app.metadata_extractor.confidence import score_field

            self._scorer = score_field
        return self._scorer

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def validate_chunk(self, chunk: Any) -> ChunkQuality:
        """Grade a :class:`Chunk` (or §5.1 payload dict)."""
        text = str(_value(chunk, "chunk_text", "") or "")
        quality = ChunkQuality()

        # --- Structural rules -------------------------------------------------
        if not text.strip():
            quality.issues.append(
                {"severity": "error", "code": "empty_text", "message": "chunk text is empty"}
            )
            quality.score = 0.0
            quality.grade = self.grade(quality.score)
            return quality

        score = 0.7  # base for a non-empty chunk

        if not _value(chunk, "document_id", ""):
            quality.issues.append(
                {"severity": "error", "code": "missing_document_id", "message": "chunk has no document_id"}
            )
            score -= 0.3

        if not _value(chunk, "content_hash", ""):
            quality.issues.append(
                {"severity": "warning", "code": "missing_content_hash", "message": "chunk has no content_hash (dedup will not work)"}
            )
            score -= 0.1

        char_count = len(text)
        if char_count < MIN_CHUNK_CHARS:
            quality.issues.append(
                {"severity": "warning", "code": "chunk_too_short", "message": f"chunk is {char_count} chars (< {MIN_CHUNK_CHARS})"}
            )
            score -= 0.1
        elif char_count > MAX_CHUNK_CHARS:
            quality.issues.append(
                {"severity": "warning", "code": "chunk_too_long", "message": f"chunk is {char_count} chars (> {MAX_CHUNK_CHARS})"}
            )
            score -= 0.1

        # --- Per-field confidence + cross-field consistency (R2) --------------
        # One score_field call per present field (``regex`` for extracted
        # fields, ``default`` for state/jurisdiction — matching the R2
        # extractor conventions); the same FieldConfidence dict feeds the R2
        # Validator, and its before/after deltas add to (or subtract from) the
        # quality score, capped at ±0.2.
        from app.metadata_extractor.models import FieldConfidence

        scorer = self._get_scorer()
        fields: dict[str, FieldConfidence] = {}
        for field_name, method in (
            ("document_type", "regex"),
            ("authority", "regex"),
            ("jurisdiction", "default"),
            ("state", "default"),
        ):
            raw = str(_value(chunk, field_name, "") or "").strip()
            if raw:
                fields[field_name] = scorer(raw, method, [], field_name=field_name, text_length=char_count)

        field_scores: dict[str, float] = {name: f.score for name, f in fields.items()}
        before = {name: f.score for name, f in fields.items()}
        validated = self._get_validator().validate_all(fields, text)
        delta = 0.0
        for name in before:
            after = validated.get(name)
            if after is not None:
                delta += after.score - before[name]
                field_scores[name] = after.score
        score += max(-0.2, min(0.2, delta))

        quality.score = max(0.0, min(1.0, score))
        quality.grade = self.grade(quality.score)
        quality.field_scores = field_scores
        return quality

    @staticmethod
    def grade(score: float) -> str:
        """Map a 0.0–1.0 score to an A–F grade."""
        if score >= 0.85:
            return "A"
        if score >= 0.7:
            return "B"
        if score >= 0.5:
            return "C"
        if score >= 0.3:
            return "D"
        return "F"


# End of chunk_quality.py

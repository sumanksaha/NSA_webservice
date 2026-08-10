"""Tests for the Agent A Phase 2 chunk quality validator (app/rag/chunk_quality.py).

Pins the grading contract: structural rules (empty text, too-short/too-long,
missing content_hash/document_id), R2 ``score_field`` per-field confidence,
R2 ``Validator`` cross-field consistency deltas, A–F grading, and the
``Chunk`` / payload-dict duck-typing.
"""

from __future__ import annotations

import json

from app.rag.chunk_quality import ChunkQuality, ChunkQualityValidator
from app.rag.chunker import Chunk


def _make_chunk(**overrides):
    defaults = {
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "chunk_index": 0,
        "chunk_text": "Every food business operator shall comply with the standards "
        "prescribed under Section 55 of the Act for safe and wholesome food.",
        "content_hash": "abc123",
        "document_type": "act",
        "authority": "Ministry of Health and Family Welfare",
        "jurisdiction": "India",
        "section_number": "55",
    }
    defaults.update(overrides)
    return Chunk(**defaults)


class TestStructuralRules:
    def test_empty_text_is_error(self):
        chunk = _make_chunk(chunk_text="   ")
        quality = ChunkQualityValidator().validate_chunk(chunk)
        assert quality.score == 0.0
        assert quality.grade == "F"
        assert quality.ok is False
        assert any(i["code"] == "empty_text" for i in quality.issues)

    def test_good_chunk_scores_high(self):
        quality = ChunkQualityValidator().validate_chunk(_make_chunk())
        assert quality.score >= 0.7
        assert quality.grade in ("A", "B")
        assert quality.ok is True

    def test_short_chunk_warns(self):
        quality = ChunkQualityValidator().validate_chunk(_make_chunk(chunk_text="short"))
        assert any(i["code"] == "chunk_too_short" for i in quality.issues)

    def test_long_chunk_warns(self):
        chunk = _make_chunk(chunk_text="word " * 500)  # 2500 chars
        quality = ChunkQualityValidator().validate_chunk(chunk)
        assert any(i["code"] == "chunk_too_long" for i in quality.issues)

    def test_missing_content_hash_warns(self):
        chunk = _make_chunk(content_hash="")
        quality = ChunkQualityValidator().validate_chunk(chunk)
        assert any(i["code"] == "missing_content_hash" for i in quality.issues)

    def test_missing_document_id_is_error(self):
        chunk = _make_chunk(document_id="")
        quality = ChunkQualityValidator().validate_chunk(chunk)
        assert any(i["code"] == "missing_document_id" for i in quality.issues)
        assert quality.ok is False


class TestGrading:
    @staticmethod
    def _grade(score):
        return ChunkQualityValidator.grade(score)

    def test_grade_boundaries(self):
        assert self._grade(0.95) == "A"
        assert self._grade(0.85) == "A"
        assert self._grade(0.84) == "B"
        assert self._grade(0.7) == "B"
        assert self._grade(0.6) == "C"
        assert self._grade(0.5) == "C"
        assert self._grade(0.4) == "D"
        assert self._grade(0.29) == "F"


class TestPayloadDictSupport:
    def test_validate_payload_dict(self):
        payload = _make_chunk().to_payload()
        quality = ChunkQualityValidator().validate_chunk(payload)
        assert isinstance(quality, ChunkQuality)
        assert quality.score >= 0.7
        assert quality.ok is True

    def test_to_dict_is_json_serializable(self):
        quality = ChunkQualityValidator().validate_chunk(_make_chunk())
        d = quality.to_dict()
        json.loads(json.dumps(d))
        assert "score" in d and "grade" in d and "issues" in d


class TestR2Integration:
    def test_field_scores_populated(self):
        quality = ChunkQualityValidator().validate_chunk(_make_chunk())
        assert "document_type" in quality.field_scores
        assert 0.0 < quality.field_scores["document_type"] <= 1.0

    def test_validator_boost_applied_for_consistent_metadata(self):
        # "Act" + Ministry authority -> R2 Validator boosts document_type.
        chunk = _make_chunk(document_type="Act")
        validator = ChunkQualityValidator()
        plain = validator.validate_chunk(_make_chunk(document_type="Act", authority=""))
        rich = validator.validate_chunk(chunk)
        assert rich.score >= plain.score

    def test_mock_validator_delta_flows_into_score(self):
        class _BoostingValidator:
            def validate_all(self, fields, text):
                from app.metadata_extractor.models import FieldConfidence

                out = dict(fields)
                if "document_type" in out:
                    out["document_type"] = FieldConfidence(
                        value=out["document_type"].value,
                        score=min(1.0, out["document_type"].score + 0.2),  # R2 clamps at 1.0
                        method=out["document_type"].method,
                    )
                return out

        validator = ChunkQualityValidator(validator=_BoostingValidator())
        quality = validator.validate_chunk(_make_chunk())
        # Base 0.7 + boost; the boost is capped at 0.2, and the mock's +0.2 is
        # itself clamped at FieldConfidence's 1.0 (delta 0.15), so >= 0.85.
        assert quality.score >= 0.85
        assert quality.grade == "A"

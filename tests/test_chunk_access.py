"""Parity tests for the shared chunk access seam (C4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.rag.retrieval.chunk import (
    chunk_id,
    field as chunk_field,
    score,
    set_field,
    set_score,
    text,
)
from app.rag.retrieval.evidence_selector import canonical_unit_id, group_chunks_by_unit
from app.rag.retrieval.provision_metadata import derive_provision_metadata
from app.rag.retrieval.result import RetrievedChunk


@dataclass(frozen=True)
class FrozenChunk:
    chunk_id: str = "frozen"
    score: float = 0.1


@dataclass
class FakeChunk:
    chunk_id: str | None = "c1"
    text: str = "Section 31 licence requirement"
    score: float = 0.75
    section_number: str | None = "31"
    act_name: str = "Food Safety and Standards Act, 2006"
    document_title: str = "Food Safety and Standards Act, 2006"
    chunk_index: int = 2
    extras: dict[str, object] = field(default_factory=dict)


def _pair() -> tuple[FakeChunk, dict[str, object]]:
    obj = FakeChunk()
    payload = {
        "chunk_id": obj.chunk_id,
        "text": obj.text,
        "score": obj.score,
        "section_number": obj.section_number,
        "act_name": obj.act_name,
        "document_title": obj.document_title,
        "chunk_index": obj.chunk_index,
    }
    return obj, payload


class TestFieldAccess:
    def test_field_reads_object_and_dict(self):
        obj, payload = _pair()
        assert chunk_field(obj, "section_number") == "31"
        assert chunk_field(payload, "section_number") == "31"

    def test_field_default_only_when_absent(self):
        assert chunk_field({}, "missing", "fallback") == "fallback"
        assert chunk_field(FakeChunk(), "missing", "fallback") == "fallback"
        assert chunk_field({"missing": None}, "missing", "fallback") is None

    def test_text_is_always_str(self):
        assert text(FakeChunk()) == "Section 31 licence requirement"
        assert text({"text": "hello"}) == "hello"
        assert text({"text": None}) == ""
        assert text({}) == ""
        assert text({"text": 123}) == "123"

    def test_chunk_id_none_vs_string_default(self):
        assert chunk_id(FakeChunk()) == "c1"
        assert chunk_id({"chunk_id": "c1"}) == "c1"
        assert chunk_id({}) is None
        assert chunk_id({}, default="") == ""
        assert chunk_id(FakeChunk(chunk_id=None), default="") == ""

    def test_score_coerces_and_guards(self):
        assert score(FakeChunk()) == 0.75
        assert score({"score": "0.25"}) == 0.25
        assert score({}) == 0.0
        assert score({"score": None}) == 0.0
        assert score({"score": "not-a-number"}) == 0.0
        assert score({"score": "bad"}, default=0.5) == 0.5


class TestFieldWrite:
    def test_set_field_object_and_dict(self):
        obj, payload = _pair()
        set_field(obj, "section_number", "32")
        set_field(payload, "section_number", "32")
        assert obj.section_number == "32"
        assert payload["section_number"] == "32"

    def test_set_score_object_and_dict(self):
        obj, payload = _pair()
        set_score(obj, "0.5")
        set_score(payload, "0.5")
        assert obj.score == 0.5
        assert payload["score"] == 0.5

    def test_set_score_immutable_chunk_is_a_no_op(self):
        chunk = FrozenChunk()
        set_score(chunk, 0.9)
        assert chunk.score == 0.1


class TestConsumerParity:
    def test_retrieved_chunk_and_dict_derive_same_identity(self):
        chunk = RetrievedChunk(
            chunk_id="c1",
            score=0.9,
            text="31. Licensing and registration — no person shall...",
            section_number="31",
            act_name="Food Safety and Standards Act, 2006",
            document_title="Food Safety and Standards Act, 2006",
        )
        assert canonical_unit_id(chunk) == canonical_unit_id(chunk.to_dict())

    def test_grouping_and_metadata_agree(self):
        chunk = RetrievedChunk(
            chunk_id="c1",
            score=0.9,
            text="2.9.8: Cumin whole means the dried mature fruits.",
            clause_number="2.9.8",
            section_number="2.9.8",
            document_title="Food Additives Regulations",
            document_id="doc1",
            chunk_index=0,
        )
        payload = chunk.to_dict()
        assert group_chunks_by_unit([chunk]).keys() == group_chunks_by_unit([payload]).keys()
        assert derive_provision_metadata(chunk) == derive_provision_metadata(payload)

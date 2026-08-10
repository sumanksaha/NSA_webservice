"""Tests for the Agent A Phase 1 chunker (app/rag/chunker.py).

The :class:`Chunk` payload must mirror the §5.1 Qdrant payload schema
(``RAG_AGENT_A_SCOPE.md``) so Agent B's ``RetrievedChunk``
(``app/rag/retrieval/result.py``) can consume the index without
transformation.  The :class:`Chunker` adapts
``LegalParagraphEngine.process_document`` output into ``Chunk`` objects.

Tests are fully self-contained: the paragraph engine is injected as a fake
(mock-injection pattern from ``tests/test_dense_retriever.py``); the real
engine is exercised in one integration test via the app's engine accessor.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.rag.chunker import Chunk, Chunker

#: Every payload field in RAG_AGENT_A_SCOPE.md §5.1 that must be present.
SCOPE_5_1_FIELDS = (
    "document_id",
    "document_uri",
    "document_title",
    "document_type",
    "authority",
    "jurisdiction",
    "state",
    "effective_date",
    "enactment_date",
    "amended_date",
    "is_current",
    "chunk_index",
    "chunk_text",
    "chunk_char_count",
    "section_number",
    "section_title",
    "subsection",
    "hierarchy_level",
    "parent_chunk_id",
    "citations",
    "references",
    "confidence",
    "created_at",
    "embedding_model",
)


def _make_paragraph(
    text="The Food Authority shall ensure food safety.",
    section=None,
    depth=1,
    parent_id=None,
    paragraph_id="para_0_0",
    doc_type="act",
    citations=None,
    overall=0.85,
):
    """Create a paragraph dict shaped like LegalParagraphEngine output."""
    return {
        "paragraph_id": paragraph_id,
        "paragraph_type": "normal",
        "text": text,
        "section": section,
        "hierarchy_depth": depth,
        "parent_id": parent_id,
        "word_count": len(text.split()),
        "document_type": doc_type,
        "citations": citations or [],
        "confidence_scores": {"overall": overall},
    }


class _FakeEngine:
    """Minimal LegalParagraphEngine stand-in returning canned paragraphs."""

    def __init__(self, paragraphs):
        self._paragraphs = paragraphs

    def process_document(self, text, doc_type_info=None):  # noqa: ARG002 - fake interface
        return self._paragraphs


class TestChunkPayloadSchema:
    def test_to_payload_contains_all_scope_5_1_fields(self):
        chunk = Chunk(chunk_id="c1", document_id="d1", chunk_index=0, chunk_text="text")
        payload = chunk.to_payload()
        for field in SCOPE_5_1_FIELDS:
            assert field in payload, f"payload missing §5.1 field {field!r}"

    def test_to_payload_is_json_serializable(self):
        chunk = Chunk(
            chunk_id="c1",
            document_id="d1",
            chunk_index=0,
            chunk_text="Section 55 text",
            citations=["Section 55"],
            references=["Section 56"],
            confidence=0.851234,
        )
        payload = chunk.to_payload()
        # Round-trip through JSON — Qdrant payloads must be JSON-safe.
        restored = json.loads(json.dumps(payload))
        assert restored["chunk_id"] == "c1"
        assert restored["citations"] == ["Section 55"]
        assert restored["confidence"] == 0.851234  # rounded to 6dp

    def test_to_payload_defaults_are_safe(self):
        chunk = Chunk(chunk_id="c1", document_id="d1", chunk_index=0, chunk_text="x")
        payload = chunk.to_payload()
        assert payload["is_current"] is True
        assert payload["section_number"] is None
        assert payload["parent_chunk_id"] is None
        assert payload["citations"] == []
        assert payload["references"] == []

    def test_chunk_id_is_embedded_in_payload(self):
        chunk = Chunk.from_paragraph(_make_paragraph())
        assert chunk.chunk_id in chunk.to_payload()["chunk_id"]


class TestChunkFromParagraph:
    def test_section_number_and_hierarchy_level_mapped(self):
        chunk = Chunk.from_paragraph(_make_paragraph(section="55", depth=2))
        assert chunk.section_number == "55"
        assert chunk.hierarchy_level == 2

    def test_section_title_extracted_from_header_text(self):
        chunk = Chunk.from_paragraph(_make_paragraph(text="Section 3: Definitions"))
        assert chunk.section_title == "Definitions"
        chunk = Chunk.from_paragraph(_make_paragraph(text="Section 3 Definitions"))
        assert chunk.section_title == "Definitions"

    def test_subsection_markers_extracted(self):
        chunk = Chunk.from_paragraph(
            _make_paragraph(text="3(1)(a) The Food Authority shall ensure food safety.")
        )
        assert chunk.subsection == "(1)(a)"

    def test_marker_chain_carries_no_section_number(self):
        """§2.3: marker chains reference a section defined elsewhere."""
        chunk = Chunk.from_paragraph(_make_paragraph(text="(1)(a) First clause."))
        assert chunk.section_number is None
        assert chunk.subsection == "(1)(a)"

    def test_citations_and_confidence_mapped(self):
        citations = [
            {"type": "section", "reference": "Section 55"},
            {"type": "statutory", "reference": "Food Safety and Standards Act"},
        ]
        chunk = Chunk.from_paragraph(
            _make_paragraph(citations=citations, overall=0.92)
        )
        assert chunk.citations == ["Section 55", "Food Safety and Standards Act"]
        assert chunk.confidence == 0.92

    def test_document_metadata_passthrough(self):
        doc = {
            "document_id": "doc-abc",
            "document_uri": "s3://corpus/fss-act-2006.pdf",
            "title": "Food Safety and Standards Act, 2006",
            "type": "act",
            "authority": "FSSAI",
            "jurisdiction": "India",
            "state": "National",
            "effective_date": "2006-08-24",
            "is_current": False,
            "references": ["Section 56"],
        }
        chunk = Chunk.from_paragraph(_make_paragraph(), doc)
        assert chunk.document_id == "doc-abc"
        assert chunk.document_uri == "s3://corpus/fss-act-2006.pdf"
        assert chunk.document_title == "Food Safety and Standards Act, 2006"
        assert chunk.document_type == "act"
        assert chunk.authority == "FSSAI"
        assert chunk.jurisdiction == "India"
        assert chunk.state == "National"
        assert chunk.effective_date == "2006-08-24"
        assert chunk.is_current is False
        assert chunk.references == ["Section 56"]

    def test_document_type_falls_back_to_unknown(self):
        chunk = Chunk.from_paragraph(_make_paragraph(doc_type=None), {})
        assert chunk.document_type == "unknown"


class TestChunker:
    def test_blank_text_returns_empty(self):
        chunker = Chunker(engine=_FakeEngine([]))
        assert chunker.chunk_text("") == []
        assert chunker.chunk_text("   \n  ") == []

    def test_chunk_count_and_sequential_index(self):
        paragraphs = [
            _make_paragraph(text="P1", paragraph_id="p0"),
            _make_paragraph(text="P2", paragraph_id="p1"),
            _make_paragraph(text="P3", paragraph_id="p2"),
        ]
        chunker = Chunker(engine=_FakeEngine(paragraphs))
        chunks = chunker.chunk_text("some text")
        assert len(chunks) == 3
        assert [c.chunk_index for c in chunks] == [0, 1, 2]

    def test_parent_hierarchy_wired_from_parent_id(self):
        paragraphs = [
            _make_paragraph(text="Header", paragraph_id="p0", depth=1),
            _make_paragraph(text="Child", paragraph_id="p1", depth=2, parent_id="p0"),
        ]
        chunker = Chunker(engine=_FakeEngine(paragraphs))
        chunks = chunker.chunk_text("text")
        parent, child = chunks
        assert child.parent_chunk_id == parent.chunk_id
        assert parent.parent_chunk_id is None

    def test_injected_embedding_model_stamped(self):
        chunker = Chunker(engine=_FakeEngine([_make_paragraph()]), embedding_model="test-model")
        chunks = chunker.chunk_text("text")
        assert chunks[0].embedding_model == "test-model"

    def test_embedding_model_read_from_config(self):
        from app import create_app

        app = create_app()
        app.config["RAG_EMBEDDING_MODEL"] = "custom/model"
        with app.app_context():
            chunker = Chunker(engine=_FakeEngine([_make_paragraph()]))
            chunks = chunker.chunk_text("text")
            assert chunks[0].embedding_model == "custom/model"

    def test_real_engine_end_to_end(self):
        """Adapter works against real LegalParagraphEngine output."""
        from app.services.legal_engine import get_legal_engine

        engine = get_legal_engine()()
        chunker = Chunker(engine=engine)
        text = (
            "The Food Safety and Standards Act, 2006\n\n"
            "Section 3(1)\n\n"
            "3(1)(a) The Food Authority shall ensure food safety.\n"
            "3(1)(b) The Food Authority shall coordinate with State authorities.\n\n"
            "Section 14 of the Act.\n"
        )
        chunks = chunker.chunk_text(text, {"document_id": "doc-1", "type": "act"})
        assert len(chunks) >= 3
        assert all(c.document_id == "doc-1" for c in chunks)
        assert all(c.chunk_index == i for i, c in enumerate(chunks))
        assert any(c.section_number == "3" for c in chunks)
        assert any(c.subsection == "(1)(a)" for c in chunks)
        assert all(isinstance(c.hierarchy_level, int) for c in chunks)


class TestPayloadConsumerContract:
    """Agent B's RetrievedChunk reads these fields from the payload (§5.1)."""

    def test_retriever_required_fields_present_in_payload(self):
        chunk = Chunk.from_paragraph(_make_paragraph(section="55", depth=1))
        payload = chunk.to_payload()
        # Keys read by DenseRetriever._payload_to_chunk.
        for key in ("chunk_text", "section_number", "document_title", "document_type",
                    "authority", "chunk_index", "hierarchy_level", "parent_chunk_id"):
            assert key in payload

    def test_payload_roundtrip_into_retrieved_chunk(self):
        from app.rag.retrieval.result import RetrievedChunk

        chunk = Chunk.from_paragraph(
            _make_paragraph(
                text="Penalties under Section 55.",
                section="55",
                depth=2,
                citations=[{"type": "section", "reference": "Section 55"}],
            ),
            {"document_id": "d1", "title": "FSS Act", "type": "act", "authority": "FSSAI"},
            chunk_index=3,
        )
        payload = chunk.to_payload()
        point = SimpleNamespace(id=chunk.chunk_id, score=0.9, payload=payload)
        retrieved = RetrievedChunk(
            chunk_id=str(point.id),
            score=float(point.score),
            text=payload["chunk_text"],
            section_number=payload["section_number"],
            document_title=payload["document_title"],
            document_type=payload["document_type"],
            authority=payload["authority"],
            chunk_index=payload["chunk_index"],
            hierarchy_level=payload["hierarchy_level"],
            parent_chunk_id=payload["parent_chunk_id"],
        )
        assert retrieved.chunk_id == chunk.chunk_id
        assert retrieved.text == "Penalties under Section 55."
        assert retrieved.section_number == "55"
        assert retrieved.document_title == "FSS Act"

"""Tests for the shared Qdrant construction/filter seam and point conversion.

``build_qdrant_client`` and ``build_filter`` are the one rule shared by the
store (ingestion) and the dense retriever (query). The drift these tests pin:
the store's old copy wrapped a pre-shaped filter as a literal ``must`` clause
and dropped list expansion, while the retriever's copy handled both.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.rag.qdrant_client import QdrantStore, build_filter, build_qdrant_client
from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.result import RetrievedChunk


class TestBuildFilter:
    def test_none_and_empty(self):
        assert build_filter(None) == {}
        assert build_filter({}) == {}

    def test_flat_dict_becomes_must_clauses(self):
        assert build_filter({"section_number": "55", "is_current": True}) == {
            "must": [
                {"key": "section_number", "match": {"value": "55"}},
                {"key": "is_current", "match": {"value": True}},
            ]
        }

    def test_list_expands_to_one_clause_per_value(self):
        built = build_filter({"document_id": ["a", "b"]})
        assert built == {
            "must": [
                {"key": "document_id", "match": {"value": "a"}},
                {"key": "document_id", "match": {"value": "b"}},
            ]
        }

    def test_shaped_filter_passes_through_identity(self):
        shaped = {"must": [{"key": "section_number", "match": {"value": "55"}}]}
        assert build_filter(shaped) is shaped

    def test_both_former_copy_sites_delegate(self):
        expected = build_filter({"document_id": ["a", "b"]})
        assert QdrantStore._build_filter({"document_id": ["a", "b"]}) == expected
        assert DenseRetriever._build_filter({"document_id": ["a", "b"]}) == expected


class TestBuildQdrantClient:
    def test_injected_client_wins(self):
        injected = SimpleNamespace()
        store = QdrantStore(client=injected)
        assert store._get_client() is injected

    def test_returns_none_without_url(self, monkeypatch):
        monkeypatch.delenv("RAG_QDRANT_URL", raising=False)
        assert build_qdrant_client() is None

    def test_reads_url_outside_app_context(self, monkeypatch):
        """Celery/ingestion has no Flask app context; Pattern A must still work."""
        import qdrant_client as qdrant_sdk

        monkeypatch.setenv("RAG_QDRANT_URL", "http://qdrant.test:6333")
        monkeypatch.setenv("RAG_QDRANT_API_KEY", "secret")

        captured: dict[str, object] = {}

        def _fake_client(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(**kwargs)

        monkeypatch.setattr(qdrant_sdk, "QdrantClient", _fake_client)

        client = build_qdrant_client()
        assert captured == {"url": "http://qdrant.test:6333", "api_key": "secret"}
        assert client is not None


class TestRetrievedChunkFromQdrantPoint:
    def test_search_point_object(self):
        point = SimpleNamespace(
            id="p1",
            score=0.87,
            payload={
                "chunk_text": "Section 55 text",
                "section_number": "55",
                "clause_number": "55.1",
                "document_id": "doc-1",
                "chunk_index": 3,
            },
        )

        chunk = RetrievedChunk.from_qdrant_point(point)

        assert chunk.chunk_id == "p1"
        assert chunk.score == 0.87
        assert chunk.text == "Section 55 text"
        assert chunk.section_number == "55"
        assert chunk.clause_number == "55.1"
        assert chunk.document_id == "doc-1"
        assert chunk.chunk_index == 3

    def test_scroll_dict_without_score(self):
        point = {"id": "p2", "payload": {"text": "plain text", "document_title": "Act"}}

        chunk = RetrievedChunk.from_qdrant_point(point)

        assert chunk.chunk_id == "p2"
        assert chunk.score == 0.0
        assert chunk.text == "plain text"
        assert chunk.document_title == "Act"

    def test_explicit_id_and_payload_win(self):
        point = {"id": "ignored", "payload": {"text": "ignored"}}

        chunk = RetrievedChunk.from_qdrant_point(point, chunk_id="explicit", payload={"text": "kept"})

        assert chunk.chunk_id == "explicit"
        assert chunk.text == "kept"

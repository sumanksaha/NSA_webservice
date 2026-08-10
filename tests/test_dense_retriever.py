"""Tests for the DenseRetriever (Phase 1, Day 1).

Tests are fully self-contained: the Qdrant client and sentence-transformers
encoder are injected as mocks via the constructor, so no external services
or optional dependencies are required.

Follows the mock-injection pattern from ``tests/test_ai_assistant.py``
(``_mock_httpx_response``) but applied to the DenseRetriever's ``client``
and ``encoder`` slots.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.rag.retrieval.dense_retriever import DenseRetriever


def _make_mock_point(
    chunk_id="c1", score=0.91, text="Section 55 penalties for adulteration.",
    section_number="55", document_title="FSS Act 2006",
    **payload_extra,
):
    """Create a mock Qdrant ScoredPoint."""
    payload = {"chunk_text": text, "section_number": section_number,
               "document_title": document_title, "document_type": "Act",
               "authority": "FSSAI", "chunk_index": 0,
               "hierarchy_level": 1, "parent_chunk_id": None}
    payload.update(payload_extra)
    return SimpleNamespace(id=chunk_id, score=score, payload=payload)


def _make_mock_encoder():
    """Mock encoder whose encode() returns a fixed-length vector."""
    return SimpleNamespace(encode=lambda text: [0.1] * 768)


def _make_mock_client(points=None):
    """Mock Qdrant client whose search() returns the given points."""
    return SimpleNamespace(search=lambda **kwargs: points or [])


class TestDenseRetrieverConstruction:
    def test_defaults(self):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        with app.app_context():
            retriever = DenseRetriever(collection_name="test_coll")
            assert retriever.collection_name == "test_coll"
            assert retriever.vector_size == 768

    def test_inject_client_and_encoder(self):
        retriever = DenseRetriever(
            collection_name="test",
            client=_make_mock_client(),
            encoder=_make_mock_encoder(),
        )
        assert retriever._client is not None
        assert retriever._encoder is not None


class TestDenseRetrieverSearch:
    def test_search_returns_chunks(self):
        points = [_make_mock_point("c1", 0.91), _make_mock_point("c2", 0.78)]
        retriever = DenseRetriever(collection_name="test", client=_make_mock_client(points), encoder=_make_mock_encoder())
        result = retriever.search("Section 55", top_k=10)
        assert result.total == 2
        assert result.source == "dense"
        assert result.chunks[0].chunk_id == "c1"
        assert result.chunks[0].score == 0.91
        assert result.chunks[0].section_number == "55"

    def test_search_empty_results(self):
        retriever = DenseRetriever(collection_name="test", client=_make_mock_client(points=[]), encoder=_make_mock_encoder())
        result = retriever.search("nonexistent", top_k=10)
        assert result.total == 0
        assert result.chunks == []

    def test_search_top_k_passed_to_qdrant(self):
        received = {}
        client = SimpleNamespace(search=lambda **kw: (received.update(kw) or [_make_mock_point("c1")]))
        retriever = DenseRetriever(collection_name="test", client=client, encoder=_make_mock_encoder())
        retriever.search("test", top_k=5)
        assert received["limit"] == 5

    def test_search_query_points_client_supported(self):
        """qdrant-client 1.12+ (no client.search) retrieves via query_points."""
        points = [_make_mock_point("c1", 0.91)]
        received = {}

        def query_points(collection_name, query, limit=10, with_payload=True,
                         with_vectors=False, score_threshold=None, query_filter=None, **kw):
            received.update(collection_name=collection_name, query=query,
                            limit=limit, query_filter=query_filter)
            return SimpleNamespace(points=points)

        retriever = DenseRetriever(
            collection_name="test",
            client=SimpleNamespace(query_points=query_points),
            encoder=_make_mock_encoder(),
        )
        result = retriever.search("Section 55", top_k=5, filters={"section_number": "55"})
        assert result.total == 1
        assert result.chunks[0].chunk_id == "c1"
        assert result.chunks[0].section_number == "55"
        assert received["query"] == [0.1] * 768
        assert received["limit"] == 5
        assert received["query_filter"]["must"][0] == {"key": "section_number", "match": {"value": "55"}}


class TestDenseRetrieverErrors:
    def test_search_handles_qdrant_exception(self):
        def bad_search(**kwargs):
            raise RuntimeError("Qdrant connection refused")

        retriever = DenseRetriever(
            collection_name="test",
            client=SimpleNamespace(search=bad_search),
            encoder=_make_mock_encoder(),
        )
        result = retriever.search("test")
        assert result.total == 0
        assert result.error is not None

    def test_search_handles_none_response(self):
        retriever = DenseRetriever(
            collection_name="test",
            client=_make_mock_client(points=None),
            encoder=_make_mock_encoder(),
        )
        result = retriever.search("test")
        assert result.total == 0


class TestDenseRetrieverPayloadConversion:
    def test_payload_missing_chunk_text_uses_text(self):
        point = _make_mock_point()
        point.payload = {"text": "fallback text", "section_number": "55"}
        retriever = DenseRetriever(
            collection_name="test",
            client=_make_mock_client([point]),
            encoder=_make_mock_encoder(),
        )
        result = retriever.search("test")
        assert result.chunks[0].text == "fallback text"

    def test_payload_missing_fields_defaults(self):
        point = SimpleNamespace(id="c1", score=0.5, payload={})
        retriever = DenseRetriever(
            collection_name="test",
            client=_make_mock_client([point]),
            encoder=_make_mock_encoder(),
        )
        result = retriever.search("test")
        chunk = result.chunks[0]
        assert chunk.text == ""
        assert chunk.document_title == ""
        assert chunk.section_number is None

    def test_search_filters_passed_to_qdrant(self):
        received = {}

        def search(collection_name, query_vector, limit=10, with_payload=True,
                   with_vectors=False, score_threshold=None, search_filter=None, **kw):
            received.update(search_filter=search_filter, **kw)
            return [_make_mock_point("c1")]

        retriever = DenseRetriever(collection_name="test", client=SimpleNamespace(search=search), encoder=_make_mock_encoder())
        retriever.search("test", top_k=5, filters={"section_number": "55"})
        assert received["search_filter"]["must"][0]["key"] == "section_number"

    def test_search_no_filters_omits_search_filter(self):
        received = {}
        client = SimpleNamespace(search=lambda **kw: (received.update(kw) or [_make_mock_point("c1")]))
        retriever = DenseRetriever(collection_name="test", client=client, encoder=_make_mock_encoder())
        retriever.search("test")
        assert "search_filter" not in received

    def test_search_payload_mapped(self):
        point = _make_mock_point(
            chunk_id="abc123", score=0.88, text="Detailed text about section 55.",
            document_type="Act", authority="FSSAI", chunk_index=3,
            hierarchy_level=2, parent_chunk_id="parent_1",
        )
        retriever = DenseRetriever(collection_name="test", client=_make_mock_client([point]), encoder=_make_mock_encoder())
        result = retriever.search("test")
        chunk = result.chunks[0]
        assert chunk.chunk_id == "abc123"
        assert chunk.text == "Detailed text about section 55."
        assert chunk.chunk_index == 3
        assert chunk.hierarchy_level == 2
        assert chunk.parent_chunk_id == "parent_1"

    def test_search_score_is_float(self):
        retriever = DenseRetriever(collection_name="test", client=_make_mock_client([_make_mock_point("c1", 0.95)]), encoder=_make_mock_encoder())
        result = retriever.search("test")
        assert isinstance(result.chunks[0].score, float)
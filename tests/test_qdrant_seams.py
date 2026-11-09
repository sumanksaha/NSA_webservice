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


class TestPayloadIndexFields:
    """``payload_index_fields`` is the pre-flight check for optional filters."""

    def test_returns_declared_fields(self):
        client = SimpleNamespace(
            get_collection=lambda name: SimpleNamespace(
                payload_schema={"section_number": object(), "clause_number": object()},
            ),
        )
        store = QdrantStore(collection_name="env_legal_768_index_fields_declared", client=client)
        assert store.payload_index_fields() == frozenset({"section_number", "clause_number"})

    def test_cached_after_first_read(self):
        calls: list[str] = []

        def _get(name):
            calls.append(name)
            return SimpleNamespace(payload_schema={"section_number": object()})

        store = QdrantStore(collection_name="env_legal_768_index_fields_cached", client=SimpleNamespace(get_collection=_get))
        store.payload_index_fields()
        store.payload_index_fields()
        assert calls == ["env_legal_768_index_fields_cached"]

    def test_failure_returns_empty_set(self):
        def _boom(name):
            raise RuntimeError("collection unavailable")

        store = QdrantStore(collection_name="env_legal_768_index_fields_failure", client=SimpleNamespace(get_collection=_boom))
        assert store.payload_index_fields() == frozenset()


class TestQueryFilterIndexGuard:
    """Store queries must not send filters on unindexed payload keys."""

    @staticmethod
    def _store(collection_name: str, indexed: set[str]) -> QdrantStore:
        client = SimpleNamespace(
            get_collection=lambda name: SimpleNamespace(payload_schema={field: object() for field in indexed}),
        )
        return QdrantStore(collection_name=collection_name, client=client)

    def test_drops_unindexed_flat_keys(self):
        store = self._store("env_legal_768_filter_guard_drop", {"section_number", "document_id"})
        filters = {"section_number": "3", "section_numbers": ["3"]}
        assert store._query_filters(filters) == {"section_number": "3"}

    def test_keeps_indexed_keys(self):
        store = self._store("env_legal_768_filter_guard_keep", {"section_number", "clause_number"})
        filters = {"section_number": "3", "clause_number": "3(1)"}
        assert store._query_filters(filters) == filters

    def test_leaves_preshaped_filters_alone(self):
        store = self._store("env_legal_768_filter_guard_preshaped", {"section_number"})
        shaped = {"must": [{"key": "clause_number", "match": {"value": "3(1)"}}]}
        assert store._query_filters(shaped) is shaped

    def test_unknown_schema_leaves_filters_alone(self):
        client = SimpleNamespace(get_collection=lambda name: (_ for _ in ()).throw(RuntimeError("boom")))
        store = QdrantStore(collection_name="env_legal_768_filter_guard_unknown", client=client)
        filters = {"section_numbers": ["3"]}
        assert store._query_filters(filters) == filters


class TestSiblingFilterField:
    """The clause-sibling fetch must never filter on an unindexed payload key.

    S1 regression: on collections without a ``clause_number`` index the fetch
    sent a filter Qdrant rejected with HTTP 400, so the whole pool came back
    empty for 13 benchmark qids.
    """

    @staticmethod
    def _run(monkeypatch, *, heading_clause: str | None, indexed: set[str]):
        from app.rag.retrieval import food_view
        from app.rag.retrieval.provision_metadata import ProvisionMetadata

        recorded: dict[str, object] = {}

        class FakeStore:
            def __init__(self, collection_name=None):
                self.collection_name = collection_name

            def payload_index_fields(self):
                return frozenset(indexed)

            def scroll_all(self, batch_size=500, filters=None):
                recorded["filters"] = filters
                return []

        monkeypatch.setattr("app.rag.qdrant_client.QdrantStore", FakeStore)
        monkeypatch.setattr(
            "app.rag.retrieval.provision_metadata.derive_provision_metadata_cached",
            lambda chunk: ProvisionMetadata(
                {"commodity": "cumin", "section": heading_clause or "3", "document_id": "doc1"},
            ),
        )

        heading = RetrievedChunk(
            chunk_id="h1",
            score=0.9,
            text="Cumin (Safed Zeera) whole means the dried mature fruits.",
            clause_number=heading_clause,
            section_number=None if heading_clause else "3",
            document_id="doc1",
        )
        view = SimpleNamespace(
            query="cumin standard",
            food=SimpleNamespace(entity="cumin"),
            sibling_budget=[1],
        )
        food_view._fetch_siblings(view, [heading], reranker=None, ctx=SimpleNamespace(collection_name="doc1"), fetch=None)
        return recorded, view

    def test_uses_clause_number_when_indexed(self, monkeypatch):
        recorded, _ = self._run(monkeypatch, heading_clause="2.9.8", indexed={"clause_number", "section_number"})
        assert recorded["filters"] == {"document_id": "doc1", "clause_number": "2.9.8"}

    def test_falls_back_to_section_number_when_clause_not_indexed(self, monkeypatch):
        recorded, _ = self._run(monkeypatch, heading_clause=None, indexed={"section_number"})
        assert recorded["filters"] == {"document_id": "doc1", "section_number": "3"}

    def test_falls_back_to_section_number_when_clause_key_has_no_index(self, monkeypatch):
        recorded, _ = self._run(monkeypatch, heading_clause="2.9.8", indexed={"section_number"})
        assert recorded["filters"] == {"document_id": "doc1", "section_number": "2.9.8"}

    def test_skips_fetch_when_neither_key_is_indexed(self, monkeypatch):
        recorded, view = self._run(monkeypatch, heading_clause="2.9.8", indexed={"document_id"})
        assert "filters" not in recorded
        assert view.sibling_budget == [1]  # budget untouched: no fetch happened

    def test_proceeds_when_schema_is_unreadable(self, monkeypatch):
        """An unreadable schema must not silently disable sibling fetch."""
        recorded, _ = self._run(monkeypatch, heading_clause="2.9.8", indexed=set())
        assert recorded["filters"] == {"document_id": "doc1", "clause_number": "2.9.8"}

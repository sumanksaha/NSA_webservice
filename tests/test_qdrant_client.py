"""Tests for the Agent A Phase 1 Qdrant wrapper (app/rag/qdrant_client.py).

Fully self-contained: the Qdrant client is injected as a mock via the
constructor (mock-injection pattern from ``tests/test_dense_retriever.py``),
so no Qdrant server or ``qdrant-client`` package is required.  The dict
fallback shapes used when ``qdrant-client`` is absent are pinned explicitly
by forcing the model accessor to ``None`` (``store._models = False``), so
the tests pass whether or not the optional dependency is installed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.qdrant_client import DEFAULT_COLLECTION, Point, QdrantStore, dense_search


def _make_mock_point(point_id="c1", score=0.91, **payload_extra):
    payload = {"document_id": "d1", "chunk_text": "text"}
    payload.update(payload_extra)
    return SimpleNamespace(id=point_id, score=score, payload=payload)


def _make_mock_client(has_collection=True, points=None):
    """Mock QdrantClient recording every call for assertion."""
    calls = {"created": [], "indexed": [], "upserted": [], "deleted": [],
             "searched": [], "scrolled": []}

    def search(**kwargs):
        calls["searched"].append(kwargs)
        return points or []

    def scroll(**kwargs):
        calls["scrolled"].append(kwargs)
        return (points or []), None

    client = SimpleNamespace(
        ping=lambda: True,
        collection_exists=lambda name: has_collection,
        create_collection=lambda **kw: calls["created"].append(kw),
        create_payload_index=lambda **kw: calls["indexed"].append(kw),
        upsert=lambda **kw: calls["upserted"].append(kw),
        search=search,
        delete=lambda **kw: calls["deleted"].append(kw),
        scroll=scroll,
    )
    client.calls = calls
    return client


def _make_search_client(accepts: str, points=None):
    """Mock client whose search() explicitly accepts one filter kwarg name.

    Exercises :meth:`QdrantStore._search_filter_kwarg` version detection
    (``qdrant-client`` renamed ``query_filter`` -> ``search_filter``).
    """
    if accepts == "search_filter":

        def search(collection_name, query_vector, limit=10, with_payload=True,
                   with_vectors=False, score_threshold=None, search_filter=None, **kw):
            return points or []
    else:

        def search(collection_name, query_vector, limit=10, with_payload=True,
                   with_vectors=False, score_threshold=None, query_filter=None, **kw):
            return points or []

    return SimpleNamespace(search=search)


class TestQdrantStoreConfig:
    def test_default_collection_and_vector_size(self):
        store = QdrantStore()
        assert store.collection_name == DEFAULT_COLLECTION
        assert store.vector_size == 768

    def test_injected_values_win(self):
        store = QdrantStore(collection_name="custom", vector_size=512)
        assert store.collection_name == "custom"
        assert store.vector_size == 512

    def test_config_values_in_app_context(self):
        from app import create_app

        app = create_app()
        app.config["RAG_QDRANT_COLLECTION"] = "config_coll"
        app.config["RAG_VECTOR_SIZE"] = "512"
        with app.app_context():
            store = QdrantStore()
            assert store.collection_name == "config_coll"
            assert store.vector_size == 512


class TestQdrantStoreHealth:
    def test_ping_true(self):
        store = QdrantStore(client=_make_mock_client())
        assert store.ping() is True

    def test_ping_false_on_exception(self):
        client = SimpleNamespace(ping=lambda: (_ for _ in ()).throw(RuntimeError("down")))
        store = QdrantStore(client=client)
        assert store.ping() is False

    def test_ping_false_without_client(self):
        store = QdrantStore()
        store._get_client = lambda: None
        assert store.ping() is False

    def test_has_collection_true_and_false(self):
        assert QdrantStore(client=_make_mock_client(has_collection=True)).has_collection() is True
        assert QdrantStore(client=_make_mock_client(has_collection=False)).has_collection() is False

    def test_has_collection_false_without_client(self):
        store = QdrantStore()
        store._get_client = lambda: None
        assert store.has_collection() is False

    def test_ping_falls_back_to_info_when_ping_missing(self):
        """qdrant-client 1.19+ removed ping(); the info() root probe is used."""
        client = SimpleNamespace(info=lambda: {"version": "1.19.0"})
        assert QdrantStore(client=client).ping() is True

    def test_ping_falls_back_to_cluster_info_when_no_health_method(self):
        """Last resort: any collection-level call proves the service answers."""
        client = SimpleNamespace(collection_cluster_info=lambda name: {"peer_id": 1})
        assert QdrantStore(client=client).ping() is True

    def test_ping_false_when_all_health_methods_fail(self):
        def boom(*_a, **_kw):
            raise RuntimeError("down")

        client = SimpleNamespace(info=boom, collection_cluster_info=boom)
        assert QdrantStore(client=client).ping() is False

    def test_ping_prefers_ping_over_info(self):
        """Older clients keep ping(); it must win over the fallback."""
        calls = []
        client = SimpleNamespace(
            ping=lambda: calls.append("ping") or True,
            info=lambda: calls.append("info") or True,
        )
        assert QdrantStore(client=client).ping() is True
        assert calls == ["ping"]

    def test_require_client_raises_when_unavailable(self):
        store = QdrantStore()
        store._get_client = lambda: None
        with pytest.raises(RuntimeError, match="Qdrant is unavailable"):
            store._require_client()


class TestQdrantStoreCollection:
    def test_ensure_collection_skips_create_when_exists(self):
        client = _make_mock_client(has_collection=True)
        store = QdrantStore(client=client)
        assert store.ensure_collection() is True
        assert client.calls["created"] == []

    def test_ensure_collection_creates_with_vector_config(self):
        client = _make_mock_client(has_collection=False)
        store = QdrantStore(client=client)
        store._models = False  # qdrant-client absent -> dict fallback
        assert store.ensure_collection() is True
        created = client.calls["created"]
        assert len(created) == 1
        assert created[0]["collection_name"] == DEFAULT_COLLECTION
        assert created[0]["vectors_config"] == {"size": 768, "distance": "Cosine"}

    def test_ensure_collection_creates_payload_indexes(self):
        client = _make_mock_client(has_collection=False)
        store = QdrantStore(client=client)
        assert store.ensure_collection() is True
        indexed_fields = {c["field_name"] for c in client.calls["indexed"]}
        for field in ("document_id", "section_number", "authority", "hierarchy_level"):
            assert field in indexed_fields

    def test_ensure_collection_returns_false_on_error(self):
        def boom(**kw):
            raise RuntimeError("create failed")

        client = _make_mock_client(has_collection=False)
        client.create_collection = boom
        store = QdrantStore(client=client)
        assert store.ensure_collection() is False

    def test_ensure_collection_sparse_enabled_adds_sparse_config(self):
        """Hybrid collections declare a named dense vector + BM25 text_sparse (IDF)."""
        client = _make_mock_client(has_collection=False)
        store = QdrantStore(client=client)
        store._models = False  # dict fallback
        assert store.ensure_collection(sparse_enabled=True) is True
        created = client.calls["created"][0]
        # All vectors must be named in hybrid mode (dense under "dense").
        assert created["vectors_config"] == {"dense": {"size": 768, "distance": "Cosine"}}
        assert created["sparse_vectors_config"] == {"text_sparse": {"modifier": "idf"}}
        assert store.has_sparse_vectors() is True

    def test_ensure_collection_sparse_enabled_named_dense_real_models(self):
        """Real models path: dense is created as a named vector in hybrid mode."""
        client = _make_mock_client(has_collection=False)
        store = QdrantStore(client=client)  # real models (qdrant-client installed)
        assert store.ensure_collection(sparse_enabled=True) is True
        vectors = client.calls["created"][0]["vectors_config"]
        assert list(vectors.keys()) == ["dense"]
        assert vectors["dense"].size == 768

    def test_ensure_collection_sparse_disabled_omits_config(self):
        client = _make_mock_client(has_collection=False)
        store = QdrantStore(client=client)
        store._models = False
        store.ensure_collection(sparse_enabled=False)
        assert "sparse_vectors_config" not in client.calls["created"][0]

    def test_has_sparse_vectors_true_when_collection_declares_sparse(self):
        params = SimpleNamespace(sparse_vectors={"text_sparse": "params"})
        config = SimpleNamespace(params=params)
        info = SimpleNamespace(config=config)
        client = SimpleNamespace(get_collection=lambda name: info)
        assert QdrantStore(client=client).has_sparse_vectors() is True

    def test_has_sparse_vectors_false_for_dense_only_collection(self):
        params = SimpleNamespace(sparse_vectors=None)
        info = SimpleNamespace(config=SimpleNamespace(params=params))
        client = SimpleNamespace(get_collection=lambda name: info)
        assert QdrantStore(client=client).has_sparse_vectors() is False

    def test_has_sparse_vectors_false_when_get_collection_fails(self):
        client = SimpleNamespace(get_collection=lambda name: (_ for _ in ()).throw(RuntimeError("down")))
        assert QdrantStore(client=client).has_sparse_vectors() is False

    def test_create_payload_index_success_and_failure(self):
        client = _make_mock_client()
        store = QdrantStore(client=client)
        assert store.create_payload_index("section_number") is True
        assert client.calls["indexed"][0]["field_schema"] == "keyword"

        def boom(**kw):
            raise RuntimeError("index failed")

        store2 = QdrantStore(client=SimpleNamespace(create_payload_index=boom))
        assert store2.create_payload_index("section_number") is False


class TestQdrantStorePoints:
    def test_upsert_points_with_sparse_vector_uses_named_vectors(self):
        """Hybrid points upsert as {"dense": [...], "text_sparse": {...}}."""
        client = _make_mock_client()
        store = QdrantStore(client=client)
        store._models = False
        points = [
            Point(
                id="c1",
                vector=[0.1] * 768,
                sparse_vector={"indices": [1, 5], "values": [0.9, 0.3]},
                payload={"document_id": "d1"},
            )
        ]
        store.upsert_points(points)
        structs = client.calls["upserted"][0]["points"]
        assert structs[0]["vector"] == {
            "dense": [0.1] * 768,
            "text_sparse": {"indices": [1, 5], "values": [0.9, 0.3]},
        }

    def test_real_models_path_named_vectors(self):
        """Production path (qdrant-client installed): pydantic shapes coerce.

        Unlike the other sparse tests (which force the dict fallback via
        ``_models = False``), this exercises the REAL models module: raw
        sparse dicts must coerce into ``SparseVector``/``Prefetch``/
        ``FusionQuery`` exactly as production passes them.
        """
        from qdrant_client import http as _http

        models = _http.models

        point = models.PointStruct(
            id="c1",
            vector={
                "dense": [0.1] * 768,
                "text_sparse": {"indices": [1, 5], "values": [0.9, 0.4]},
            },
            payload={"document_id": "d1"},
        )
        assert point.vector["dense"] == [0.1] * 768
        assert point.vector["text_sparse"].indices == [1, 5]
        assert point.vector["text_sparse"].values == [0.9, 0.4]

        sparse_cfg = models.SparseVectorParams(modifier=models.Modifier.IDF)
        assert sparse_cfg.modifier == models.Modifier.IDF

        prefetch = models.Prefetch(
            query={"indices": [1], "values": [1.0]}, using="text_sparse", limit=50
        )
        assert prefetch.using == "text_sparse"

        fusion = models.FusionQuery(fusion=models.Fusion.RRF)
        assert fusion.fusion == models.Fusion.RRF

    def test_upsert_points_real_models_struct(self):
        """Real PointStruct produced by upsert_points for hybrid points."""
        client = _make_mock_client()
        store = QdrantStore(client=client)  # _models left as-is (real client)
        store.upsert_points(
            [
                Point(
                    id="c1",
                    vector=[0.1] * 768,
                    sparse_vector={"indices": [1, 5], "values": [0.9, 0.4]},
                    payload={"document_id": "d1"},
                )
            ]
        )
        struct = client.calls["upserted"][0]["points"][0]
        assert struct.vector["dense"] == [0.1] * 768
        assert struct.vector["text_sparse"].indices == [1, 5]

    def test_upsert_points_without_sparse_keeps_flat_vector(self):
        client = _make_mock_client()
        store = QdrantStore(client=client)
        store._models = False
        store.upsert_points([Point(id="c1", vector=[0.1] * 768, payload={})])
        structs = client.calls["upserted"][0]["points"]
        assert structs[0]["vector"] == [0.1] * 768

    def test_upsert_points_passes_dict_structs_and_returns_count(self):
        client = _make_mock_client()
        store = QdrantStore(client=client)
        store._models = False  # qdrant-client absent -> plain dicts
        points = [
            Point(id="c1", vector=[0.1] * 768, payload={"document_id": "d1"}),
            Point(id="c2", vector=[0.2] * 768, payload={"document_id": "d1"}),
        ]
        assert store.upsert_points(points) == 2
        upserted = client.calls["upserted"]
        assert len(upserted) == 1
        structs = upserted[0]["points"]
        assert structs[0] == {"id": "c1", "vector": [0.1] * 768, "payload": {"document_id": "d1"}}
        assert upserted[0]["collection_name"] == DEFAULT_COLLECTION

    def test_upsert_points_batches_large_payloads(self, monkeypatch):
        """Regression (2026-08-09): a single giant upsert (2523 hybrid points)
        was dropped by Qdrant Cloud ("connection forcibly closed").  Points are
        now upserted in small request batches."""
        monkeypatch.setattr("app.rag.qdrant_client.UPSERT_BATCH_SIZE", 40)
        client = _make_mock_client()
        store = QdrantStore(client=client)
        store._models = False
        points = [Point(id=f"c{i}", vector=[0.1] * 768, payload={"document_id": "d1"}) for i in range(100)]

        assert store.upsert_points(points) == 100
        upserted = client.calls["upserted"]
        assert [len(call["points"]) for call in upserted] == [40, 40, 20]
        assert all(call["collection_name"] == DEFAULT_COLLECTION for call in upserted)
        # Order preserved across batches.
        assert upserted[1]["points"][0]["id"] == "c40"

    def test_upsert_points_batch_retries_once_then_succeeds(self):
        """A transient failure on one batch is retried in place and does not
        abort the remaining batches."""
        attempts = {"n": 0}
        real = _make_mock_client()

        def flaky_upsert(**kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("connection forcibly closed")
            real.calls["upserted"].append(kwargs)

        client = real
        client.upsert = flaky_upsert
        store = QdrantStore(client=client)
        store._models = False
        points = [Point(id=f"c{i}", vector=[0.1] * 768, payload={}) for i in range(3)]

        assert store.upsert_points(points) == 3
        assert len(real.calls["upserted"]) == 1
        assert len(real.calls["upserted"][0]["points"]) == 3

    def test_upsert_points_batch_failure_raises_with_batch_number(self):
        """A batch that fails twice surfaces a RuntimeError naming the batch."""
        client = _make_mock_client()

        def failing_upsert(**kwargs):
            raise ConnectionError("boom")

        client.upsert = failing_upsert
        store = QdrantStore(client=client)
        store._models = False
        points = [Point(id=f"c{i}", vector=[0.1] * 768, payload={}) for i in range(3)]

        with pytest.raises(RuntimeError, match="batch #0.*failed after retry"):
            store.upsert_points(points)

    def test_search_points_kwargs_and_result_mapping(self):
        points = [_make_mock_point("c1", 0.91, section_number="55")]
        client = _make_mock_client(points=points)
        store = QdrantStore(client=client)
        results = store.search_points([0.1] * 768, top_k=5)
        kwargs = client.calls["searched"][0]
        assert kwargs["collection_name"] == DEFAULT_COLLECTION
        assert kwargs["query_vector"] == [0.1] * 768
        assert kwargs["limit"] == 5
        assert kwargs["with_payload"] is True
        assert kwargs["with_vectors"] is False
        assert results[0] == {"id": "c1", "score": 0.91, "payload": {"document_id": "d1", "chunk_text": "text", "section_number": "55"}}

    def test_search_points_score_threshold_included(self):
        client = _make_mock_client(points=[_make_mock_point()])
        store = QdrantStore(client=client)
        store.search_points([0.1] * 768, top_k=3, score_threshold=0.7)
        assert client.calls["searched"][0]["score_threshold"] == 0.7

    def test_search_points_omits_filter_when_none(self):
        client = _make_mock_client(points=[_make_mock_point()])
        store = QdrantStore(client=client)
        store.search_points([0.1] * 768)
        kwargs = client.calls["searched"][0]
        assert "search_filter" not in kwargs
        assert "query_filter" not in kwargs

    def test_search_points_uses_search_filter_when_accepted(self):
        received = {}

        def search(collection_name, query_vector, limit=10, with_payload=True,
                   with_vectors=False, score_threshold=None, search_filter=None, **kw):
            received.update(search_filter=search_filter, **kw)
            return [_make_mock_point()]

        store = QdrantStore(client=SimpleNamespace(search=search))
        store.search_points([0.1] * 768, filters={"section_number": "55"})
        assert received["search_filter"]["must"][0] == {
            "key": "section_number",
            "match": {"value": "55"},
        }

    def test_search_points_query_points_fallback(self):
        """qdrant-client >= 1.12: query_points -> QueryResponse.points."""
        points = [_make_mock_point("c1", 0.91, section_number="55")]
        received = {}

        def query_points(collection_name, query, limit=10, with_payload=True,
                         with_vectors=False, score_threshold=None, query_filter=None, **kw):
            received.update(
                collection_name=collection_name, query=query, limit=limit,
                query_filter=query_filter, with_payload=with_payload,
                with_vectors=with_vectors, score_threshold=score_threshold,
            )
            return SimpleNamespace(points=points)

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        results = store.search_points([0.1] * 768, top_k=5, score_threshold=0.7, filters={"section_number": "55"})
        assert received["collection_name"] == DEFAULT_COLLECTION
        assert received["query"] == [0.1] * 768
        assert received["limit"] == 5
        assert received["score_threshold"] == 0.7
        assert received["query_filter"]["must"][0] == {"key": "section_number", "match": {"value": "55"}}
        assert results[0] == {"id": "c1", "score": 0.91, "payload": {"document_id": "d1", "chunk_text": "text", "section_number": "55"}}

    def test_search_points_query_points_without_filter(self):
        received = {}

        def query_points(collection_name, query, limit=10, with_payload=True,
                         with_vectors=False, score_threshold=None, query_filter=None, **kw):
            received.update(query=query, query_filter=query_filter)
            return SimpleNamespace(points=[])

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        assert store.search_points([0.1] * 768) == []
        assert received["query_filter"] is None

    def test_dense_search_helper_prefers_search_when_present(self):
        """Old clients keep search(); the helper must not switch to query_points."""
        calls = []

        def search(**kw):
            calls.append(("search", kw))
            return [_make_mock_point()]

        def query_points(**kw):
            calls.append(("query_points", kw))
            return SimpleNamespace(points=[])

        client = SimpleNamespace(search=search, query_points=query_points)
        results = dense_search(client, collection_name="c", vector=[0.1], limit=2)
        assert calls == [("search", {
            "collection_name": "c", "query_vector": [0.1], "limit": 2,
            "with_payload": True, "with_vectors": False,
        })]
        assert results[0].id == "c1"

    def test_search_sparse_uses_query_points_with_sparse_vector(self):
        """BM25 sparse search: query_points with SparseVector + using=text_sparse."""
        points = [_make_mock_point("c1", 0.87)]
        received = {}

        def query_points(collection_name, query, limit=10, using=None, with_payload=True,
                         with_vectors=False, score_threshold=None, query_filter=None, **kw):
            received.update(
                collection_name=collection_name, query=query, limit=limit,
                using=using, query_filter=query_filter, score_threshold=score_threshold,
            )
            return SimpleNamespace(points=points)

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        results = store.search_sparse(
            {"indices": [3, 9], "values": [0.8, 0.2]},
            top_k=4, score_threshold=0.5, filters={"document_type": "act"},
        )
        assert received["collection_name"] == DEFAULT_COLLECTION
        # Raw dict converted to models.SparseVector for the real client.
        assert received["query"].indices == [3, 9]
        assert received["query"].values == [0.8, 0.2]
        assert received["using"] == "text_sparse"
        assert received["limit"] == 4
        assert received["score_threshold"] == 0.5
        assert received["query_filter"]["must"][0] == {"key": "document_type", "match": {"value": "act"}}
        assert results[0] == {"id": "c1", "score": 0.87, "payload": {"document_id": "d1", "chunk_text": "text"}}

    def test_sparse_query_helper_dict_fallback_when_no_models(self):
        """Without qdrant-client the raw {indices, values} dict is sent."""
        from app.rag.qdrant_client import _sparse_query

        assert _sparse_query({"indices": [1], "values": [0.5]}, None) == {
            "indices": [1], "values": [0.5],
        }

    def test_search_sparse_legacy_search_path(self):
        """Clients without query_points fall back to search(query_vector=...)."""
        received = {}

        def search(collection_name, query_vector, limit=10, using=None, with_payload=True,
                   with_vectors=False, **kw):
            received.update(query_vector=query_vector, using=using, limit=limit)
            return []

        store = QdrantStore(client=SimpleNamespace(search=search))
        assert store.search_sparse({"indices": [1], "values": [1.0]}) == []
        assert received["query_vector"].indices == [1]
        assert received["using"] == "text_sparse"

    def test_hybrid_search_prefetch_and_rrf(self):
        """Server-side fusion: prefetch dense+sparse, Fusion.RRF query."""
        points = [_make_mock_point("c1", 0.99)]
        received = {}

        def query_points(collection_name, prefetch, query, limit=10, with_payload=True,
                         with_vectors=False, query_filter=None, **kw):
            received.update(
                collection_name=collection_name, prefetch=prefetch, query=query,
                limit=limit, query_filter=query_filter,
            )
            return SimpleNamespace(points=points)

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        store._models = False  # dict fallback shapes
        results = store.hybrid_search(
            [0.1] * 768,
            {"indices": [1], "values": [1.0]},
            top_k=5,
            filters={"is_current": True},
        )
        assert received["collection_name"] == DEFAULT_COLLECTION
        assert received["limit"] == 5
        assert received["query"] == {"fusion": "rrf"}
        assert len(received["prefetch"]) == 2
        assert received["prefetch"][0]["using"] == "dense"
        assert received["prefetch"][0]["query"] == [0.1] * 768
        assert received["prefetch"][1]["using"] == "text_sparse"
        assert received["prefetch"][1]["query"] == {"indices": [1], "values": [1.0]}
        assert received["prefetch"][0]["limit"] >= 25
        assert received["query_filter"]["must"][0] == {"key": "is_current", "match": {"value": True}}
        assert results[0]["id"] == "c1"

    def test_hybrid_search_real_models_uses_sparse_vector_prefetch(self):
        """Real client path: prefetch sparse uses SparseVector + Fusion.RRF."""
        points = [_make_mock_point("c1", 0.99)]
        received = {}

        def query_points(collection_name, prefetch, query, limit=10, with_payload=True,
                         with_vectors=False, query_filter=None, **kw):
            received.update(prefetch=prefetch, query=query)
            return SimpleNamespace(points=points)

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        store.hybrid_search([0.1] * 768, {"indices": [1], "values": [1.0]}, top_k=5)
        assert received["query"].fusion == "rrf"
        sparse_prefetch = received["prefetch"][1]
        assert sparse_prefetch.using == "text_sparse"
        assert sparse_prefetch.query.indices == [1]

    def test_hybrid_search_raises_without_query_points(self):
        store = QdrantStore(client=SimpleNamespace(search=lambda **kw: []))
        with pytest.raises(RuntimeError, match="query_points"):
            store.hybrid_search([0.1], {"indices": [], "values": []})

    def test_search_points_passes_using_when_sparse_collection(self):
        """Named-vector collections require using="dense" on dense queries."""
        params = SimpleNamespace(sparse_vectors={"text_sparse": "p"})
        info = SimpleNamespace(config=SimpleNamespace(params=params))
        received = {}

        def search(**kw):
            received.update(kw)
            return []

        client = SimpleNamespace(get_collection=lambda name: info, search=search)
        store = QdrantStore(client=client)
        store.search_points([0.1] * 768)
        assert received["using"] == "dense"

    def test_search_points_filter_detection_both_versions(self):
        # search_filter variant: detection returns "search_filter".
        client_sf = _make_search_client(accepts="search_filter")
        assert QdrantStore._search_filter_kwarg(client_sf) == "search_filter"
        # query_filter variant: detection falls back to "query_filter".
        client_qf = _make_search_client(accepts="query_filter")
        assert QdrantStore._search_filter_kwarg(client_qf) == "query_filter"

    def test_delete_points_by_ids(self):
        client = _make_mock_client()
        store = QdrantStore(client=client)
        store._models = False
        assert store.delete_points(point_ids=["c1", "c2"]) == 2
        assert client.calls["deleted"][0]["points_selector"] == {"points": ["c1", "c2"]}

    def test_delete_points_by_document_id(self):
        client = _make_mock_client()
        store = QdrantStore(client=client)
        store._models = False
        assert store.delete_points(document_id="d1") == 1
        selector = client.calls["deleted"][0]["points_selector"]
        assert selector["filter"]["must"][0] == {"key": "document_id", "match": {"value": "d1"}}

    def test_delete_points_requires_argument(self):
        store = QdrantStore(client=_make_mock_client())
        with pytest.raises(ValueError, match="point_ids and/or document_id"):
            store.delete_points()

    def test_scroll_points_mapping(self):
        points = [_make_mock_point("c1", 0.5, document_title="FSS Act")]
        client = _make_mock_client(points=points)
        store = QdrantStore(client=client)
        results = store.scroll_points(limit=50)
        assert client.calls["scrolled"][0]["limit"] == 50
        assert results[0]["id"] == "c1"
        assert results[0]["payload"]["document_title"] == "FSS Act"

    def test_scroll_all_paginates_with_vectors(self):
        """scroll_all iterates offset pages and includes vectors when asked."""
        points = [
            SimpleNamespace(id="c1", payload={"document_id": "d1"}, vector=[0.1] * 768),
            SimpleNamespace(id="c2", payload={"document_id": "d2"}, vector=[0.2] * 768),
        ]
        calls = []

        def scroll(collection_name, limit=100, offset=None, with_payload=True,
                   with_vectors=False, scroll_filter=None):
            calls.append((limit, offset, with_vectors))
            if offset is None:
                return points[:1], "page2"
            return points[1:], None

        store = QdrantStore(client=SimpleNamespace(scroll=scroll))
        results = store.scroll_all(with_vectors=True, batch_size=1)
        assert len(results) == 2
        assert results[0] == {"id": "c1", "payload": {"document_id": "d1"}, "vector": [0.1] * 768}
        assert results[1] == {"id": "c2", "payload": {"document_id": "d2"}, "vector": [0.2] * 768}
        assert calls == [(1, None, True), (1, "page2", True)]

    def test_scroll_all_without_vectors(self):
        """Backup-safe default: vectors omitted unless explicitly requested."""
        points = [SimpleNamespace(id="c1", payload={"document_id": "d1"}, vector=[0.1])]
        store = QdrantStore(client=SimpleNamespace(scroll=lambda **kw: (points, None)))
        results = store.scroll_all(with_vectors=False)
        assert results == [{"id": "c1", "payload": {"document_id": "d1"}}]


class TestBuildFilter:
    def test_flat_filter_to_qdrant_must_shape(self):
        flt = QdrantStore._build_filter({"section_number": "55", "is_current": True})
        assert flt == {
            "must": [
                {"key": "section_number", "match": {"value": "55"}},
                {"key": "is_current", "match": {"value": True}},
            ]
        }

    def test_empty_filter_yields_empty_dict(self):
        assert QdrantStore._build_filter({}) == {}

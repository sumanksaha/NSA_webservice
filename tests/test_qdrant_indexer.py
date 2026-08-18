"""Tests for the Agent A Phase 1 Qdrant indexer (app/rag/qdrant_indexer.py).

Two layers:

- **Pipeline** tests exercise ``QdrantIndexer`` with fake store / embedder /
  chunker injected via the constructor (mock-injection pattern from
  ``tests/test_dense_retriever.py``) — no Qdrant server or
  sentence-transformers required.
- **after_flush hook** tests use a real (test-only) SQLAlchemy chunk model
  registered via ``register_chunk_model`` and a fake default indexer injected
  with ``set_default_indexer``, following the FTS5 auto-index test style from
  ``tests/test_search.py::TestAutoIndexHook``.
"""

from __future__ import annotations

import uuid

import pytest

from app.extensions import db
from app.rag.chunker import Chunk
from app.rag.qdrant_indexer import (
    ChunkIngestionResult,
    QdrantIndexer,
    register_chunk_model,
    register_qdrant_hooks,
    set_default_indexer,
    unregister_model,
)

# --------------------------------------------------------------------------- #
# Test fixtures / doubles
# --------------------------------------------------------------------------- #


class _TestQdrantChunk(db.Model):
    """Minimal ORM stand-in for the planned ``LegalChunk`` model.

    Leading underscore so pytest does not collect it as a test class.
    """

    __tablename__ = "qdrant_indexer_test_chunk"

    id = db.Column(db.String(36), primary_key=True)
    document_id = db.Column(db.String(36), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False, default=0)
    chunk_text = db.Column(db.Text, nullable=False)
    section_number = db.Column(db.String(32), nullable=True)


class _TestQdrantDocument(db.Model):
    """Minimal ORM stand-in for the planned ``LegalDocument`` model."""

    __tablename__ = "qdrant_indexer_test_document"

    id = db.Column(db.String(36), primary_key=True)


class _TestPayloadChunk(db.Model):
    """Chunk model exposing an explicit ``to_payload()`` method."""

    __tablename__ = "qdrant_indexer_test_payload_chunk"

    id = db.Column(db.String(36), primary_key=True)
    document_id = db.Column(db.String(36), nullable=False)
    chunk_text = db.Column(db.Text, nullable=False)

    def to_payload(self):
        return {
            "chunk_id": self.id,
            "document_id": self.document_id,
            "chunk_index": 7,
            "chunk_text": self.chunk_text,
            "document_type": "act",
            "marker": "from_to_payload",
        }


class _FakeChunker:
    def __init__(self, chunks):
        self._chunks = chunks
        self.embedding_model = "test-model"

    def chunk_text(self, text, document=None):
        return self._chunks


class _FakeEmbedder:
    def __init__(self, vector_size=768, fail=False, validate_result=True):
        self.vector_size = vector_size
        self._fail = fail
        self.validate_result = validate_result

    def embed_chunks(self, chunks):
        if self._fail:
            raise RuntimeError("embedding failed")
        return [[0.1] * self.vector_size for _ in chunks]

    def validate_vector_size(self, expected=None):
        return self.validate_result


class _FakeStore:
    def __init__(self):
        self.points = []
        self.upsert_attempts = 0
        self.failures = 0
        self.deleted_ids = []
        self.deleted_docs = []
        self.ensure_calls = 0
        self.ping_ok = True

    def upsert_points(self, points):
        self.upsert_attempts += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("transient qdrant error")
        self.points.extend(points)
        return len(points)

    def delete_points(self, point_ids=None, document_id=None):
        if point_ids:
            self.deleted_ids.extend(point_ids)
        if document_id:
            self.deleted_docs.append(document_id)
        return len(point_ids or []) or 1

    def ensure_collection(self, create_payload_indexes=True):
        self.ensure_calls += 1
        return True

    def ping(self):
        return self.ping_ok


class _FakeIndexer:
    """Records what the after_flush hook delegates to it."""

    def __init__(self):
        self.upserted_payloads = []
        self.deleted_ids = []
        self.deleted_docs = []

    def sync_payloads(self, payloads):
        self.upserted_payloads.extend(payloads)
        return ChunkIngestionResult(chunk_count=len(payloads), points_upserted=len(payloads))

    def remove_chunks(self, point_ids):
        self.deleted_ids.extend(point_ids)

    def remove_document(self, document_id):
        self.deleted_docs.append(document_id)


def _make_chunk(index, document_id="doc-1", text=None):
    return Chunk(
        chunk_id=f"c{index}",
        document_id=document_id,
        chunk_index=index,
        chunk_text=text or f"Section {index} of the Act.",
        section_number=str(index),
        document_type="act",
    )


@pytest.fixture
def test_client():
    """App with the test chunk table created (mirrors other test files)."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            yield client


@pytest.fixture
def hook_env(test_client):
    """Register the test chunk model + inject a fake default indexer."""
    register_qdrant_hooks()  # idempotent; attached at app boot already
    fake = _FakeIndexer()
    register_chunk_model(_TestQdrantChunk)
    set_default_indexer(fake)
    yield fake
    unregister_model(_TestQdrantChunk)
    set_default_indexer(None)


# --------------------------------------------------------------------------- #
# Pipeline tests
# --------------------------------------------------------------------------- #


class TestQdrantIndexerPipeline:
    def _indexer(self, store=None, embedder=None, chunker=None):
        return QdrantIndexer(
            store=store or _FakeStore(),
            embedder=embedder or _FakeEmbedder(),
            chunker=chunker or _FakeChunker([_make_chunk(0), _make_chunk(1)]),
        )

    def test_index_document_chunks_embeds_and_upserts(self):
        store = _FakeStore()
        idx = self._indexer(store=store)
        result = idx.index_document(
            "full act text",
            {"document_id": "doc-1", "type": "act", "title": "FSS Act"},
        )
        assert result.ok
        assert result.chunk_count == 2
        assert result.points_upserted == 2
        assert result.document_id == "doc-1"
        assert result.document_type == "act"
        assert result.embedding_model == "test-model"
        assert result.vector_size == 768
        assert len(store.points) == 2
        # Points carry the §5.1 payload (document_id + section_number).
        payload = store.points[0].payload
        assert payload["document_id"] == "doc-1"
        assert payload["section_number"] == "0"

    def test_index_document_empty_text_no_upsert(self):
        store = _FakeStore()
        chunker = _FakeChunker([])
        idx = self._indexer(store=store, chunker=chunker)
        result = idx.index_document("", {"document_id": "doc-empty"})
        assert result.ok  # nothing to index is not an error
        assert result.chunk_count == 0
        assert result.points_upserted == 0
        assert store.points == []

    def test_sync_chunks_payload_schema(self):
        store = _FakeStore()
        idx = self._indexer(store=store)
        chunks = [_make_chunk(3, document_id="doc-9", text="Penalties under Section 55.")]
        result = idx.sync_chunks(chunks)
        assert result.ok
        payload = store.points[0].payload
        for key in (
            "chunk_id", "document_id", "chunk_text", "chunk_index",
            "section_number", "document_type", "hierarchy_level",
            "parent_chunk_id", "citations", "references", "confidence",
            "embedding_model", "created_at",
        ):
            assert key in payload, f"payload missing §5.1 field {key!r}"

    def test_embedding_failure_reported_in_result(self):
        idx = self._indexer(embedder=_FakeEmbedder(fail=True))
        result = idx.index_document("text", {"document_id": "doc-1"})
        assert not result.ok
        assert result.points_upserted == 0
        assert any("embedding failed" in e for e in result.errors)

    def test_upsert_retried_once_then_fails(self):
        store = _FakeStore()
        store.failures = 99  # every attempt raises
        idx = self._indexer(store=store)
        result = idx.sync_chunks([_make_chunk(0)])
        assert not result.ok
        assert store.upsert_attempts == 2  # initial + one retry
        assert any("after retry" in e for e in result.errors)

    def test_upsert_recovers_on_second_attempt(self):
        store = _FakeStore()
        store.failures = 1  # first attempt raises, retry succeeds
        idx = self._indexer(store=store)
        result = idx.sync_chunks([_make_chunk(0)])
        assert result.ok
        assert store.upsert_attempts == 2
        assert len(store.points) == 1

    def test_vector_size_mismatch_aborts_before_embedding(self):
        embedder = _FakeEmbedder(validate_result=False)
        idx = self._indexer(embedder=embedder)
        result = idx.sync_chunks([_make_chunk(0)])
        assert not result.ok
        assert any("vector size" in e for e in result.errors)
        assert result.points_upserted == 0

    def test_remove_chunks_and_remove_document_delegate_to_store(self):
        store = _FakeStore()
        idx = self._indexer(store=store)
        assert idx.remove_chunks(["c1", "c2"]) == 2
        assert store.deleted_ids == ["c1", "c2"]
        assert idx.remove_document("doc-1") == 1
        assert store.deleted_docs == ["doc-1"]

    def test_ensure_collection_and_ping_delegate(self):
        store = _FakeStore()
        idx = self._indexer(store=store)
        assert idx.ping() is True
        assert idx.ensure_collection() is True
        assert store.ensure_calls == 1


# --------------------------------------------------------------------------- #
# BM25 sparse ingestion (2026-08-09)
# --------------------------------------------------------------------------- #


class _FakeSparseStore(_FakeStore):
    """Fake store whose collection declares BM25 sparse vectors."""

    def __init__(self, sparse=True):
        super().__init__()
        self._sparse = sparse

    def has_sparse_vectors(self):
        return self._sparse

    @property
    def collection_name(self):
        return "test_coll"


class _FakeSparseEmbedder:
    def __init__(self, fail=False):
        self._fail = fail

    def is_available(self):
        return not self._fail

    def embed_chunks(self, chunks):
        if self._fail:
            raise RuntimeError("sparse embedding failed")
        return [{"indices": [i], "values": [0.9]} for i in range(len(chunks))]


class TestQdrantIndexerSparseIngestion:
    """Sync upserts named dense+sparse vectors when the collection supports it."""

    def test_sparse_vectors_upserted_when_collection_supports(self):
        store = _FakeSparseStore(sparse=True)
        sparse_embedder = _FakeSparseEmbedder()
        idx = QdrantIndexer(
            store=store,
            embedder=_FakeEmbedder(),
            chunker=_FakeChunker([_make_chunk(0), _make_chunk(1)]),
            sparse_embedder=sparse_embedder,
        )
        result = idx.index_document("text", {"document_id": "doc-1"})
        assert result.ok
        assert result.points_upserted == 2
        assert all(p.sparse_vector is not None for p in store.points)
        assert store.points[0].sparse_vector == {"indices": [0], "values": [0.9]}

    def test_dense_only_collection_skips_sparse(self):
        store = _FakeSparseStore(sparse=False)
        idx = QdrantIndexer(
            store=store,
            embedder=_FakeEmbedder(),
            chunker=_FakeChunker([_make_chunk(0)]),
            sparse_embedder=_FakeSparseEmbedder(),
        )
        result = idx.sync_chunks([_make_chunk(0)])
        assert result.ok
        assert store.points[0].sparse_vector is None

    def test_sparse_embedder_unavailable_keeps_dense_upsert(self):
        store = _FakeSparseStore(sparse=True)
        idx = QdrantIndexer(
            store=store,
            embedder=_FakeEmbedder(),
            chunker=_FakeChunker([_make_chunk(0)]),
            sparse_embedder=_FakeSparseEmbedder(fail=True),
        )
        result = idx.sync_chunks([_make_chunk(0)])
        assert result.ok  # dense-only upsert still succeeds
        assert store.points[0].sparse_vector is None

    def test_store_without_sparse_capability_skips_sparse(self):
        store = _FakeStore()  # no has_sparse_vectors method
        idx = QdrantIndexer(
            store=store,
            embedder=_FakeEmbedder(),
            chunker=_FakeChunker([_make_chunk(0)]),
            sparse_embedder=_FakeSparseEmbedder(),
        )
        result = idx.sync_chunks([_make_chunk(0)])
        assert result.ok
        assert store.points[0].sparse_vector is None


# --------------------------------------------------------------------------- #
# after_flush hook tests
# --------------------------------------------------------------------------- #


class TestQdrantIndexerHook:
    def test_after_flush_upserts_new_chunk_rows(self, test_client, hook_env):
        with test_client.application.app_context():
            row = _TestQdrantChunk(
                id=str(uuid.uuid4()), document_id="doc-1", chunk_index=0,
                chunk_text="Section 55 text", section_number="55",
            )
            db.session.add(row)
            db.session.flush()
        assert len(hook_env.upserted_payloads) == 1
        payload = hook_env.upserted_payloads[0]
        assert payload["document_id"] == "doc-1"
        assert payload["chunk_text"] == "Section 55 text"
        assert payload["section_number"] == "55"
        assert payload["chunk_id"] == row.id

    def test_after_flush_upserts_dirty_chunk_rows(self, test_client, hook_env):
        with test_client.application.app_context():
            row = _TestQdrantChunk(
                id=str(uuid.uuid4()), document_id="doc-1", chunk_index=0,
                chunk_text="original",
            )
            db.session.add(row)
            db.session.commit()
            hook_env.upserted_payloads.clear()
            row.chunk_text = "updated"
            db.session.flush()
        assert len(hook_env.upserted_payloads) == 1
        assert hook_env.upserted_payloads[0]["chunk_text"] == "updated"

    def test_after_flush_removes_deleted_chunk_rows(self, test_client, hook_env):
        with test_client.application.app_context():
            row = _TestQdrantChunk(
                id="chunk-del-1", document_id="doc-1", chunk_index=0,
                chunk_text="gone",
            )
            db.session.add(row)
            db.session.commit()
            hook_env.upserted_payloads.clear()  # ignore the insert's upsert
            db.session.delete(row)
            db.session.flush()
        assert hook_env.deleted_ids == ["chunk-del-1"]
        assert hook_env.upserted_payloads == []

    def test_after_flush_ignores_unregistered_models(self, test_client):
        from app.rag import qdrant_indexer as qi

        fake = _FakeIndexer()
        qi.unregister_model(_TestQdrantChunk)
        qi.set_default_indexer(fake)
        try:
            with test_client.application.app_context():
                row = _TestQdrantChunk(
                    id=str(uuid.uuid4()), document_id="doc-1", chunk_index=0,
                    chunk_text="not indexed",
                )
                db.session.add(row)
                db.session.flush()
        finally:
            qi.set_default_indexer(None)
        assert fake.upserted_payloads == []
        assert fake.deleted_ids == []

    def test_after_flush_swallows_indexer_errors(self, test_client, hook_env):
        def boom(payloads):
            raise RuntimeError("qdrant down")

        hook_env.sync_payloads = boom
        with test_client.application.app_context():
            row = _TestQdrantChunk(
                id=str(uuid.uuid4()), document_id="doc-1", chunk_index=0,
                chunk_text="text",
            )
            db.session.add(row)
            # A Qdrant failure must never break the caller's transaction.
            db.session.flush()

    def test_after_flush_removes_document_chunks_on_document_delete(self, test_client):
        """Deleting a registered document model removes all its chunks."""
        from app.rag import qdrant_indexer as qi

        qi.register_document_model(_TestQdrantDocument)
        fake = _FakeIndexer()
        qi.set_default_indexer(fake)
        try:
            with test_client.application.app_context():
                doc = _TestQdrantDocument(id="doc-del-1")
                db.session.add(doc)
                db.session.commit()
                db.session.delete(doc)
                db.session.flush()
        finally:
            qi.unregister_model(_TestQdrantDocument)
            qi.set_default_indexer(None)
        assert fake.deleted_docs == ["doc-del-1"]
        assert fake.deleted_ids == []

    def test_after_flush_prefers_to_payload_method(self, test_client):
        """Chunk models with ``to_payload()`` use it over attribute duck-typing."""
        from app.rag import qdrant_indexer as qi

        qi.register_chunk_model(_TestPayloadChunk)
        fake = _FakeIndexer()
        qi.set_default_indexer(fake)
        try:
            with test_client.application.app_context():
                row = _TestPayloadChunk(id="p1", document_id="doc-1", chunk_text="text")
                db.session.add(row)
                db.session.flush()
        finally:
            qi.unregister_model(_TestPayloadChunk)
            qi.set_default_indexer(None)
        assert len(fake.upserted_payloads) == 1
        payload = fake.upserted_payloads[0]
        # Only to_payload() could produce these fields.
        assert payload["marker"] == "from_to_payload"
        assert payload["document_type"] == "act"
        assert payload["chunk_index"] == 7
        assert payload["chunk_id"] == "p1"

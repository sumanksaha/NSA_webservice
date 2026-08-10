"""Tests for the BM25 sparse embedding service (app/rag/sparse_embedding.py).

Covers the fastembed-backed ``SparseEmbeddingService``: JSON-safe
``{indices, values}`` output, batch/chunk helpers, lazy import degradation
when fastembed is missing, and config resolution.  No fastembed model is
downloaded — a fake embedder is injected via the constructor (mock-injection
pattern from ``tests/test_embedding_service.py``).
"""

from __future__ import annotations

import pytest

from app.rag.sparse_embedding import DEFAULT_SPARSE_MODEL, SparseEmbeddingService


class _FakeSparseEmbedder:
    """Minimal fastembed TextSparseEmbedding double.

    ``embed()`` is a generator of objects with ``indices`` / ``values``
    (numpy-like arrays) — the real contract of fastembed's sparse output.
    """

    def __init__(self, result=None):
        self._result = result or {"indices": [1, 7, 42], "values": [0.9, 0.4, 0.2]}

    def embed(self, texts):
        for _text in texts:
            yield type("SparseOut", (), {
                "indices": self._result["indices"],
                "values": self._result["values"],
            })()


class _ChunkLike:
    def __init__(self, text):
        self.chunk_text = text


class TestSparseEmbeddingService:
    def test_model_name_default(self):
        assert SparseEmbeddingService().model_name == DEFAULT_SPARSE_MODEL

    def test_model_name_override(self):
        assert SparseEmbeddingService(model_name="x/bm25").model_name == "x/bm25"

    def test_model_name_from_config(self):
        from app import create_app

        app = create_app()
        app.config["RAG_SPARSE_MODEL"] = "custom/bm25"
        with app.app_context():
            assert SparseEmbeddingService().model_name == "custom/bm25"

    def test_embed_sparse_returns_json_safe_dict(self):
        svc = SparseEmbeddingService(embedder=_FakeSparseEmbedder())
        result = svc.embed_sparse("section 55 penalties")
        assert result == {"indices": [1, 7, 42], "values": [0.9, 0.4, 0.2]}
        assert all(isinstance(i, int) for i in result["indices"])
        assert all(isinstance(v, float) for v in result["values"])

    def test_embed_batch(self):
        svc = SparseEmbeddingService(embedder=_FakeSparseEmbedder())
        results = svc.embed_batch(["a", "b"])
        assert len(results) == 2
        assert results[0]["indices"] == [1, 7, 42]

    def test_embed_batch_empty(self):
        svc = SparseEmbeddingService(embedder=_FakeSparseEmbedder())
        assert svc.embed_batch([]) == []

    def test_embed_chunks_strings_and_objects(self):
        svc = SparseEmbeddingService(embedder=_FakeSparseEmbedder())
        from_strings = svc.embed_chunks(["t1", "t2"])
        assert len(from_strings) == 2
        from_objects = svc.embed_chunks([_ChunkLike("t1")])
        assert len(from_objects) == 1

    def test_embed_chunks_empty(self):
        svc = SparseEmbeddingService(embedder=_FakeSparseEmbedder())
        assert svc.embed_chunks([]) == []

    def test_is_available_with_injected_embedder(self):
        assert SparseEmbeddingService(embedder=_FakeSparseEmbedder()).is_available() is True

    def test_degrades_when_fastembed_missing(self, monkeypatch):
        """fastembed absent -> is_available False, embed raises RuntimeError."""
        import sys

        monkeypatch.setitem(sys.modules, "fastembed", None)  # import fails
        svc = SparseEmbeddingService()
        assert svc.is_available() is False
        with pytest.raises(RuntimeError, match="fastembed"):
            svc.embed_sparse("q")

    def test_require_embedder_raises_when_unavailable(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "fastembed", None)
        svc = SparseEmbeddingService()
        with pytest.raises(RuntimeError, match="fastembed"):
            svc._require_embedder()

    def test_embedder_construction_failure_degrades(self, monkeypatch):
        """Model load failure (e.g. bad model name) -> is_available False."""
        import sys
        import types

        class _BoomTextSparseEmbedding:
            def __init__(self, model_name=None, **kw):
                raise RuntimeError("model download failed")

        fake_fastembed = types.ModuleType("fastembed")
        fake_fastembed.TextSparseEmbedding = _BoomTextSparseEmbedding
        monkeypatch.setitem(sys.modules, "fastembed", fake_fastembed)
        assert SparseEmbeddingService().is_available() is False


# End of test_sparse_embedding.py

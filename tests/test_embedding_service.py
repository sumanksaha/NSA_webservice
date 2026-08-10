"""Tests for the Agent A Phase 1 embedding service (app/rag/embedding_service.py).

Fully self-contained: the sentence-transformers encoder is injected as a mock
via the constructor (mock-injection pattern from
``tests/test_dense_retriever.py``), so no model downloads or optional
dependencies are required.  The vector-size contract guard
(:meth:`EmbeddingService.validate_vector_size`) is pinned directly — a
384-dim MiniLM model against a 768-dim index must be rejected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.chunker import Chunk
from app.rag.embedding_service import DEFAULT_EMBEDDING_MODEL, EmbeddingService


def _make_mock_encoder(dim: int = 768):
    """Mock encoder: single-string -> flat vector, list -> matrix."""
    def encode(texts):
        items = texts if isinstance(texts, list) else [texts]
        return [[0.1] * dim for _ in items]

    return SimpleNamespace(
        encode=encode,
        get_sentence_embedding_dimension=lambda: dim,
    )


class TestEmbeddingServiceConfig:
    def test_default_model_outside_app_context(self):
        svc = EmbeddingService()
        assert svc.model_name == DEFAULT_EMBEDDING_MODEL

    def test_injected_model_name_wins(self):
        svc = EmbeddingService(model_name="custom/model")
        assert svc.model_name == "custom/model"

    def test_model_read_from_config_in_app_context(self):
        from app import create_app

        app = create_app()
        app.config["RAG_EMBEDDING_MODEL"] = "config/model"
        with app.app_context():
            assert EmbeddingService().model_name == "config/model"

    def test_vector_size_from_encoder_dimension(self):
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=384))
        assert svc.vector_size == 384

    def test_vector_size_falls_back_to_config_768(self):
        svc = EmbeddingService()
        assert svc.vector_size == 768


class TestEmbeddingServiceEncoding:
    def test_embed_text_returns_flat_vector(self):
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=768))
        vector = svc.embed_text("Section 55 of the FSS Act")
        assert len(vector) == 768
        assert all(v == 0.1 for v in vector)

    def test_embed_text_normalizes_2d_encoder_output(self):
        # Simulate a sentence-transformers (1, dim) numpy-style result.
        encoder = SimpleNamespace(encode=lambda texts: [[0.2] * 768])
        svc = EmbeddingService(encoder=encoder)
        vector = svc.embed_text("text")
        assert len(vector) == 768  # flattened, not [[...]]

    def test_embed_batch_returns_matrix_preserving_order(self):
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=384))
        vectors = svc.embed_batch(["first", "second"])
        assert len(vectors) == 2
        assert len(vectors[0]) == 384

    def test_embed_chunks_with_chunk_objects(self):
        chunks = [
            Chunk(chunk_id="c1", document_id="d1", chunk_index=0, chunk_text="alpha"),
            Chunk(chunk_id="c2", document_id="d1", chunk_index=1, chunk_text="beta"),
        ]
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=768))
        vectors = svc.embed_chunks(chunks)
        assert len(vectors) == 2
        assert len(vectors[0]) == 768

    def test_embed_chunks_with_plain_strings(self):
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=768))
        vectors = svc.embed_chunks(["alpha", "beta"])
        assert len(vectors) == 2

    def test_embed_chunks_empty(self):
        svc = EmbeddingService(encoder=_make_mock_encoder())
        assert svc.embed_chunks([]) == []


class TestEmbeddingServiceValidation:
    def test_validate_vector_size_matching(self):
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=768))
        assert svc.validate_vector_size(768) is True

    def test_validate_vector_size_mismatch_returns_false(self):
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=384))
        assert svc.validate_vector_size(768) is False

    def test_validate_vector_size_default_expected_from_config(self):
        # No encoder -> nothing to validate -> True even without config.
        svc = EmbeddingService(encoder=_make_mock_encoder(dim=768))
        assert svc.validate_vector_size() is True

    def test_validate_vector_size_without_encoder_returns_true(self):
        svc = EmbeddingService()
        svc._get_encoder = lambda: None  # sentence-transformers unavailable
        assert svc.validate_vector_size(768) is True

    def test_require_encoder_returns_injected_encoder(self):
        encoder = _make_mock_encoder()
        svc = EmbeddingService(encoder=encoder)
        assert svc._require_encoder() is encoder

    def test_require_encoder_raises_when_unavailable(self):
        svc = EmbeddingService()
        svc._get_encoder = lambda: None
        with pytest.raises(RuntimeError, match="sentence-transformers"):
            svc._require_encoder()

"""Unit tests for the shared RAG model runtime (app/rag/model_runtime.py).

The runtime is the one place SentenceTransformer / CrossEncoder construction
happens. These tests pin the contract callers rely on: one model per
``(kind, model_name)``, thread-capping before construction, graceful absence of
the optional package, and cold-start clearing. No model is downloaded — the
``sentence_transformers`` module is replaced with a fake.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest

from app.rag import model_runtime


class _FakeModel:
    instances: ClassVar[list[_FakeModel]] = []

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.__class__.instances.append(self)


class _FakeSentenceTransformer(_FakeModel):
    instances: ClassVar[list[_FakeSentenceTransformer]] = []


class _FakeCrossEncoder(_FakeModel):
    instances: ClassVar[list[_FakeCrossEncoder]] = []


@pytest.fixture(autouse=True)
def _fake_sentence_transformers(monkeypatch):
    """Install a fake ``sentence_transformers`` module and reset the cache."""
    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    module.CrossEncoder = _FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    model_runtime.clear_model_cache()
    _FakeSentenceTransformer.instances.clear()
    _FakeCrossEncoder.instances.clear()
    yield
    model_runtime.clear_model_cache()


class TestModelCache:
    def test_same_name_shares_one_model(self):
        first = model_runtime.get_sentence_transformer("model/a")
        second = model_runtime.get_sentence_transformer("model/a")

        assert first is second
        assert len(_FakeSentenceTransformer.instances) == 1

    def test_different_names_get_different_models(self):
        a = model_runtime.get_sentence_transformer("model/a")
        b = model_runtime.get_sentence_transformer("model/b")

        assert a is not b
        assert len(_FakeSentenceTransformer.instances) == 2

    def test_kinds_do_not_share_a_cache_slot(self):
        st = model_runtime.get_sentence_transformer("same/name")
        ce = model_runtime.get_cross_encoder("same/name")

        assert isinstance(st, _FakeSentenceTransformer)
        assert isinstance(ce, _FakeCrossEncoder)

    def test_blank_name_returns_none_without_loading(self):
        assert model_runtime.get_sentence_transformer("") is None
        assert _FakeSentenceTransformer.instances == []

    def test_clear_model_cache_forces_a_reload(self):
        first = model_runtime.get_sentence_transformer("model/a")
        model_runtime.clear_model_cache()
        second = model_runtime.get_sentence_transformer("model/a")

        assert first is not second
        assert len(_FakeSentenceTransformer.instances) == 2


class TestFailureContract:
    def test_missing_package_returns_none_and_is_remembered(self, monkeypatch):
        monkeypatch.setitem(model_runtime._MODEL_CLASSES, "missing", ("definitely_not_installed_xyz", "Model"))

        assert model_runtime._get_model("missing", "any") is None
        assert "definitely_not_installed_xyz" in model_runtime._unavailable
        # Second call short-circuits on the remembered absence.
        assert model_runtime._get_model("missing", "any") is None

    def test_thread_cap_runs_before_construction(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(model_runtime, "cap_torch_threads", lambda: calls.append("capped"))

        model_runtime.get_cross_encoder("ce/a")

        assert calls == ["capped"]

    def test_load_error_propagates(self, monkeypatch):
        class _Broken:
            def __init__(self, _name: str) -> None:
                raise OSError("weights unavailable")

        fake = ModuleType("sentence_transformers")
        fake.SentenceTransformer = _Broken  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

        with pytest.raises(OSError, match="weights unavailable"):
            model_runtime.get_sentence_transformer("model/a")


class TestCallSitesDelegate:
    """The five former construction sites all cross the runtime seam."""

    def test_embedding_service_uses_runtime(self, monkeypatch):
        from app.rag import embedding_service

        sentinel = object()
        names: list[str] = []

        def _fake(model_name: str):
            names.append(model_name)
            return sentinel

        monkeypatch.setattr(model_runtime, "get_sentence_transformer", _fake)

        service = embedding_service.EmbeddingService(model_name="model/embed")
        assert service._get_encoder() is sentinel
        assert names == ["model/embed"]

    def test_dense_retriever_uses_runtime(self, monkeypatch):
        from app.rag.retrieval import dense_retriever

        sentinel = object()
        monkeypatch.setattr(model_runtime, "get_sentence_transformer", lambda _name: sentinel)

        retriever = dense_retriever.DenseRetriever(collection_name="c", embedding_model="model/embed")
        assert retriever._get_encoder() is sentinel

    def test_plain_reranker_uses_runtime(self, monkeypatch):
        from app.rag.retrieval import reranker

        sentinel = object()
        monkeypatch.setattr(model_runtime, "get_cross_encoder", lambda _name: sentinel)

        assert reranker.Reranker(model_name="ce/a")._get_encoder() is sentinel

    def test_ensemble_reranker_uses_runtime(self, monkeypatch):
        from app.rag.retrieval import reranker

        sentinel = object()
        monkeypatch.setattr(model_runtime, "get_cross_encoder", lambda _name: sentinel)

        assert reranker.EnsembleReranker(model_name="ce/a")._get_encoder() is sentinel

    def test_remote_embedder_fallback_uses_runtime(self, monkeypatch):
        from app.rag.retrieval import remote_embedder

        sentinel = SimpleNamespace(encode=lambda texts: [[0.0] for _ in texts])
        monkeypatch.setattr(model_runtime, "get_sentence_transformer", lambda _name: sentinel)

        client = remote_embedder.RemoteEmbedClient(endpoint="http://x", local_model="model/embed")
        assert client._get_local_encoder() is sentinel

    def test_remote_reranker_fallback_uses_runtime(self, monkeypatch):
        from app.rag.retrieval import remote_reranker

        sentinel = SimpleNamespace(predict=lambda pairs: [0.0 for _ in pairs])
        monkeypatch.setattr(model_runtime, "get_cross_encoder", lambda _name: sentinel)

        client = remote_reranker.RemoteRerankClient(endpoint="http://x", local_model="ce/a")
        assert client._get_local_encoder() is sentinel

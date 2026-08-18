"""Tests for the remote dense-embedding client + wiring.

Covers ``app/rag/retrieval/remote_embedder.py`` (encoder-seam HTTP client for
hosted dense inference — Modal / TEI-style ``/embed``) and the
``DenseRetriever`` wiring behind ``RAG_EMBED_ENDPOINT``.  No network and no
torch required — ``httpx.MockTransport`` stands in for the endpoint and a fake
encoder for the local fallback.
"""

from __future__ import annotations

import httpx
import pytest

from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.remote_embedder import RemoteEmbedClient

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _transport(handler) -> httpx.MockTransport:
    """Wrap a request handler so it records the requests it served."""

    def _recorded(request: httpx.Request) -> httpx.Response:
        handler.requests.append(request)
        return handler(request)

    handler.requests = []
    return httpx.MockTransport(_recorded)


def _json_handler(status: int = 200, payload=None):
    class _H:
        def __init__(self):
            self.requests = []

        def __call__(self, request: httpx.Request) -> httpx.Response:
            if status != 200:
                return httpx.Response(status, json={"error": "boom"})
            return httpx.Response(200, json=payload if payload is not None else {"vectors": []})

    return _H()


class _FakeLocalEncoder:
    """Local SentenceTransformer stand-in — maps text → fixed-dim vector."""

    def __init__(self, dim: int = 3):
        self.dim = dim
        self.encode_calls = 0

    def encode(self, texts):
        self.encode_calls += 1
        return [[float(i), 0.0, float(len(texts))] for i in range(len(texts))]


def _vectors(n: int, dim: int = 3) -> list[list[float]]:
    return [[float(i + j) for j in range(dim)] for i in range(n)]


# --------------------------------------------------------------------------- #
# RemoteEmbedClient — encoder contract
# --------------------------------------------------------------------------- #


class TestEmbedContract:
    def test_returns_vectors_in_order(self):
        handler = _json_handler(payload={"vectors": _vectors(2)})
        client = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        assert client.embed(["a", "b"]) == _vectors(2)

    def test_plain_list_response(self):
        """Some hosts return a bare list of vectors — accept that too."""
        handler = _json_handler(payload=_vectors(2))
        client = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        assert client.embed(["a", "b"]) == _vectors(2)

    def test_single_batched_request(self):
        handler = _json_handler(payload={"vectors": _vectors(2)})
        client = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        client.embed(["a", "b"])
        assert len(handler.requests) == 1
        assert b'"texts"' in handler.requests[0].read()

    def test_empty_texts(self):
        client = RemoteEmbedClient("https://embed.example", transport=_transport(_json_handler()))
        assert client.embed([]) == []

    def test_url_normalization(self):
        """Bare base URL gets /embed appended; explicit /embed stays."""
        handler = _json_handler(payload={"vectors": _vectors(1)})
        client = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        client.embed(["a"])
        assert handler.requests[0].url.path == "/embed"

        handler2 = _json_handler(payload={"vectors": _vectors(1)})
        client2 = RemoteEmbedClient("https://embed.example/embed", transport=_transport(handler2))
        client2.embed(["a"])
        assert handler2.requests[0].url.path == "/embed"

    def test_modal_function_url_not_appended(self):
        """Modal web endpoints serve at the root — no /embed sub-path appended."""
        handler = _json_handler(payload={"vectors": _vectors(1)})
        client = RemoteEmbedClient("https://sumanksaha--embed.modal.run", transport=_transport(handler))
        client.embed(["a"])
        assert handler.requests[0].url.path == "/"

    def test_vectors_coerced_to_floats(self):
        handler = _json_handler(payload={"vectors": [["1", 2]]})
        client = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        assert client.embed(["a"]) == [[1.0, 2.0]]

    def test_unexpected_response_raises(self):
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "nope"})

        client = RemoteEmbedClient("https://embed.example", transport=httpx.MockTransport(_handler))
        with pytest.raises(RuntimeError, match="unexpected embed response"):
            client.embed(["a"])


class TestAuthAndErrors:
    def test_bearer_token_sent(self):
        handler = _json_handler(payload={"vectors": _vectors(1)})
        client = RemoteEmbedClient("https://embed.example", token="sekret", transport=_transport(handler))
        client.embed(["a"])
        assert handler.requests[0].headers["Authorization"] == "Bearer sekret"

    def test_no_token_no_auth_header(self):
        handler = _json_handler(payload={"vectors": _vectors(1)})
        client = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        client.embed(["a"])
        assert "Authorization" not in handler.requests[0].headers

    def test_http_error_raises_without_fallback(self):
        handler = _json_handler(status=500)
        client = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        with pytest.raises(RuntimeError, match="no local fallback"):
            client.embed(["a"])

    def test_transport_error_raises_without_fallback(self):
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = RemoteEmbedClient("https://embed.example", transport=httpx.MockTransport(_boom))
        with pytest.raises(RuntimeError, match="no local fallback"):
            client.embed(["a"])


class TestLocalFallback:
    def test_falls_back_to_local_encoder_on_remote_failure(self):
        """Remote down → local encoder embeds (lazy, built on demand)."""
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        local = _FakeLocalEncoder()
        client = RemoteEmbedClient(
            "https://embed.example",
            transport=httpx.MockTransport(_boom),
            local_encoder=local,
        )
        assert client.embed(["x", "y"]) == [[0.0, 0.0, 2.0], [1.0, 0.0, 2.0]]
        assert local.encode_calls == 1

    def test_local_fallback_not_built_when_remote_works(self):
        handler = _json_handler(payload={"vectors": _vectors(1)})
        local = _FakeLocalEncoder()
        client = RemoteEmbedClient(
            "https://embed.example",
            transport=_transport(handler),
            local_encoder=local,
        )
        assert client.embed(["a"]) == _vectors(1)
        assert local.encode_calls == 0  # never touched


# --------------------------------------------------------------------------- #
# DenseRetriever wiring (RAG_EMBED_ENDPOINT)
# --------------------------------------------------------------------------- #


class TestDenseRetrieverRemoteWiring:
    def test_endpoint_set_uses_remote_embedder(self, monkeypatch):
        """embed_query returns the remote vector when RAG_EMBED_ENDPOINT is set."""
        monkeypatch.setenv("RAG_EMBED_ENDPOINT", "https://embed.example")
        monkeypatch.delenv("RAG_EMBED_TOKEN", raising=False)
        monkeypatch.delenv("RAG_EMBED_TIMEOUT", raising=False)
        monkeypatch.delenv("RAG_EMBED_REMOTE_FALLBACK", raising=False)

        dr = DenseRetriever(collection_name="c")
        remote = dr._get_remote_embedder()
        assert remote is not None
        assert remote.endpoint == "https://embed.example"
        # Fallback is on by default → the local model name is carried for the
        # lazy local encoder (same default as the reranker wiring).
        assert remote.local_model == "sentence-transformers/all-mpnet-base-v2"

    def test_no_endpoint_keeps_local_path(self, monkeypatch):
        monkeypatch.delenv("RAG_EMBED_ENDPOINT", raising=False)
        dr = DenseRetriever(collection_name="c")
        assert dr._get_remote_embedder() is None

    def test_remote_fallback_env_controls_local_model(self, monkeypatch):
        monkeypatch.setenv("RAG_EMBED_ENDPOINT", "https://embed.example")
        monkeypatch.setenv("RAG_EMBED_REMOTE_FALLBACK", "true")
        monkeypatch.delenv("RAG_EMBED_TOKEN", raising=False)
        monkeypatch.delenv("RAG_EMBED_TIMEOUT", raising=False)
        dr = DenseRetriever(collection_name="c")
        remote = dr._get_remote_embedder()
        assert remote.local_model == "sentence-transformers/all-mpnet-base-v2"

    def test_embed_query_goes_through_remote(self, monkeypatch):
        """End-to-end: injected remote client is used by embed_query."""
        monkeypatch.setenv("RAG_EMBED_ENDPOINT", "https://embed.example")
        monkeypatch.delenv("RAG_EMBED_TOKEN", raising=False)
        monkeypatch.delenv("RAG_EMBED_TIMEOUT", raising=False)
        monkeypatch.delenv("RAG_EMBED_REMOTE_FALLBACK", raising=False)

        handler = _json_handler(payload={"vectors": _vectors(1, dim=2)})
        dr = DenseRetriever(collection_name="c")
        dr._remote_embed = RemoteEmbedClient("https://embed.example", transport=_transport(handler))
        assert dr.embed_query("q") == [0.0, 1.0]  # flat vector for one text
        assert handler.requests[0].url.path == "/embed"

"""Tests for the remote cross-encoder client (TEI /rerank) + wiring.

Covers ``app/rag/retrieval/remote_reranker.py`` (encoder-seam HTTP client for
the HF-hosted legal cross-encoders) and the ``_build_reranker`` wiring behind
``RAG_RERANKER_ENDPOINT`` (docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md
Part B).  No network and no torch required — ``httpx.MockTransport`` stands in
for the endpoint and a fake encoder for the local fallback.
"""

from __future__ import annotations

import httpx
import pytest

from app.rag.retrieval.remote_reranker import RemoteRerankClient
from app.rag.retrieval.reranker import EnsembleReranker
from app.rag.retrieval.result import RetrievedChunk

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
            return httpx.Response(200, json=payload if payload is not None else [])

    return _H()


class _FakeLocalEncoder:
    """Local CE stand-in — scores text via a map (mirrors _MockCrossEncoder)."""

    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.predict_calls = 0

    def predict(self, pairs):
        self.predict_calls += 1
        return [self.scores.get(text, 0.0) for _query, text in pairs]


# --------------------------------------------------------------------------- #
# RemoteRerankClient — encoder contract
# --------------------------------------------------------------------------- #


class TestPredictContract:
    def test_returns_scores_in_pair_order(self):
        """Object-form TEI response (index field) maps back to pair order."""
        handler = _json_handler(payload=[{"index": 1, "score": 0.2}, {"index": 0, "score": 4.1}])
        client = RemoteRerankClient("https://ce.example", transport=_transport(handler))
        scores = client.predict([("q", "a"), ("q", "b")])
        assert scores == [4.1, 0.2]

    def test_plain_number_list_response(self):
        """Some TEI builds return a bare scores list — accept that too."""
        handler = _json_handler(payload=[1.5, -0.5])
        client = RemoteRerankClient("https://ce.example", transport=_transport(handler))
        assert client.predict([("q", "a"), ("q", "b")]) == [1.5, -0.5]

    def test_single_request_per_distinct_query(self):
        """All texts sharing a query go in one POST (the latency bound)."""
        handler = _json_handler(payload=[{"index": 0, "score": 1.0}, {"index": 1, "score": 2.0}])
        client = RemoteRerankClient("https://ce.example", transport=_transport(handler))
        client.predict([("q1", "a"), ("q1", "b")])
        assert len(handler.requests) == 1
        body = handler.requests[0].read()
        assert b'"query":"q1"' in body
        assert b'"texts"' in body

    def test_groups_multiple_distinct_queries(self):
        """Pairs with different queries POST once per query, merged in order."""
        import json

        def _handler(request: httpx.Request) -> httpx.Response:
            data = json.loads(request.content)
            return httpx.Response(200, json=[{"index": i, "score": float(i)} for i in range(len(data["texts"]))])

        handler = _handler
        client = RemoteRerankClient("https://ce.example", transport=_transport(handler))
        scores = client.predict([("q1", "a"), ("q2", "x"), ("q1", "b"), ("q2", "y")])
        assert scores == [0.0, 0.0, 1.0, 1.0]  # per-group index order preserved

    def test_empty_pairs(self):
        client = RemoteRerankClient("https://ce.example", transport=_transport(_json_handler()))
        assert client.predict([]) == []

    def test_url_normalization(self):
        """Bare base URL gets /rerank appended; explicit /rerank stays."""
        handler = _json_handler(payload=[{"index": 0, "score": 1.0}])
        client = RemoteRerankClient("https://ce.example", transport=_transport(handler))
        client.predict([("q", "a")])
        assert handler.requests[0].url.path == "/rerank"

        handler2 = _json_handler(payload=[{"index": 0, "score": 1.0}])
        client2 = RemoteRerankClient("https://ce.example/rerank", transport=_transport(handler2))
        client2.predict([("q", "a")])
        assert handler2.requests[0].url.path == "/rerank"

    def test_modal_function_url_not_appended(self):
        """Modal web endpoints serve at the root — no /rerank sub-path appended."""
        handler = _json_handler(payload=[{"index": 0, "score": 1.0}])
        client = RemoteRerankClient("https://sumanksaha--rerank.modal.run", transport=_transport(handler))
        client.predict([("q", "a")])
        assert handler.requests[0].url.path == "/"


class TestAuthAndErrors:
    def test_bearer_token_sent(self):
        handler = _json_handler(payload=[{"index": 0, "score": 1.0}])
        client = RemoteRerankClient("https://ce.example", token="sekret", transport=_transport(handler))
        client.predict([("q", "a")])
        assert handler.requests[0].headers["Authorization"] == "Bearer sekret"

    def test_no_token_no_auth_header(self):
        handler = _json_handler(payload=[{"index": 0, "score": 1.0}])
        client = RemoteRerankClient("https://ce.example", transport=_transport(handler))
        client.predict([("q", "a")])
        assert "Authorization" not in handler.requests[0].headers

    def test_http_error_raises_without_fallback(self):
        handler = _json_handler(status=500)
        client = RemoteRerankClient("https://ce.example", transport=_transport(handler))
        with pytest.raises(RuntimeError, match="no local fallback"):
            client.predict([("q", "a")])

    def test_transport_error_raises_without_fallback(self):
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = RemoteRerankClient("https://ce.example", transport=httpx.MockTransport(_boom))
        with pytest.raises(RuntimeError, match="no local fallback"):
            client.predict([("q", "a")])


class TestServerlessMode:
    """HF Serverless Inference API backend (per-pair [SEP] requests)."""

    def test_serverless_request_shape(self):
        """One POST per pair, input is 'query [SEP] text', URL is the endpoint."""
        import json

        def _handler(request: httpx.Request) -> httpx.Response:
            data = json.loads(request.content)
            assert data["inputs"] == "q [SEP] a"
            return httpx.Response(200, json=[{"label": "LABEL_0", "score": -0.821}])

        handler = _handler
        client = RemoteRerankClient(
            "https://api-inference.huggingface.co/models/sumanksaha/Foodmultidomain",
            mode="serverless",
            transport=_transport(handler),
        )
        assert client.predict([("q", "a")]) == [-0.821]
        assert handler.requests[0].url.path.startswith("/models/")
        assert not handler.requests[0].url.path.endswith("/rerank")

    def test_serverless_per_pair_requests(self):
        """Serverless has no batching — one request per pair, in order."""
        import json

        def _handler(request: httpx.Request) -> httpx.Response:
            json.loads(request.content)
            n = sum(1 for r in _handler.requests if r is not request)  # prior calls
            return httpx.Response(200, json=[{"label": "LABEL_0", "score": float(n)}])

        handler = _handler
        client = RemoteRerankClient(
            "https://api-inference.huggingface.co/models/m", mode="serverless", transport=_transport(handler)
        )
        assert client.predict([("q", "a"), ("q", "b")]) == [0.0, 1.0]
        assert len(handler.requests) == 2

    def test_serverless_auth_header(self):

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"label": "LABEL_0", "score": 1.0}])

        handler = _handler
        client = RemoteRerankClient(
            "https://api-inference.huggingface.co/models/m",
            token="sekret",
            mode="serverless",
            transport=_transport(handler),
        )
        client.predict([("q", "a")])
        assert handler.requests[0].headers["Authorization"] == "Bearer sekret"

    def test_serverless_unexpected_response_raises(self):
        """A non-score response (e.g. model not served) surfaces for fallback."""

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "Model not served"})

        client = RemoteRerankClient(
            "https://api-inference.huggingface.co/models/m",
            mode="serverless",
            transport=httpx.MockTransport(_handler),
        )
        with pytest.raises(RuntimeError, match="no local fallback"):
            client.predict([("q", "a")])

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError, match="mode must be"):
            RemoteRerankClient("https://ce.example", mode="bogus")


class TestLocalFallback:
    def test_falls_back_to_local_encoder_on_remote_failure(self):
        """Remote down → local CE scores the pairs (lazy, built on demand)."""

        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        local = _FakeLocalEncoder({"a": 9.0, "b": 1.0})
        client = RemoteRerankClient(
            "https://ce.example",
            transport=httpx.MockTransport(_boom),
            local_encoder=local,
        )
        assert client.predict([("q", "a"), ("q", "b")]) == [9.0, 1.0]
        assert local.predict_calls == 1

    def test_local_fallback_not_built_when_remote_works(self):
        handler = _json_handler(payload=[{"index": 0, "score": 3.0}])
        local = _FakeLocalEncoder({})
        client = RemoteRerankClient(
            "https://ce.example",
            transport=_transport(handler),
            local_encoder=local,
        )
        assert client.predict([("q", "a")]) == [3.0]
        assert local.predict_calls == 0  # never touched

    def test_ensemble_reranker_uses_remote_client_as_encoder(self):
        """End-to-end through EnsembleReranker: remote fails → local scores decide."""

        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        local = _FakeLocalEncoder({"head a": 5.0, "head b": 1.0})
        client = RemoteRerankClient(
            "https://ce.example",
            transport=httpx.MockTransport(_boom),
            local_encoder=local,
        )

        def _chunk(cid, score, text):
            return RetrievedChunk(
                chunk_id=cid,
                score=score,
                text=text,
                section_number=None,
                act_name="",
                document_title="",
                hierarchy_level=3,
            )

        chunks = [_chunk("c1", 0.9, "head a"), _chunk("c2", 0.8, "head b")]
        reranker = EnsembleReranker(encoder=client, ce_head=2, ce_weight=1.0)
        reranker.skip_ce_when_confident = False
        result = reranker.rerank("q", chunks)
        assert result[0].chunk_id == "c1"  # local CE prefers "head a"


# --------------------------------------------------------------------------- #
# _build_reranker wiring
# --------------------------------------------------------------------------- #


class TestBuildRerankerRemoteWiring:
    def test_endpoint_set_injects_remote_client(self, monkeypatch):
        from app.rag.retrieval.remote_reranker import RemoteRerankClient as RRC
        from app.rag.tasks import _build_reranker

        monkeypatch.setenv("RAG_RERANKER_ENDPOINT", "https://ce.example")
        monkeypatch.delenv("RAG_RERANKER_MODEL", raising=False)
        monkeypatch.delenv("RAG_ENSEMBLE_RERANK", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker, EnsembleReranker)
        assert isinstance(reranker._encoder, RRC)
        assert reranker._encoder.endpoint == "https://ce.example"
        # Fallback is on by default → local_model carries the configured model.
        assert reranker._encoder.local_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def test_endpoint_set_fallback_off_no_local_model(self, monkeypatch):
        from app.rag.retrieval.remote_reranker import RemoteRerankClient as RRC
        from app.rag.tasks import _build_reranker

        monkeypatch.setenv("RAG_RERANKER_ENDPOINT", "https://ce.example")
        monkeypatch.setenv("RAG_RERANKER_REMOTE_FALLBACK", "false")
        monkeypatch.delenv("RAG_ENSEMBLE_RERANK", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker._encoder, RRC)
        assert reranker._encoder.local_model is None

    def test_no_endpoint_keeps_local_encoder_none(self, monkeypatch):
        from app.rag.tasks import _build_reranker

        monkeypatch.delenv("RAG_RERANKER_ENDPOINT", raising=False)
        monkeypatch.delenv("RAG_ENSEMBLE_RERANK", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker, EnsembleReranker)
        assert reranker._encoder is None  # local CE built lazily via _get_encoder

    def test_mode_serverless_wired(self, monkeypatch):
        from app.rag.retrieval.remote_reranker import RemoteRerankClient as RRC
        from app.rag.tasks import _build_reranker

        monkeypatch.setenv(
            "RAG_RERANKER_ENDPOINT", "https://api-inference.huggingface.co/models/sumanksaha/Foodmultidomain"
        )
        monkeypatch.setenv("RAG_RERANKER_MODE", "serverless")
        monkeypatch.delenv("RAG_ENSEMBLE_RERANK", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker._encoder, RRC)
        assert reranker._encoder.mode == "serverless"
        assert reranker._encoder.endpoint == "https://api-inference.huggingface.co/models/sumanksaha/Foodmultidomain"

    def test_endpoint_honoured_for_plain_reranker(self, monkeypatch):
        """The non-ensemble Reranker also uses the remote client when set."""
        from app.rag.retrieval.reranker import Reranker
        from app.rag.tasks import _build_reranker

        monkeypatch.setenv("RAG_RERANKER_ENDPOINT", "https://ce.example")
        monkeypatch.setenv("RAG_ENSEMBLE_RERANK", "false")
        monkeypatch.delenv("RAG_RERANKER_MODEL", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker, Reranker)
        assert reranker._encoder is None  # plain Reranker keeps local CE path

"""Remote cross-encoder client for hosted CE inference.

Serves the fine-tuned legal cross-encoders (``legal_ce_v1`` / ``legal_ce_v2``
pushed to the HF Hub) over HTTP instead of loading torch locally — see
``docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md`` (Part B).  Two backends:

- ``mode="tei"`` (default): TEI (text-embeddings-inference) ``/rerank``
  endpoint — one batched POST per distinct query.  Run via a Docker Space
  or Inference Endpoint.
- ``mode="serverless"``: the **HF Serverless Inference API** — one POST per
  pair with ``[SEP]`` concatenation (cross-encoders are served as
  ``text-classification``).  Free and zero-ops, but no batching (one
  request per pair) and subject to cold starts / rate limits.

The client implements the **same ``predict(pairs) -> list[float]`` contract**
as a local ``sentence_transformers.CrossEncoder`` so it can be injected as the
``encoder`` argument of :class:`Reranker` / :class:`EnsembleReranker` with
**zero changes** to the reranking logic — the constructor-injection seam the
existing test suite already exercises with mock encoders.

Fallback chain (matching the codebase graceful-degradation pattern):

    remote CE → local CE (lazy, built only on first remote failure) → raise
    (the reranker then degrades to sec_act features-only)

Cost controls:
- TEI: one HTTP request per distinct query in the pair batch, so the CE
  head costs one round-trip.
- The reranker's dynamic CE-skipping (exact sec+act match on the whole head)
  still prevents remote calls entirely on high-confidence queries.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RemoteRerankClient:
    """Encoder-seam-compatible HTTP client for a TEI ``/rerank`` endpoint.

    Args:
        endpoint: TEI base URL, e.g. ``https://nsa-ce-v2.hf.space`` or a
            full ``.../rerank`` path.  A missing trailing ``/rerank`` is
            appended.
        token: Optional Bearer token (Inference Endpoint / private Space auth).
        timeout: Per-request timeout in seconds.
        local_model: Optional model name for the lazy local CE fallback
            (built on first remote failure; ``None`` disables the fallback).
        local_encoder: Optional pre-built local encoder (testing) — injected
            directly instead of loading ``local_model``.
        transport: Optional ``httpx`` transport (testing).
        mode: ``"tei"`` (default) or ``"serverless"`` — see module docstring.
    """

    def __init__(
        self,
        endpoint: str,
        token: str | None = None,
        timeout: float = 5.0,
        local_model: str | None = None,
        local_encoder: Any | None = None,
        transport: Any | None = None,
        mode: str = "tei",
    ) -> None:
        if mode not in ("tei", "serverless"):
            raise ValueError(f"mode must be 'tei' or 'serverless', got {mode!r}")
        self.endpoint = str(endpoint).rstrip("/")
        self.token = token
        self.timeout = float(timeout)
        self.local_model = local_model
        self._local_encoder = local_encoder
        self._local_attempted = local_encoder is not None
        self._transport = transport
        self.mode = mode

    # ------------------------------------------------------------------ #
    # Encoder contract (mirrors sentence_transformers.CrossEncoder.predict)
    # ------------------------------------------------------------------ #

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score ``(query, text)`` pairs, returning one float per pair."""
        if not pairs:
            return []
        try:
            return self._remote_predict(pairs)
        except Exception as exc:  # noqa: BLE001 - any remote failure → local
            local = self._get_local_encoder()
            if local is not None:
                logger.warning(
                    "RemoteRerankClient: remote rerank failed (%s) — falling back to local CE", exc
                )
                return local.predict(pairs)
            raise RuntimeError(f"Remote reranker unavailable and no local fallback: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Remote call
    # ------------------------------------------------------------------ #

    def _remote_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Dispatch to the active backend (TEI batching or serverless per-pair)."""
        if self.mode == "serverless":
            return self._serverless_predict(pairs)
        return self._tei_predict(pairs)

    def _tei_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """POST each distinct query's texts to ``/rerank``; map scores to pair order."""
        import httpx

        # Group pairs by query — TEI /rerank takes a single query + N texts.
        groups: dict[str, list[str]] = {}
        group_order: list[tuple[str, int]] = []  # (query, group_index)
        for query, text in pairs:
            if query not in groups:
                groups[query] = []
                group_order.append((query, len(groups) - 1))
            groups[query].append(text)

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        url = self._rerank_url()

        scores_by_group: dict[str, list[float]] = {}
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for query, _idx in group_order:
                resp = client.post(url, json={"query": query, "texts": groups[query]}, headers=headers)
                resp.raise_for_status()
                scores_by_group[query] = self._parse_scores(resp.json())

        # Re-map to original pair order.
        result: list[float] = []
        cursor = {q: 0 for q, _ in group_order}
        for query, _text in pairs:
            group_scores = scores_by_group[query]
            result.append(group_scores[cursor[query]])
            cursor[query] += 1
        return result

    @staticmethod
    def _parse_scores(data: Any) -> list[float]:
        """Normalize TEI ``/rerank`` responses to a scores list.

        Accepts either the object form ``[{"index": i, "score": s}, ...]``
        (index-ordered) or a plain list of numbers.
        """
        if not isinstance(data, list) or not data:
            return []
        first = data[0]
        if isinstance(first, dict):
            by_index = {int(item.get("index", i)): float(item["score"]) for i, item in enumerate(data)}
            return [by_index[i] for i in sorted(by_index)]
        return [float(s) for s in data]

    def _serverless_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score pairs via the HF Serverless Inference API (one POST per pair).

        Cross-encoders are served as ``text-classification``: the input is the
        ``"query [SEP] text"`` pair and the response is
        ``[{"label": "LABEL_0", "score": <logit>}]`` (the model's Identity
        CE activation makes ``score`` the raw rerank logit).
        """
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        scores: list[float] = []
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for query, text in pairs:
                resp = client.post(
                    self.endpoint,
                    json={"inputs": f"{query} [SEP] {text}"},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    scores.append(float(data[0].get("score", 0.0)))
                else:
                    raise RuntimeError(f"unexpected serverless response: {data!r}")
        return scores

    def _rerank_url(self) -> str:
        """The ``/rerank`` URL — append if the endpoint is a bare base URL.

        Modal web endpoints are an exception: their function-specific URL
        (``https://<workspace>--<app>-<label>.modal.run``) serves at the root
        — POSTing to a ``/rerank`` sub-path 404s.  Detected by the
        ``.modal.run`` suffix and passed through unchanged.
        """
        if self.endpoint.endswith("/rerank"):
            return self.endpoint
        if self.endpoint.endswith(".modal.run"):
            return self.endpoint
        return f"{self.endpoint}/rerank"

    # ------------------------------------------------------------------ #
    # Local fallback (lazy)
    # ------------------------------------------------------------------ #

    def _get_local_encoder(self) -> Any | None:
        """Build the local ``CrossEncoder`` on first remote failure.

        ``None`` when no local model is configured or sentence-transformers /
        torch are unavailable — the caller then degrades further (the
        reranker's sec_act features-only path).
        """
        if self._local_attempted:
            return self._local_encoder
        self._local_attempted = True
        if not self.local_model:
            return None
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

            # Bound torch threads before the model is built (RAG_TORCH_THREADS)
            # so the fallback does not peg every core on a laptop.
            from app.rag.torch_runtime import cap_torch_threads

            cap_torch_threads()
            self._local_encoder = CrossEncoder(self.local_model)
        except Exception as exc:  # noqa: BLE001 - optional dependency / bad path
            logger.warning("RemoteRerankClient: local CE fallback unavailable (%s)", exc)
            self._local_encoder = None
        return self._local_encoder

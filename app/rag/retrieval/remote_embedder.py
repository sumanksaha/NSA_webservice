"""Remote dense-embedding client for hosted inference (Modal / TEI-style ``/embed``).

Mirrors :mod:`app.rag.retrieval.remote_reranker` for the dense side: an HTTP
client implementing an encoder-seam contract so :class:`DenseRetriever` can
embed queries without loading ``all-mpnet-base-v2`` + torch locally (Render
free tier cannot hold them — see docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md
Part B for the equivalent CE story).

Contract (mirrors ``sentence_transformers.SentenceTransformer`` where it
matters):

    embed(texts: list[str]) -> list[list[float]]

One batched ``POST {"texts": [...]}`` to ``{endpoint}/embed`` returning
``{"vectors": [[...], ...]}``.  The injected client is used exactly where the
local encoder was, so all retrieval logic is unchanged.

Fallback chain (graceful degradation, same as the reranker):

    remote embed → local SentenceTransformer (lazy, built only on first
    remote failure) → raise (the caller then degrades to sparse-only)

On Render free tier set ``RAG_EMBED_REMOTE_FALLBACK=false`` so a dead endpoint
never triggers a local torch build (OOM) — it degrades straight to sparse-only.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RemoteEmbedClient:
    """Encoder-seam-compatible HTTP client for a hosted ``/embed`` endpoint.

    Args:
        endpoint: Base URL, e.g. ``https://<workspace>--nsa-legal-inference-embed.modal.run``.
            A missing trailing ``/embed`` is appended.
        token: Optional Bearer token.
        timeout: Per-request timeout in seconds.
        local_model: Optional model name for the lazy local SentenceTransformer
            fallback (built on first remote failure; ``None`` disables it).
        local_encoder: Optional pre-built local encoder (testing) — injected
            directly instead of loading ``local_model``.
        transport: Optional ``httpx`` transport (testing).
    """

    def __init__(
        self,
        endpoint: str,
        token: str | None = None,
        timeout: float = 5.0,
        local_model: str | None = None,
        local_encoder: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self.endpoint = str(endpoint).rstrip("/")
        self.token = token
        self.timeout = float(timeout)
        self.local_model = local_model
        self._local_encoder = local_encoder
        self._local_attempted = local_encoder is not None
        self._transport = transport

    # ------------------------------------------------------------------ #
    # Encoder contract
    # ------------------------------------------------------------------ #

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` into dense vectors (one float list per text)."""
        if not texts:
            return []
        try:
            return self._remote_embed(texts)
        except Exception as exc:  # noqa: BLE001 - any remote failure → local
            local = self._get_local_encoder()
            if local is not None:
                logger.warning(
                    "RemoteEmbedClient: remote embed failed (%s) — falling back to local encoder", exc
                )
                return self._local_embed(local, texts)
            raise RuntimeError(f"Remote embedder unavailable and no local fallback: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Remote call
    # ------------------------------------------------------------------ #

    def _remote_embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            resp = client.post(
                self._embed_url(),
                json={"texts": list(texts)},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        vectors = data.get("vectors") if isinstance(data, dict) else data
        if not isinstance(vectors, list) or not vectors:
            raise RuntimeError(f"unexpected embed response: {data!r}")
        return [[float(v) for v in vec] for vec in vectors]

    def _embed_url(self) -> str:
        """The ``/embed`` URL — append if the endpoint is a bare base URL.

        Modal web endpoints are an exception: their function-specific URL
        (``https://<workspace>--<app>-<label>.modal.run``) serves at the root
        — POSTing to an ``/embed`` sub-path 404s.  Detected by the
        ``.modal.run`` suffix and passed through unchanged.
        """
        if self.endpoint.endswith("/embed"):
            return self.endpoint
        if self.endpoint.endswith(".modal.run"):
            return self.endpoint
        return f"{self.endpoint}/embed"

    # ------------------------------------------------------------------ #
    # Local fallback (lazy)
    # ------------------------------------------------------------------ #

    def _get_local_encoder(self) -> Any | None:
        """Build the local ``SentenceTransformer`` on first remote failure.

        ``None`` when no local model is configured or sentence-transformers /
        torch are unavailable — the caller then degrades further (sparse-only).
        """
        if self._local_attempted:
            return self._local_encoder
        self._local_attempted = True
        if not self.local_model:
            return None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            # Bound torch threads before the model is built (RAG_TORCH_THREADS)
            # so the fallback does not peg every core on a laptop.
            from app.rag.torch_runtime import cap_torch_threads

            cap_torch_threads()
            self._local_encoder = SentenceTransformer(self.local_model)
        except Exception as exc:  # noqa: BLE001 - optional dependency / bad path
            logger.warning("RemoteEmbedClient: local encoder fallback unavailable (%s)", exc)
            self._local_encoder = None
        return self._local_encoder

    @staticmethod
    def _local_embed(encoder: Any, texts: list[str]) -> list[list[float]]:
        """Embed with a local encoder, normalizing the return shape."""
        vectors = encoder.encode(list(texts))
        if hasattr(vectors, "tolist"):
            return vectors.tolist()
        return [list(v) for v in vectors]

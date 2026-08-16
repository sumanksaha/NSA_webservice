"""Modal-hosted inference for the NSA legal RAG stack.

One warm container serves the two models the Render free tier cannot hold
(torch + weights exceed 512 MB RAM / 0.1 CPU):

- ``POST /rerank`` — TEI-compatible cross-encoder endpoint::

      {"query": "...", "texts": ["...", "..."]}
      → [{"index": 0, "score": 4.2}, {"index": 1, "score": -1.1}]

  Backed by the fine-tuned legal cross-encoder ``sumanksaha/Foodmultidomain``.
  Response shape matches what ``RemoteRerankClient`` (TEI mode) parses, so the
  app's ensemble reranker works unchanged with ``RAG_RERANKER_MODE=tei``.

- ``POST /embed`` — dense embedding endpoint::

      {"texts": ["...", "..."]}
      → {"vectors": [[0.01, ...], ...]}

  Backed by ``sentence-transformers/all-mpnet-base-v2`` (768-dim), the same
  model the ``fssai_legal_768`` collection was indexed with — dimensions and
  embedding space match, so query-side remote embedding is lossless.

Deploy (from a machine with the Modal CLI authenticated)::

    modal deploy app.py

The printed URLs are the ``RAG_RERANKER_ENDPOINT`` / ``RAG_EMBED_ENDPOINT``
values.  The CE model repo is **gated** — the workspace must have a Secret
named ``hf-token`` containing ``HF_TOKEN`` (see README.md).
"""

from __future__ import annotations

import modal
from pydantic import BaseModel

MODEL_RERANK = "sumanksaha/Foodmultidomain"  # gated — requires HF_TOKEN secret
MODEL_EMBED = "sentence-transformers/all-mpnet-base-v2"

#: Workspace Secret holding HF_TOKEN (read token, gate accepted).
hf_secret = modal.Secret.from_name("hf-token")


def _download_models() -> None:
    """Download both models at image-build time (one-time, baked into the image).

    Runs inside the build with the ``hf-token`` secret available, so the gated
    cross-encoder is fetched during build — containers then start warm without
    re-downloading ~500 MB of weights on every cold start.
    """
    from sentence_transformers import CrossEncoder, SentenceTransformer

    CrossEncoder(MODEL_RERANK)
    SentenceTransformer(MODEL_EMBED)


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "sentence-transformers>=3.3",
        "torch>=2.0",
        "fastapi",
        "pydantic",
    )
    .run_function(_download_models, secrets=[hf_secret])
)

app = modal.App("nsa-legal-inference")


class RerankRequest(BaseModel):
    query: str
    texts: list[str]


class EmbedRequest(BaseModel):
    texts: list[str]


@app.cls(
    image=image,
    secrets=[hf_secret],
    scaledown_window=600,
)
@modal.concurrent(max_inputs=4)
class Inference:
    """Container-lifetime model holder + the two HTTP endpoints."""

    @modal.enter()
    def load(self) -> None:
        """Load both models once per container (cold start ~10-30 s)."""
        from sentence_transformers import CrossEncoder, SentenceTransformer

        self.ce = CrossEncoder(MODEL_RERANK)
        self.emb = SentenceTransformer(MODEL_EMBED)

    @modal.fastapi_endpoint(method="POST", label="rerank")
    def rerank(self, body: RerankRequest) -> list[dict]:
        """TEI-compatible /rerank — scores ``(query, text)`` pairs."""
        scores = self.ce.predict([(body.query, t) for t in body.texts])
        return [{"index": i, "score": float(s)} for i, s in enumerate(scores)]

    @modal.fastapi_endpoint(method="POST", label="embed")
    def embed(self, body: EmbedRequest) -> dict:
        """Dense embeddings — plain ``encode()``, no normalization, matching
        how the collection was indexed."""
        vectors = self.emb.encode(body.texts)
        return {"vectors": [v.tolist() for v in vectors]}

    @modal.fastapi_endpoint(method="GET", label="healthz")
    def healthz(self) -> dict:
        return {"status": "ok", "rerank": MODEL_RERANK, "embed": MODEL_EMBED}

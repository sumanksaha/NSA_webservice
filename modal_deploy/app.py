"""Modal-hosted inference for the NSA legal RAG stack.

One warm container serves the two models the Render free tier cannot hold
(torch + weights exceed 512 MB RAM / 0.1 CPU):

- ``POST /rerank`` — TEI-compatible cross-encoder endpoint::

      {"query": "...", "texts": ["...", "..."]}
      → [{"index": 0, "score": 4.2}, {"index": 1, "score": -1.1}]

  Backed by the fine-tuned legal cross-encoder stored on the Modal Volume
  ``nsa-ce-models`` (mounted at ``/models``) — no Hugging Face Hub dependency.
  Response shape matches what ``RemoteRerankClient`` (TEI mode) parses, so the
  app's ensemble reranker works unchanged with ``RAG_RERANKER_MODE=tei``.

- ``POST /embed`` — dense embedding endpoint::

      {"texts": ["...", "..."]}
      → {"vectors": [[0.01, ...], ...]}

  Backed by ``sentence-transformers/all-mpnet-base-v2`` (768-dim), the same
  model the ``fssai_legal_768`` collection was indexed with — dimensions and
  embedding space match, so query-side remote embedding is lossless.

Ship a new cross-encoder (from a machine with the Modal CLI authenticated)::

    # 1. upload only the inference files (config/safetensors/tokenizer) —
    #    never train_state.pt / tokenized_cache.pt
    MSYS_NO_PATHCONV=1 modal volume put nsa-ce-models <local_dir> /<model_name> --force
    # 2. set CE_MODEL_NAME below to <model_name>, then
    modal deploy app.py
    # 3. a warm container from the previous version may keep serving for up to
    #    ``scaledown_window`` (10 min) — stop it so the swap is immediate:
    modal container list && modal container stop <container_id> --yes
    # 4. (optional) drop the superseded checkpoint
    modal volume rm nsa-ce-models /<old_model_name> -r

The printed URLs are the ``RAG_RERANKER_ENDPOINT`` / ``RAG_EMBED_ENDPOINT``
values (they are stable across redeploys).
"""

from __future__ import annotations

import modal
from pydantic import BaseModel

#: Persistent Volume holding the fine-tuned cross-encoder checkpoints.
CE_VOLUME_NAME = "nsa-ce-models"
CE_VOLUME_MOUNT = "/models"
#: Directory name inside the Volume for the checkpoint to serve.
CE_MODEL_NAME = "legal_ce_v2_K500"
CE_MODEL_DIR = f"{CE_VOLUME_MOUNT}/{CE_MODEL_NAME}"

MODEL_EMBED = "sentence-transformers/all-mpnet-base-v2"

ce_volume = modal.Volume.from_name(CE_VOLUME_NAME)


def _download_models() -> None:
    """Download the embedding model at image-build time (baked into the image).

    The cross-encoder is *not* downloaded here — it is read from the mounted
    Volume at container start, so swapping the CE never requires an image
    rebuild.
    """
    from sentence_transformers import SentenceTransformer

    SentenceTransformer(MODEL_EMBED)


image = (
    modal.Image
    .debian_slim(python_version="3.12")
    .pip_install(
        "sentence-transformers>=3.3",
        "torch>=2.0",
        "fastapi",
        "pydantic",
    )
    .run_function(_download_models)
)

app = modal.App("nsa-legal-inference")


class RerankRequest(BaseModel):
    query: str
    texts: list[str]


class EmbedRequest(BaseModel):
    texts: list[str]


@app.cls(
    image=image,
    volumes={CE_VOLUME_MOUNT: ce_volume},
    scaledown_window=600,
)
@modal.concurrent(max_inputs=4)
class Inference:
    """Container-lifetime model holder + the two HTTP endpoints."""

    @modal.enter()
    def load(self) -> None:
        """Load both models once per container (cold start ~10-30 s)."""
        from sentence_transformers import CrossEncoder, SentenceTransformer

        # ``max_length=256`` mirrors how the checkpoint was trained/evaluated.
        self.ce = CrossEncoder(CE_MODEL_DIR, max_length=256)
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
        return {
            "status": "ok",
            "rerank": CE_MODEL_DIR,
            "rerank_source": f"modal-volume:{CE_VOLUME_NAME}",
            "embed": MODEL_EMBED,
        }

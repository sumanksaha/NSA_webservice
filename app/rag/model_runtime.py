"""Shared torch model runtime — one cache for every RAG encoder.

Before this module five call sites each built their own
``SentenceTransformer`` / ``CrossEncoder`` with a near-copy of the same lazy
import and torch-thread cap: :class:`~app.rag.embedding_service.EmbeddingService`,
:class:`~app.rag.retrieval.dense_retriever.DenseRetriever`,
:class:`~app.rag.retrieval.reranker.Reranker`,
:class:`~app.rag.retrieval.reranker.EnsembleReranker`, and the lazy local
fallbacks in :class:`~app.rag.retrieval.remote_embedder.RemoteEmbedClient` /
:class:`~app.rag.retrieval.remote_reranker.RemoteRerankClient`.

That is fine for a single retriever, but ``build_hybrid_retriever`` is cached
per collection (the multi-domain router builds one per domain). Each collection
therefore loaded its *own* copy of the same mpnet/CE weights — several hundred
MB apiece on a box that cannot hold them. Keying one process-wide cache by
``(kind, model_name)`` means the first caller pays the load and every later
caller shares the model.

Interface::

    from app.rag.model_runtime import get_cross_encoder, get_sentence_transformer

    encoder = get_sentence_transformer(cfg.embedding_model)   # Any | None
    ce = get_cross_encoder(cfg.reranker_model)                # Any | None

Contract:

- **Optional dependency** — returns ``None`` when ``sentence-transformers`` is
  not installed; absence is remembered so repeated calls do not re-import.
- **Lazy, thread-capped** — construction runs through
  :func:`app.rag.torch_runtime.cap_torch_threads` first (``RAG_TORCH_THREADS``),
  so a model build never pegs every core.
- **Load errors propagate** — a present-but-broken model is not silently
  downgraded. The remote clients already wrap construction in their documented
  fallback path; local callers keep their existing failure mode.
- **Injection still wins** — every caller keeps its ``encoder=`` constructor
  argument. Tests never touch the registry.

Call :func:`clear_model_cache` from harnesses that need a cold start (tests
that swap model names). Re-ingestion deliberately does not clear it: the
weights are unchanged, so caching them across a rebuild is the point.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.rag.torch_runtime import cap_torch_threads

logger = logging.getLogger(__name__)

#: Model kinds and their (module, class) import targets.
_MODEL_CLASSES: dict[str, tuple[str, str]] = {
    "sentence_transformer": ("sentence_transformers", "SentenceTransformer"),
    "cross_encoder": ("sentence_transformers", "CrossEncoder"),
}

#: ``(kind, model_name) -> model instance``. Process-wide on purpose: the
#: expensive artefacts are the weights, not the wrapper object.
_models: dict[tuple[str, str], Any] = {}

#: Packages already known to be missing, so every call does not retry an
#: import that can never succeed.
_unavailable: set[str] = set()

_lock = threading.Lock()


def get_sentence_transformer(model_name: str) -> Any | None:
    """Return a cached ``SentenceTransformer`` for *model_name* (or ``None``)."""
    return _get_model("sentence_transformer", model_name)


def get_cross_encoder(model_name: str) -> Any | None:
    """Return a cached ``CrossEncoder`` for *model_name* (or ``None``)."""
    return _get_model("cross_encoder", model_name)


def clear_model_cache() -> None:
    """Drop every cached model and the remembered missing-package state.

    Callers that already hold a reference keep using it; this only affects
    future :func:`get_sentence_transformer` / :func:`get_cross_encoder` calls.
    """
    with _lock:
        _models.clear()
        _unavailable.clear()


def _get_model(kind: str, model_name: str) -> Any | None:
    """Shared lookup/construct path for both model kinds."""
    if not model_name:
        return None
    key = (kind, model_name)

    cached = _models.get(key)
    if cached is not None:
        return cached

    model_class = _import_model_class(kind)
    if model_class is None:
        return None

    with _lock:
        # Another thread may have loaded it while this one waited.
        cached = _models.get(key)
        if cached is not None:
            return cached

        # Bound torch threads before construction (RAG_TORCH_THREADS).
        cap_torch_threads()
        model = model_class(model_name)
        _models[key] = model
        logger.info("model_runtime: loaded %s %s", kind, model_name)
        return model


def _import_model_class(kind: str) -> Any | None:
    """Import the model class for *kind*, remembering package absence."""
    module_name, class_name = _MODEL_CLASSES[kind]
    if module_name in _unavailable:
        return None
    try:
        module = __import__(module_name, fromlist=[class_name])
    except ImportError:
        _unavailable.add(module_name)
        logger.warning(
            "model_runtime: %s not installed; %s unavailable (callers degrade).",
            module_name,
            kind,
        )
        return None
    return getattr(module, class_name)


__all__ = [
    "clear_model_cache",
    "get_cross_encoder",
    "get_sentence_transformer",
]

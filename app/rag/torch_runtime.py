"""Thread/CPU configuration for torch-based RAG components.

The production RAG pipeline (dense embedding + cross-encoder reranking) runs
on CPU in most deployments.  PyTorch defaults to one thread per logical core,
which on small laptops (e.g. the 8-thread i5-1135G7 / 8 GB RAM class) pegs
every core during a single query — freezing the rest of the machine while the
reranker scores a 30-chunk head.

This module is the single place that caps the torch thread pools for RAG
inference, honouring the ``RAG_TORCH_THREADS`` config/env (default 4).
Everything here is best-effort and idempotent; missing torch degrades to a
no-op so callers never need to guard.

Measured on the reference laptop (i5-1135G7, 8 GB, no discrete GPU): a
4-thread cap is both faster than the 8-thread default *and* leaves the
machine usable during a query.
"""

from __future__ import annotations

import contextlib
import logging
import os

from app.shared.config import cfg

logger = logging.getLogger(__name__)


def resolve_torch_threads() -> int:
    """Resolve the RAG torch thread cap via the shared config seam."""
    return max(1, int(cfg.torch_threads))


def cap_torch_threads() -> None:
    """Bound the intra/inter-op thread pools for RAG inference.

    Call from lazy model loaders (``EmbeddingService._get_encoder``,
    ``Reranker._get_encoder``, ``EnsembleReranker._get_encoder``) right
    before the model is constructed.  Idempotent and safe to call
    repeatedly.  ``set_num_interop_threads`` must run before parallel torch
    work starts; if it has already started it raises, which we swallow
    (intra-op capping still applies).
    """
    threads = resolve_torch_threads()
    try:
        import torch

        torch.set_num_threads(threads)
        with contextlib.suppress(Exception):
            torch.set_num_interop_threads(1)
        if os.environ.get("OMP_NUM_THREADS") is None:
            os.environ["OMP_NUM_THREADS"] = str(threads)
    except ImportError:
        pass

"""Post-retrieval enrichment stage registry for the RAG pipeline.

Replaces the 3 inline ``if cfg.X and result.chunks:`` blocks that were
scattered through ``run_retrieval_pipeline`` (tasks.py L255–296) with a
data-driven, ordered registry.  Each stage is independently feature-flagged
and error-isolated (per the design spec).

The enrich functions lazy-import their heavy dependencies so the module boots
without Neo4j, network, or the optional embedding stack — mirroring the
lazy-import pattern in ``retrieval/factory.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.shared.config import cfg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalStage:
    """One enrichment step in the post-retrieval pipeline.

    Attributes:
        name: Human-readable stage identifier (for logging).
        is_enabled: Feature-flag gate (evaluated at apply-time, not import-time).
        enrich: ``(query, result) -> value`` — the enrichment logic.
        output_key: Key under which the value is stored in the output dict.
        default: Value placed in the output dict when the stage is disabled
            or when ``result`` has no chunks.  Must match the original
            initialisation in ``run_retrieval_pipeline``.
        isolate: When True, exceptions are logged and the default is kept
            (the stage degrades gracefully).  When False, exceptions propagate
            — preserving the original behaviour where a stage without a
            try/except guard could abort the whole pipeline.
    """

    name: str
    is_enabled: Callable[[], bool]
    enrich: Callable[[str, Any], Any]
    output_key: str
    default: Any = None
    isolate: bool = True


# ---------------------------------------------------------------------------
# Enrich functions — lazy imports keep boots lightweight.
# ---------------------------------------------------------------------------


def _enrich_legal_identity(query: str, result: Any) -> list[dict[str, Any]]:
    from app.rag.retrieval.legal_identity import parse_legal_identity

    return [parse_legal_identity(c).to_dict() for c in result.chunks]


def _enrich_reference_expansion(query: str, result: Any) -> list[str]:
    from app.rag.retrieval.reference_graph import expand_candidates

    expanded = expand_candidates(result.chunks, top_k=10, depth=1)
    logger.info(
        "apply_stages: reference expansion found %d candidates",
        len(expanded),
    )
    return expanded


def _enrich_evidence_set(query: str, result: Any) -> dict[str, Any]:
    from app.rag.retrieval.evidence_selector import select_evidence_set

    es = select_evidence_set(query, result.chunks, max_size=5, min_size=2)
    data = es.to_dict()
    logger.info(
        "apply_stages: evidence set selected %d items (%s)",
        len(es.items),
        [it["evidence_type"] for it in data["items"]],
    )
    return data


# ---------------------------------------------------------------------------
# Stage definitions (ordered: identity → expansion → evidence)
# ---------------------------------------------------------------------------


def _legal_identity_flag() -> bool:
    from app.rag.retrieval.legal_identity import _legal_identity_enabled

    return _legal_identity_enabled()


def _reference_expansion_flag() -> bool:
    from app.rag.retrieval.reference_graph import _reference_expansion_enabled

    return _reference_expansion_enabled()


POST_RETRIEVAL_STAGES: list[RetrievalStage] = [
    RetrievalStage(
        name="legal_identity",
        is_enabled=_legal_identity_flag,
        enrich=_enrich_legal_identity,
        output_key="legal_identities",
        default=[],
        isolate=False,  # matches original: no try/except wrapper
    ),
    RetrievalStage(
        name="reference_expansion",
        is_enabled=_reference_expansion_flag,
        enrich=_enrich_reference_expansion,
        output_key="expanded_candidates",
        default=[],
        isolate=True,  # matches original: try/except + warning
    ),
    RetrievalStage(
        name="evidence_selector",
        is_enabled=lambda: cfg.evidence_selector,
        enrich=_enrich_evidence_set,
        output_key="evidence_set",
        default=None,
        isolate=True,  # matches original: try/except + warning
    ),
]


def apply_stages(
    query: str,
    result: Any,
    stages: list[RetrievalStage] | None = None,
) -> dict[str, Any]:
    """Apply all enabled post-retrieval enrichment stages to *result*.

    Returns a dict mapping ``output_key -> value`` for every stage, with
    defaults filling in disabled or skipped stages.  When *result* has no
    chunks, all stages are skipped and only defaults are returned.

    Stages whose ``isolate`` flag is True log-and-continue on error; stages
    with ``isolate=False`` propagate exceptions (matching the original inline
    behaviour).
    """
    if stages is None:
        stages = POST_RETRIEVAL_STAGES

    out: dict[str, Any] = {s.output_key: s.default for s in stages}

    if not getattr(result, "chunks", None):
        return out

    for stage in stages:
        if not stage.is_enabled():
            continue
        try:
            logger.debug("apply_stages: running stage %s for query=%r", stage.name, query)
            out[stage.output_key] = stage.enrich(query, result)
        except Exception as exc:
            logger.warning("apply_stages: stage %s failed: %s", stage.name, exc)
            if not stage.isolate:
                raise

    return out

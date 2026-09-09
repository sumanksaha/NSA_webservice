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
from concurrent.futures import ThreadPoolExecutor
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
# Enrich functions — lazy imports keep boots lightweight.
# ---------------------------------------------------------------------------

# Add evidence task based stage
def _enrich_evidence_plan(query: str, result: Any) -> dict[str, Any]:
    """Plan per-task retrieval from Evidence Tasks attached to result.

    Expects result.evidence_tasks (list of EvidenceTask) or
    result["evidence_tasks"] (from dict). Outputs a retrieval plan
    dict mapping task_id -> retrieval_routes.
    """
    from app.rag.evidence_task import EvidenceTask

    evidence_tasks: list[EvidenceTask] | None = None
    if hasattr(result, "evidence_tasks"):
        evidence_tasks = result.evidence_tasks
    elif isinstance(result, dict):
        evidence_tasks = result.get("evidence_tasks")

    if not evidence_tasks:
        return {"retrieval_plan": {}}

    retrieval_plan: dict[str, list[str]] = {}
    for task in evidence_tasks:
        routes = []
        retrieval = task.retrieval
        if retrieval.get("identifier"):
            routes.append("identifier")
        if retrieval.get("lexical"):
            routes.append("lexical")
        if retrieval.get("dense"):
            routes.append("dense")
        if retrieval.get("knowledge_graph"):
            routes.append("knowledge_graph")
        retrieval_plan[task.task_id] = routes

    return {"retrieval_plan": retrieval_plan}


# ---------------------------------------------------------------------------
# Stage definitions (ordered: identity → expansion → evidence)
# ---------------------------------------------------------------------------


def _legal_identity_flag() -> bool:
    from app.rag.retrieval.legal_identity import _legal_identity_enabled

    return _legal_identity_enabled()


def _reference_expansion_flag() -> bool:
    from app.rag.retrieval.reference_graph import _reference_expansion_enabled

    return _reference_expansion_enabled()


def _evidence_plan_flag() -> bool:
    """Feature flag for evidence task planning stage."""
    from app.shared.config import cfg

    return getattr(cfg, "evidence_plan_enabled", False)


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
    RetrievalStage(
        name="evidence_plan",
        is_enabled=_evidence_plan_flag,
        enrich=_enrich_evidence_plan,
        output_key="retrieval_plan",
        default={},
        isolate=True,
    ),
]


def apply_stages(
    query: str,
    result: Any,
    stages: list[RetrievalStage] | None = None,
    parallel: bool = True,
    evidence_tasks: Any | None = None,
) -> dict[str, Any]:
    """Apply all enabled post-retrieval enrichment stages to *result*.

    When *parallel* is True (default), independent stages run concurrently
    via ThreadPoolExecutor — this covers the 2.5 efficiency improvement
    (parallelize independent post-retrieval stages).  Sequential mode is
    used when stages depend on each other's outputs (e.g. evidence_selector
    after reference_expansion, though in practice they are independent).

    *evidence_tasks* (optional list of EvidenceTask) is attached to *result*
    so that the evidence_plan stage can build a per-task retrieval plan.
    """
    if stages is None:
        stages = POST_RETRIEVAL_STAGES

    out: dict[str, Any] = {s.output_key: s.default for s in stages}

    if not getattr(result, "chunks", None):
        return out

    if evidence_tasks is not None:
        result.evidence_tasks = evidence_tasks

    enabled = [s for s in stages if s.is_enabled()]

    if parallel and len(enabled) > 1:
        # 2.5: Parallelize independent post-retrieval stages.
        # Stage isolation ensures one failure doesn't abort the pipeline.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(4, len(enabled))) as executor:
            futures = {executor.submit(_run_stage, s, query, result): s for s in enabled}
            for future in futures:
                stage = futures[future]
                try:
                    out[stage.output_key] = future.result()
                    logger.debug("apply_stages: completed stage %s (parallel)", stage.name)
                except Exception as exc:
                    logger.warning("apply_stages: stage %s failed: %s", stage.name, exc)
                    if not stage.isolate:
                        raise
    else:
        for stage in enabled:
            try:
                logger.debug("apply_stages: running stage %s for query=%r", stage.name, query)
                out[stage.output_key] = stage.enrich(query, result)
            except Exception as exc:
                logger.warning("apply_stages: stage %s failed: %s", stage.name, exc)
                if not stage.isolate:
                    raise

    return out


def _run_stage(stage: RetrievalStage, query: str, result: Any) -> Any:
    return stage.enrich(query, result)

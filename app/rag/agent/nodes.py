"""Graph nodes — thin adapters over the existing RAG services (M3).

Each node is a plain function ``(state: dict[str, Any]) -> partial RAGState``.
They reuse the production pipeline entry points (``run_retrieval_pipeline``
/ ``run_generation_pipeline``) so the agent path and the legacy path share
exactly the same retrieval, reranking, KG-fusion, generation and
verification code — the graph only adds orchestration around them.

Imports inside the functions keep the agent package lazy: the legacy
pipeline never imports LangGraph or this module.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _ms(start: float) -> int:
    """Elapsed milliseconds since ``start``, safe for OS clock adjustments."""
    try:
        return int((time.monotonic() - start) * 1000)
    except (ValueError, TypeError):
        return 0


# Groundedness below this triggers the expand-and-retry loop (plan §5.3).
GROUNDEDNESS_THRESHOLD = 0.7


def _query_for_retrieval(state: dict[str, Any]) -> str:
    """The query to retrieve with.

    Priority: failure-aware targeted query (Phase 2.6 retry) > expanded
    query (groundedness retry) > original query.  Without the targeted
    branch, retries re-retrieved with the *same* query — the Phase 0
    defect where ``targeted_retry`` was decorative.
    """
    return state.get("targeted_query") or state.get("expanded_query") or state.get("query") or ""


def _safe_int(value: Any, default: int) -> int:
    """Coerce to int with a fallback (state values may be None/str)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def classify_node(state: dict[str, Any]) -> dict[str, Any]:
    """Classify the query into a legal query type.

    Wraps :class:`QueryClassifier`; a failure degrades to ``"general"``
    so the graph never stalls on classification.
    """
    start = time.monotonic()
    query = state.get("query") or ""
    query_type = "general"
    detail: dict[str, Any] = {"fallback": False}
    try:
        from app.rag.retrieval import QueryClassifier

        query_type = QueryClassifier().classify(query).value
    except Exception as exc:
        logger.warning("classify_node: classification failed (%s)", exc)
        detail = {"fallback": True, "error": str(exc)}
    return {
        "query_type": query_type,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "classify",
                "latency_ms": _ms(start),
                "detail": {"query_type": query_type, **detail},
            },
        ],
    }


def retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """Retrieve candidate chunks via the Phase 1 pipeline.

    Calls ``run_retrieval_pipeline`` — which already runs hybrid retrieval
    (dense + Qdrant-side BM25 + identifier arm) and the ensemble reranker
    (sec_act features + remote CE when configured).  The returned chunks
    are plain dicts (``RetrievedChunk.to_dict()``), kept JSON-serializable.
    """
    start = time.monotonic()
    from app.rag.tasks import run_retrieval_pipeline

    result = run_retrieval_pipeline(
        query=_query_for_retrieval(state),
        top_k=state.get("top_k", 10),
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
        pipeline="agent",
    )
    return {
        "chunks": result.get("chunks", []),
        "query_type": result.get("query_type") or state.get("query_type", "general"),
        "retrieval_latency_ms": result.get("retrieval_latency_ms", 0),
        "log_id": result.get("log_id"),
        # Evidence set is already computed by apply_stages inside
        # run_retrieval_pipeline — forward it to avoid recompute in the
        # evidence_node downstream.
        "evidence_set": result.get("evidence_set"),
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "retrieve",
                "latency_ms": _ms(start),
                "detail": {
                    "chunk_count": len(result.get("chunks", [])),
                    "retrieval_latency_ms": result.get("retrieval_latency_ms", 0),
                    "log_id": result.get("log_id"),
                },
            },
        ],
    }


def evidence_node(state: dict[str, Any]) -> dict[str, Any]:
    """Pass through the evidence set computed during retrieval.

    The evidence selector already ran inside ``run_retrieval_pipeline``
    (via ``apply_stages``) and ``retrieve_node`` forwarded the result into
    ``state["evidence_set"]``.  This node simply records the pass-through in
    the audit trail — no recomputation, no redundant ``select_evidence_set``
    call.
    """
    start = time.monotonic()
    evidence_set = state.get("evidence_set")
    return {
        "evidence_set": evidence_set,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "evidence",
                "latency_ms": _ms(start),
                "detail": {"evidence_set": evidence_set is not None},
            },
        ],
    }


def generate_node(state: dict[str, Any]) -> dict[str, Any]:
    """Generate a grounded answer from the retrieved chunks.

    Calls ``run_generation_pipeline`` with the chunks already in state
    (skips retrieval) so KG fusion, grounding, hallucination detection and
    logging all run exactly as in the legacy path.
    """
    start = time.monotonic()
    from app.rag.tasks import run_generation_pipeline

    result = run_generation_pipeline(
        query=_query_for_retrieval(state),
        chunks=state.get("chunks"),
        query_type=state.get("query_type", ""),
        top_k=state.get("top_k", 10),
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
        pipeline="agent",
    )
    return {
        "answer": result.get("answer", ""),
        "groundedness": result.get("groundedness_score", 0.0),
        "hallucination_detected": result.get("hallucination_detected", False),
        "response": result,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "generate",
                "latency_ms": _ms(start),
                "detail": {
                    "groundedness": result.get("groundedness_score", 0.0),
                    "hallucination_detected": result.get("hallucination_detected", False),
                    "answer_length": len(result.get("answer", "")),
                },
            },
        ],
    }


def verify_node(state: dict[str, Any]) -> dict[str, Any]:
    """Assess the generated response's groundedness.

    The actual verification (claim extraction, evidence comparison,
    groundedness scoring) already happened inside ``generate_node`` via
    ``run_generation_pipeline``.  This node records the score on the
    state so the graph's conditional edge can route on it; the threshold
    lives in :data:`GROUNDEDNESS_THRESHOLD`.
    """
    return {
        "groundedness": state.get("groundedness", 0.0),
        "hallucination_detected": state.get("hallucination_detected", False),
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "verify",
                "latency_ms": 0,
                "detail": {
                    "groundedness": state.get("groundedness", 0.0),
                    "hallucination_detected": state.get("hallucination_detected", False),
                },
            },
        ],
    }


def citation_quality_node(state: dict[str, Any]) -> dict[str, Any]:
    """Check if cited chunks are actually in the retrieved set.

    Extracts citations from the generated answer (via the ``response``
    dict's ``citations`` field) and verifies each cited ``chunk_id`` is
    present in the retrieved ``chunks`` list.  If a citation references
    a chunk that was never retrieved, it's a hallucinated citation.

    Sets:
    - ``citation_quality_ok``: True if all citations are valid, False otherwise
    - ``missing_citations``: List of chunk_ids cited but not retrieved
    """
    start = time.monotonic()
    response = state.get("response") or {}
    citations = response.get("citations", [])
    # Retrieved set = linear-path chunks + all DAG-path evidence chunks, so
    # citations synthesized from per-task evidence are not flagged missing
    # (on the DAG path ``state["chunks"]`` is empty — Phase 0 fix).
    chunks = list(state.get("chunks") or [])
    for ev_chunks in (state.get("evidence") or {}).values():
        chunks.extend(ev_chunks or [])
    retrieved_chunk_ids = {c.get("chunk_id") for c in chunks if isinstance(c, dict) and c.get("chunk_id")}
    cited_chunk_ids = []
    missing: list[str] = []
    for cit in citations:
        cit_id = cit.get("chunk_id") if isinstance(cit, dict) else None
        if cit_id:
            cited_chunk_ids.append(cit_id)
            if cit_id not in retrieved_chunk_ids:
                missing.append(cit_id)
    quality_ok = len(missing) == 0
    return {
        "citation_quality_ok": quality_ok,
        "missing_citations": missing,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "citation_quality",
                "latency_ms": _ms(start),
                "detail": {
                    "cited_count": len(cited_chunk_ids),
                    "missing_count": len(missing),
                    "quality_ok": quality_ok,
                },
            },
        ],
    }


def targeted_retry_node(state: dict[str, Any]) -> dict[str, Any]:
    """Phase 2.6: failure-aware targeted retry.

    Classifies the verification failures with the deterministic taxonomy
    (:class:`FailureClassifier` — no LLM) and builds a targeted retrieval
    query via :class:`TargetedRetryPlanner`.  The targeted query is stored
    on state and picked up by ``_query_for_retrieval`` on the retry round.

    Phase 0 fix: the previous version imported a nonexistent
    ``classify_failure`` helper (ImportError at runtime) and classified raw
    citation ids instead of the verification result.
    """
    start = time.monotonic()
    from app.rag.planning.failure_classifier import FailureClassifier
    from app.rag.planning.targeted_retry import TargetedRetryPlanner

    verification_result = {
        "missing_citations": state.get("missing_citations") or [],
        "groundedness_score": state.get("groundedness", 0.0),
        "evidence_coverage": state.get("evidence_coverage", 1.0),
    }
    failures = FailureClassifier().classify(verification_result)
    if not failures:
        return {
            "targeted_query": None,
            "audit_trail": [
                *(state.get("audit_trail") or []),
                {
                    "node": "targeted_retry",
                    "latency_ms": _ms(start),
                    "detail": {"skipped": "no_failures"},
                },
            ],
        }

    planner = TargetedRetryPlanner()
    query_type = state.get("query_type", "general")
    target = planner.target_query(
        query=state.get("query", ""),
        failures=failures,
        query_type=query_type,
        context={"collection_name": state.get("collection_name")},
    )

    return {
        "targeted_query": target,
        "retry_count": state.get("retry_count", 0) + 1,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "targeted_retry",
                "latency_ms": _ms(start),
                "detail": {"target": target, "failures": [str(f) for f in failures]},
            },
        ],
    }


def expand_query_node(state: dict[str, Any]) -> dict[str, Any]:
    """Rephrase / expand the query for a grounded retry.

    Reuses :class:`GroundedLLMClient` with a fixed expansion prompt
    (the same client the generation service uses — stub mode makes tests
    network-free).  On failure the original query is kept so the retry
    still proceeds; the retry count is always incremented so the loop
    terminates.
    """
    start = time.monotonic()
    from app.rag.generation.llm_client import GroundedLLMClient

    original = state.get("query") or ""
    expanded = original
    detail: dict[str, Any] = {"changed": False}
    try:
        client = GroundedLLMClient()
        resp = client.call(
            "You are a legal-retrieval query rewriter.",
            (
                "Rewrite the following food-safety legal question to improve "
                "retrieval: keep the statute, section and offence keywords, "
                "and expand abbreviations. Reply with only the rewritten "
                f"query.\n\nOriginal: {original}"
            ),
            temperature=0.0,
            max_tokens=120,
        )
        if resp.success and resp.text.strip():
            expanded = resp.text.strip()
            detail = {"changed": expanded != original}
    except Exception as exc:
        logger.warning("expand_query_node: query expansion failed (%s)", exc)
        detail = {"changed": False, "error": str(exc)}

    return {
        "expanded_query": expanded,
        "retry_count": state.get("retry_count", 0) + 1,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "expand_query",
                "latency_ms": _ms(start),
                "detail": {"retry": state.get("retry_count", 0) + 1, **detail},
            },
        ],
    }


def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Assemble the final ``RAGResponse``-schema result dict.

    Merges the generation result (``state["response"]``) with agent
    metadata: the retry count, the expanded query (if any) and the full
    audit trail.
    """
    response = dict(state.get("response") or {})
    # Phase 0 fix: the abstain path writes ``answer`` without a full
    # response dict — surface it instead of returning an empty response.
    state_answer = state.get("answer") or ""
    if state_answer and not response.get("answer"):
        response["answer"] = state_answer
    response.setdefault("query", state.get("query", ""))
    response.setdefault("query_type", state.get("query_type", "general"))
    response.setdefault("groundedness", state.get("groundedness", 0.0))
    response.setdefault("retrieved_chunks", state.get("chunks", []))
    if state.get("abstained"):
        response.setdefault("abstained", True)
    response["pipeline"] = "agent"
    response["agent"] = {
        "retry_count": state.get("retry_count", 0),
        "expanded_query": state.get("expanded_query"),
        "groundedness": state.get("groundedness", 0.0),
        "hallucination_detected": state.get("hallucination_detected", False),
        "audit_trail": state.get("audit_trail", []),
    }
    return {"response": response}


def reason_node(state: dict[str, Any]) -> dict[str, Any]:
    """Multi-hop reasoning: analyze chunks to decide if more retrieval is needed.

    Priority 3: Multi-hop agent. Generates a brief reasoning note from
    retrieved chunks; sets `need_more_hops` if coverage is incomplete.
    """
    start = time.monotonic()
    chunks = state.get("chunks", [])
    chunk_text = "\n\n".join(c.get("text", "")[:300] for c in chunks[:3])
    reasoning = f"Reviewed {len(chunks)} chunks. Coverage {'sufficient' if len(chunks) >= 3 else 'incomplete'}."
    need_more = len(chunks) < 3 or len(chunk_text) < 500
    return {
        "reasoning": reasoning,
        "need_more_hops": need_more,
        "hop_count": state.get("hop_count", 0) + 1,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {"node": "reason", "latency_ms": _ms(start), "detail": {"need_more": need_more}},
        ],
    }


def plan_node(state: dict[str, Any]) -> dict[str, Any]:
    """Build a structured query plan of EvidenceTasks with a complexity label.

    Uses QueryPlanner to decompose the query into EvidenceTasks with a DAG.
    The plan is stored **serialized** (JSON-safe dicts) so downstream nodes
    and the checkpointer can read it: ``query_plan["tasks"]`` feeds
    ``plan_tasks_node`` and ``query_plan["complexity"]`` drives the
    post-plan router (linear vs DAG path).

    Phase 0 fixes: the previous version called ``QueryPlanner.plan(query,
    query_type)`` (TypeError — ``plan`` takes only the query) and read
    ``plan.subquestions``, which does not exist on ``DecompositionResult``.
    """
    start = time.monotonic()
    from app.rag.planning.query_planner import QueryPlanner

    query = state.get("query") or ""
    plan = QueryPlanner().plan(query)
    task_dicts = [t.to_dict() for t in plan.tasks]
    dag_valid = not plan.dag.has_cycle()
    return {
        "query_plan": {
            "intent": plan.intent.value,
            "complexity": plan.complexity.value,
            "total_tasks": plan.total_tasks,
            "dag_valid": dag_valid,
            "tasks": task_dicts,
        },
        "subquestions": [t.task_id for t in plan.tasks],
        "evidence_requirements": [t.evidence_requirement.value for t in plan.tasks],
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "plan",
                "latency_ms": _ms(start),
                "detail": {
                    "intent": plan.intent.value,
                    "complexity": plan.complexity.value,
                    "task_count": len(task_dicts),
                    "dag_valid": dag_valid,
                },
            },
        ],
    }


def plan_tasks_node(state: dict[str, Any]) -> dict[str, Any]:
    """P1: build the EvidenceTask DAG from the query plan.

    Phase 0 fix: the previous version read ``state["evidence_tasks"]``,
    which nothing ever populated, so the DAG path was a no-op.  Tasks now
    come from the serialized plan written by ``plan_node``
    (``query_plan["tasks"]``); an externally injected ``evidence_tasks``
    list (EvidenceTask objects or dicts) is still honoured as a fallback.
    """
    start = time.monotonic()
    from app.rag.evidence_task import EvidenceTask, TaskDAG

    plan = state.get("query_plan")
    raw_tasks: list[Any] = []
    source = "none"
    if isinstance(plan, dict) and plan.get("tasks"):
        raw_tasks = list(plan["tasks"])
        source = "query_plan"
    else:
        external = state.get("evidence_tasks") or []
        if external:
            raw_tasks = list(external)
            source = "external"

    parsed: list[EvidenceTask] = []
    for raw in raw_tasks:
        try:
            if isinstance(raw, EvidenceTask):
                parsed.append(raw)
            else:
                parsed.append(EvidenceTask.from_dict(raw))
        except (ValueError, TypeError) as exc:
            logger.warning("plan_tasks_node: skipping unparsable task (%s)", exc)

    tasks: dict[str, dict[str, Any]] = {}
    task_order: list[str] = []
    valid = True
    if parsed:
        dag = TaskDAG()
        for task in parsed:
            dag.add_task(task)
        valid = not dag.has_cycle()
        task_order = [t.task_id for t in dag.topological_order()]
        tasks = {t.task_id: t.to_dict() for t in parsed}

    return {
        "tasks": tasks,
        "task_order": task_order,
        "dag_valid": valid,
        "tasks_completed": 0,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "plan_tasks",
                "latency_ms": _ms(start),
                "detail": {"task_count": len(parsed), "dag_valid": valid, "source": source},
            },
        ],
    }


def execute_task_node(state: dict[str, Any]) -> dict[str, Any]:
    """P1: execute the EvidenceTask DAG in topological order.

    For each task whose dependencies are satisfied, run task-scoped
    retrieval (the EvidenceTask is attached so the per-task retrieval-plan
    stage can use it) and store the chunks under ``evidence[task_id]``.
    Tasks with unmet dependencies are skipped and recorded in the audit
    trail (Phase 1 will route them to failure diagnosis).  Budget counters
    are consumed here so the downstream gates see real usage (Phase 0 fix:
    the budget gate previously never consumed anything, so abstention on
    budget exhaustion was unreachable).
    """
    start = time.monotonic()
    from app.rag.evidence_task import EvidenceTask
    from app.rag.tasks import run_retrieval_pipeline

    tasks = state.get("tasks") or {}
    task_order = state.get("task_order") or []
    evidence: dict[str, list] = dict(state.get("evidence") or {})
    budget = dict(state.get("budget") or {})
    completed: set[str] = set()
    skipped: list[str] = []
    documents_used = 0

    for task_id in task_order:
        raw = tasks.get(task_id)
        if raw is None:
            continue
        try:
            task = raw if isinstance(raw, EvidenceTask) else EvidenceTask.from_dict(raw)
        except (ValueError, TypeError) as exc:
            logger.warning("execute_task_node: unparsable task %s (%s)", task_id, exc)
            skipped.append(task_id)
            continue
        if not all(dep in completed for dep in task.dependency):
            skipped.append(task_id)
            continue
        result = run_retrieval_pipeline(
            query=task.question or state.get("query", ""),
            top_k=state.get("top_k", 10),
            collection_name=state.get("collection_name"),
            filters=state.get("filters"),
            pipeline="agent",
            evidence_tasks=[task],
        )
        chunks = result.get("chunks", [])
        evidence[task_id] = chunks
        documents_used += len(chunks)
        completed.add(task_id)

    budget["consumed_tasks"] = _safe_int(budget.get("consumed_tasks"), 0) + len(completed)
    budget["consumed_documents"] = _safe_int(budget.get("consumed_documents"), 0) + documents_used
    budget["consumed_retrieval_rounds"] = _safe_int(budget.get("consumed_retrieval_rounds"), 0) + (
        1 if completed else 0
    )

    return {
        "evidence": evidence,
        "tasks_completed": len(completed),
        "budget": budget,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "execute_task",
                "latency_ms": _ms(start),
                "detail": {"tasks_completed": len(completed), "skipped": skipped, "documents": documents_used},
            },
        ],
    }


def evidence_sufficiency_node(state: dict[str, Any]) -> dict[str, Any]:
    """P2: Evidence sufficiency gate. Checks per-task evidence coverage.

    Routes to ``synthesize`` when sufficient, ``targeted_retry`` when
    recoverable, or ``abstain`` when evidence is critically insufficient
    (budget exhausted and coverage < 0.3).
    """
    start = time.monotonic()
    evidence = state.get("evidence") or {}
    tasks = state.get("tasks") or {}
    total_tasks = len(tasks)
    # Coverage counts tasks with NON-EMPTY evidence — execute_task stores an
    # empty list per task even when retrieval found nothing, and counting
    # bare keys made empty evidence read as fully covered (Phase 0 fix).
    covered = sum(1 for chunks in evidence.values() if chunks)
    coverage = covered / total_tasks if total_tasks else 0.0
    retry_count = _safe_int(state.get("retry_count"), 0)
    max_retries = _safe_int(state.get("max_retries"), 2)
    budget_exhausted = bool(state.get("budget_exhausted")) or retry_count >= max_retries
    citation_ok = bool(state.get("citation_quality_ok", True))
    hallucinated = bool(state.get("hallucination_detected", False))
    # Sufficient only when there are tasks to cover, most are covered, and
    # the verification signals are clean.  (Phase 0 fix: the old expression
    # passed two positional args to ``any()`` — a TypeError at runtime.)
    sufficient = total_tasks > 0 and coverage >= 0.5 and citation_ok and not hallucinated
    # Abstain when the budget is exhausted with critically low coverage, or
    # when there is nothing to synthesize from at all (no tasks, no
    # evidence).  Written to state so ``_route_after_evidence`` can route.
    abstain_required = (budget_exhausted and coverage < 0.3) or (total_tasks == 0 and not evidence)
    return {
        "evidence_coverage": coverage,
        "evidence_sufficient": sufficient,
        "budget_exhausted": budget_exhausted,
        "abstain_required": abstain_required,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "evidence_sufficiency",
                "latency_ms": _ms(start),
                "detail": {
                    "coverage": coverage,
                    "sufficient": sufficient,
                    "budget_exhausted": budget_exhausted,
                    "abstain_required": abstain_required,
                    "total_tasks": total_tasks,
                    "tasks_with_evidence": covered,
                },
            },
        ],
    }


def budget_gate_node(state: dict[str, Any]) -> dict[str, Any]:
    """P3: budget gate before DAG execution.

    Pure read of the consumed counters vs the configured maxima — the
    counters themselves are consumed by the executor nodes (execute_task,
    targeted_retry).  When the budget is exhausted the graph abstains
    instead of executing more tasks.  Phase 0 fix: this node previously
    rewrote the counters without ever incrementing them, so exhaustion was
    unreachable.
    """
    budget = dict(state.get("budget") or {})
    exhausted = (
        _safe_int(budget.get("consumed_tasks"), 0) >= _safe_int(budget.get("max_tasks"), 10)
        or _safe_int(budget.get("consumed_retrieval_rounds"), 0) >= _safe_int(budget.get("max_retrieval_rounds"), 5)
        or _safe_int(budget.get("consumed_documents"), 0) >= _safe_int(budget.get("max_documents"), 50)
        or _safe_int(budget.get("consumed_llm_calls"), 0) >= _safe_int(budget.get("max_llm_calls"), 20)
    )
    return {
        "budget": budget,
        "budget_exhausted": exhausted,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "budget_gate",
                "latency_ms": 0,
                "detail": {
                    "exhausted": exhausted,
                    "consumed_tasks": _safe_int(budget.get("consumed_tasks"), 0),
                    "max_tasks": _safe_int(budget.get("max_tasks"), 10),
                },
            },
        ],
    }


def abstain_node(state: dict[str, Any]) -> dict[str, Any]:
    """P2: Abstain when evidence is insufficient. Returns an abstention answer."""
    return {
        "answer": "INSUFFICIENT EVIDENCE: The evidence gathered does not support a defensible answer. Additional retrieval (e.g., targeted retry or expanded query) or expert review may be required.",
        "groundedness": 0.0,
        "hallucination_detected": False,
        "abstained": True,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {"node": "abstain", "latency_ms": 0, "detail": {"reason": "insufficient_evidence"}},
        ],
    }


def synthesize_node(state: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    from app.rag.tasks import run_generation_pipeline

    evidence = state.get("evidence") or {}
    merged = []
    seen: set[str] = set()
    for chunks in evidence.values():
        for c in chunks:
            cid = c.get("chunk_id")
            if cid and cid not in seen:
                merged.append(c)
                seen.add(cid)
    if not merged:
        merged = state.get("chunks", [])
    result = run_generation_pipeline(
        query=state.get("query", ""),
        chunks=merged,
        query_type=state.get("query_type", ""),
        top_k=state.get("top_k", 10),
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
        pipeline="agent",
    )
    # Consume the LLM-call budget counter (synthesis is one LLM call) so a
    # later budget gate sees real usage.
    budget = dict(state.get("budget") or {})
    budget["consumed_llm_calls"] = _safe_int(budget.get("consumed_llm_calls"), 0) + 1
    return {
        "budget": budget,
        "answer": result.get("answer", ""),
        "groundedness": result.get("groundedness_score", 0.0),
        "hallucination_detected": result.get("hallucination_detected", False),
        "response": result,
        "final_answer": result.get("answer", ""),
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "synthesize",
                "latency_ms": _ms(start),
                "detail": {"merged_chunks": len(merged), "groundedness": result.get("groundedness_score", 0.0)},
            },
        ],
    }


def multi_hop_retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """Targeted retrieval using reasoning note — multi-hop for cross-reference / case-law.

    Priority 3: Inspects retrieved chunks for cross-references (via
    ReferenceExtractor), builds follow-up queries, and merges results.
    Activated for query_type ``cross_reference`` or ``case_law``.
    """
    start = time.monotonic()
    from app.rag.tasks import run_retrieval_pipeline

    reasoning = state.get("reasoning", "")
    query = state.get("expanded_query") or state.get("query", "")
    query_type = state.get("query_type", "general")

    # First pass — standard retrieval (re-uses existing chunks if present).
    result = run_retrieval_pipeline(
        query=query,
        top_k=state.get("top_k", 10),
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
        pipeline="agent",
    )
    chunks = result.get("chunks", [])

    # Multi-hop: only for complex cross-reference / case-law queries.
    # Extract cross-references from retrieved chunks and build a follow-up.
    if query_type in ("cross_reference", "case_law") and chunks:
        try:
            from app.rag.retrieval.reference_extractor import ReferenceExtractor

            refs = ReferenceExtractor().extract_references(chunks)
            # Build refined query from first cross-reference found.
            if refs:
                first_ref = refs[0]
                refined = f"{query} AND {first_ref.get('text', '')}"
                # Second retrieval pass — merge results.
                result2 = run_retrieval_pipeline(
                    query=refined,
                    top_k=state.get("top_k", 10),
                    collection_name=state.get("collection_name"),
                    filters=state.get("filters"),
                    pipeline="agent",
                )
                chunks2 = result2.get("chunks", [])
                # Merge — prefer second-pass chunks that don't duplicate
                # first-pass chunk_ids, preserving RRF score order.
                seen = {c.get("chunk_id") for c in chunks}
                for c in chunks2:
                    if c.get("chunk_id") not in seen:
                        chunks.append(c)
                        seen.add(c.get("chunk_id"))
                result = {**result, "chunks": chunks, "total": len(chunks)}
        except Exception as exc:
            logger.warning("multi_hop_retrieve_node: cross-ref extraction failed (%s)", exc)

    return {
        "chunks": chunks,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "multi_hop_retrieve",
                "latency_ms": _ms(start),
                "detail": {
                    "refined": bool(reasoning),
                    "query_type": query_type,
                    "multi_hop": query_type in ("cross_reference", "case_law"),
                },
            },
        ],
    }

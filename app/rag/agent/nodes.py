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
    """The query to retrieve with — the expanded query when one exists."""
    return state.get("expanded_query") or state.get("query") or ""


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
    chunks = state.get("chunks", [])
    retrieved_chunk_ids = {c.get("chunk_id") for c in chunks if c.get("chunk_id")}
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
    """Phase 2.6: Targeted retry node using failure-aware retrieval.

    Diagnoses verification failures and triggers targeted retrieval queries
    based on the failure classification (missing provision, wrong act, etc.).
    """
    start = time.monotonic()
    from app.rag.planning.failure_classifier import classify_failure
    from app.rag.planning.targeted_retry import TargetedRetryPlanner

    failures = state.get("missing_citations", [])
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
        failures=[classify_failure(f) for f in failures],
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
                "detail": {"target": target, "failures": failures},
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
    response.setdefault("query", state.get("query", ""))
    response.setdefault("query_type", state.get("query_type", "general"))
    response.setdefault("retrieved_chunks", state.get("chunks", []))
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
    """Build a structured query plan with subquestions and evidence requirements.

    Uses QueryPlanner to decompose compound queries and produce a DAG of
    subquestions with evidence requirements.  Sets ``query_plan`` on state
    so downstream nodes (retrieve, evidence, generate) can use it.
    """
    start = time.monotonic()
    from app.rag.planning.query_planner import QueryPlanner

    query = state.get("query") or ""
    query_type = str(state.get("query_type", "general"))
    plan = QueryPlanner().plan(query, query_type)
    return {
        "query_plan": plan,
        "subquestions": [sq.id for sq in plan.subquestions],
        "evidence_requirements": [er.requirement_id for er in plan.evidence_requirements],
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "plan",
                "latency_ms": _ms(start),
                "detail": {
                    "intent": plan.intent.value,
                    "subquestion_count": len(plan.subquestions),
                    "evidence_req_count": len(plan.evidence_requirements),
                },
            },
        ],
    }


def plan_tasks_node(state: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    from app.rag.evidence_task import TaskDAG

    evidence_tasks = state.get("evidence_tasks") or []
    dag = TaskDAG()
    for task in evidence_tasks:
        dag.add_task(task)
    valid = not dag.has_cycle()
    return {
        "tasks": {t.task_id: t for t in evidence_tasks},
        "task_order": [t.task_id for t in dag.topological_order()],
        "dag_valid": valid,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "plan_tasks",
                "latency_ms": _ms(start),
                "detail": {"task_count": len(evidence_tasks), "dag_valid": valid},
            },
        ],
    }


def execute_task_node(state: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    from app.rag.tasks import run_retrieval_pipeline

    tasks = state.get("tasks") or {}
    task_order = state.get("task_order") or []
    evidence: dict[str, list] = dict(state.get("evidence") or {})
    completed: set[str] = set()
    for task_id in task_order:
        task = tasks.get(task_id)
        if task is None or not all(dep in completed for dep in task.dependency):
            continue
        result = run_retrieval_pipeline(
            query=task.question or state.get("query", ""),
            top_k=state.get("top_k", 10),
            collection_name=state.get("collection_name"),
            filters=state.get("filters"),
            pipeline="agent",
            evidence_tasks=[task],
        )
        evidence[task_id] = result.get("chunks", [])
        completed.add(task_id)
    return {
        "evidence": evidence,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {"node": "execute_task", "latency_ms": _ms(start), "detail": {"tasks_completed": len(completed)}},
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
    completed_tasks = len(evidence)
    total_tasks = len(tasks)
    coverage = completed_tasks / max(total_tasks, 1)
    # Check budget exhaustion
    try:
        retry_count = int(state.get("retry_count", 0))
    except (TypeError, ValueError):
        retry_count = 0
    try:
        max_retries = int(state.get("max_retries", 2))
    except (TypeError, ValueError):
        max_retries = 2
    budget_exhausted = retry_count >= max_retries
    sufficient = coverage >= 0.5 and not any(
        not state.get("citation_quality_ok", True),
        state.get("hallucination_detected", False),
    )
    # Abstain if budget exhausted and coverage is critically low
    abstain_required = budget_exhausted and coverage < 0.3
    return {
        "evidence_coverage": coverage,
        "evidence_sufficient": sufficient,
        "budget_exhausted": budget_exhausted,
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
                },
            },
        ],
    }


def budget_gate_node(state: dict[str, Any]) -> dict[str, Any]:
    """P3: Budget gate. Checks budget consumption against limits.

    Updates consumed counters and returns whether budget is exhausted.
    """
    budget = state.get("budget") or {}
    max_tasks = budget.get("max_tasks", 10)
    max_retrieval_rounds = budget.get("max_retrieval_rounds", 5)
    max_documents = budget.get("max_documents", 50)
    max_llm_calls = budget.get("max_llm_calls", 20)
    consumed_tasks = budget.get("consumed_tasks", 0)
    consumed_rounds = budget.get("consumed_retrieval_rounds", 0)
    consumed_docs = budget.get("consumed_documents", 0)
    consumed_llm = budget.get("consumed_llm_calls", 0)
    exhausted = (
        consumed_tasks >= max_tasks
        or consumed_rounds >= max_retrieval_rounds
        or consumed_docs >= max_documents
        or consumed_llm >= max_llm_calls
    )
    return {
        "budget": {
            **budget,
            "consumed_tasks": consumed_tasks,
            "consumed_retrieval_rounds": consumed_rounds,
            "consumed_documents": consumed_docs,
            "consumed_llm_calls": consumed_llm,
        },
        "budget_exhausted": exhausted,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {"node": "budget_gate", "latency_ms": 0, "detail": {"exhausted": exhausted}},
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
    return {
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

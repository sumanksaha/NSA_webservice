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


def _verify_claims(answer: str, chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Claim-level verification of a generated answer (V2 plan item 15).

    Extracts factual claims via the rule-based :class:`ClaimExtractor` and
    verifies each against the evidence chunks via the
    :class:`EvidenceVerifier` (section-match + textual overlap, no LLM).
    Returns ``None`` when the answer carries no verifiable claims.
    """
    if not answer or not answer.strip():
        return None
    from app.rag.agent.sufficiency import as_retrieved_chunks
    from app.rag.verification.claim_extractor import ClaimExtractor
    from app.rag.verification.evidence_verifier import EvidenceVerifier

    claims = ClaimExtractor().extract(answer)
    if not claims:
        return None
    evidence_chunks = as_retrieved_chunks([c for c in chunks if isinstance(c, dict)])
    verifications = EvidenceVerifier().verify_claims(claims, evidence_chunks)
    claim_dicts = [
        {
            **c.to_dict(),
            "verified": v.verified,
            "confidence": round(v.confidence, 3),
            "method": v.method,
            "supporting_chunks": v.supporting_chunks,
        }
        for c, v in zip(claims, verifications, strict=True)
    ]
    verified_count = sum(1 for v in verifications if v.verified)
    return {
        "claims": claim_dicts,
        "claim_groundedness": verified_count / len(claims),
        "unverified_claims": [
            c.text for c, v in zip(claims, verifications, strict=True) if not v.verified
        ],
    }


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


# Historical budget defaults, used when a state carries no budget dict
# (unit-test fixtures) so consumption never raises.
_BUDGET_DEFAULTS = {
    "max_tasks": 10,
    "max_retrieval_rounds": 5,
    "max_documents": 50,
    "max_llm_calls": 20,
}


def _consume_budget(
    state: dict[str, Any],
    *,
    retrieval_rounds: int = 0,
    documents: int = 0,
    llm_calls: int = 0,
) -> dict[str, Any]:
    """Increment the budget's consumed counters (Phase 3).

    Counters are clamped at their caps so an oversized batch (e.g. a
    retrieval returning more chunks than ``max_documents``) cannot push a
    counter past its tier ceiling.  Missing caps/counters default to the
    historical values, so nodes can consume budget on any state shape.
    """
    budget = dict(state.get("budget") or {})
    for key, default in _BUDGET_DEFAULTS.items():
        budget.setdefault(key, default)
    for key in ("consumed_tasks", "consumed_retrieval_rounds", "consumed_documents", "consumed_llm_calls"):
        budget.setdefault(key, 0)

    def _bump(counter: str, amount: int, cap_key: str) -> None:
        if amount <= 0:
            return
        cap = _safe_int(budget.get(cap_key), _BUDGET_DEFAULTS[cap_key])
        budget[counter] = min(_safe_int(budget.get(counter), 0) + amount, cap)

    _bump("consumed_retrieval_rounds", retrieval_rounds, "max_retrieval_rounds")
    _bump("consumed_documents", documents, "max_documents")
    _bump("consumed_llm_calls", llm_calls, "max_llm_calls")
    return budget


def retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """Retrieve candidate chunks via the Phase 1 pipeline.

    Calls ``run_retrieval_pipeline`` — which already runs hybrid retrieval
    (dense + Qdrant-side BM25 + identifier arm) and the ensemble reranker
    (sec_act features + remote CE when configured).  The returned chunks
    are plain dicts (``RetrievedChunk.to_dict()``), kept JSON-serializable.

    Phase 3: consumes the retrieval budget (rounds + documents) so the
    linear path enforces the same caps as the DAG path.
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
        "budget": _consume_budget(state, retrieval_rounds=1, documents=len(result.get("chunks", []))),
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
    # Claim-level verification (item 15): extract + entail-check the answer's
    # claims against the retrieved evidence.  Threshold enforcement happens
    # in the verify/citation gate — this node only measures.
    claim_report = _verify_claims(result.get("answer", ""), state.get("chunks") or [])
    update: dict[str, Any] = {
        "answer": result.get("answer", ""),
        "groundedness": result.get("groundedness_score", 0.0),
        "hallucination_detected": result.get("hallucination_detected", False),
        "response": result,
        # Phase 3: generation is an LLM call — consume the budget so the
        # linear path's LLM usage counts toward the tier cap.
        "budget": _consume_budget(state, llm_calls=1),
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "generate",
                "latency_ms": _ms(start),
                "detail": {
                    "groundedness": result.get("groundedness_score", 0.0),
                    "hallucination_detected": result.get("hallucination_detected", False),
                    "answer_length": len(result.get("answer", "")),
                    "claims": len(claim_report["claims"]) if claim_report else 0,
                },
            },
        ],
    }
    if claim_report:
        update["claims"] = claim_report["claims"]
        update["claim_groundedness"] = claim_report["claim_groundedness"]
        update["unverified_claims"] = claim_report["unverified_claims"]
    return update


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
    # Phase 2 (item 16): the sufficiency gate's rubric failures arrive as
    # ready-made taxonomy codes (EVIDENCE_CONTRADICTION, TEMPORAL_INVALIDITY,
    # INSUFFICIENT_AUTHORITY_SCORE, …) — the live contradiction/temporal/
    # authority signals feed diagnosis through them, so the classifier only
    # needs the non-rubric signals (citations, groundedness, coverage).
    failures: list[str] = list(state.get("diagnosis_failures") or [])
    failures.extend(FailureClassifier().classify(verification_result))
    # Dedupe while preserving order.
    seen: set[str] = set()
    failures = [f for f in failures if not (f in seen or seen.add(f))]
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
        # Phase 3: query rewriting is an LLM call — consume the budget
        # even when the call fails (the spend already happened).
        "budget": _consume_budget(state, llm_calls=1),
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
    # Phase 1: surface per-task DAG outcomes (status / confidence /
    # failure_reason) so callers and evaluation can see decomposition health.
    if state.get("task_results"):
        response["agent"]["task_results"] = state["task_results"]
        response["agent"]["evidence_coverage"] = state.get("evidence_coverage", 0.0)
    # Phase 3: routing economics telemetry — which strategy ran, at which
    # budget tier, and what it actually consumed.
    if state.get("routing_decision"):
        response["agent"]["routing"] = {
            "decision": state["routing_decision"],
            "budget": state.get("budget"),
        }
    # Phase 2: claim-level verification + sufficiency signals on the payload.
    if state.get("claims"):
        response["agent"]["claim_groundedness"] = state.get("claim_groundedness", 0.0)
        response["agent"]["unverified_claims"] = state.get("unverified_claims", [])
    if state.get("task_sufficiency"):
        response["agent"]["has_conflicts"] = bool(state.get("has_conflicts", False))
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

    Phase 3 (item 18): the plan node also makes the *routing economics*
    decision — DIRECT vs decomposition vs DAG, with a DIRECT override for
    single-identifier lookups — and shrinks the state budget to the
    strategy's tier (shrink-only: explicit caller caps are never raised).

    Phase 0 fixes: the previous version called ``QueryPlanner.plan(query,
    query_type)`` (TypeError — ``plan`` takes only the query) and read
    ``plan.subquestions``, which does not exist on ``DecompositionResult``.
    """
    start = time.monotonic()
    from app.rag.agent.routing_economics import apply_budget_tier, route_strategy
    from app.rag.planning.query_planner import QueryPlanner

    query = state.get("query") or ""
    plan = QueryPlanner().plan(query)
    task_dicts = [t.to_dict() for t in plan.tasks]
    dag_valid = not plan.dag.has_cycle()
    decision = route_strategy(
        {"complexity": plan.complexity.value},
        str(state.get("query_type", "")),
        query,
        retry_count=_safe_int(state.get("retry_count"), 0),
        prior_decision=state.get("routing_decision"),
    )
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
        "routing_decision": decision,
        "budget": apply_budget_tier(state.get("budget"), decision["tier"]),
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
                    "strategy": decision["strategy"],
                    "tier": decision["tier"],
                    "pinned": decision["pinned"],
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


def _dependency_chunks(state: dict[str, Any], task: Any) -> list[dict[str, Any]]:
    """Evidence produced by this task's dependencies (the DAG-edge payload).

    The worker receives the *wave view* of the state: before each wave the
    executor refreshes ``state["evidence"]`` with the live evidence mapping,
    which by construction already contains every completed dependency's
    results — exactly the inputs cross-reference expansion should mine.
    """
    evidence = state.get("evidence") or {}
    chunks: list[dict[str, Any]] = []
    for dep in getattr(task, "dependency", None) or []:
        dep_evidence = evidence.get(dep)
        if isinstance(dep_evidence, list):
            chunks.extend(c for c in dep_evidence if isinstance(c, dict))
    return chunks


#: Max cross-reference expansion queries per task (budget containment).
_CROSS_REF_LIMIT = 2


def _cross_reference_queries(question: str, dep_chunks: list[dict[str, Any]]) -> list[str]:
    """Deterministic cross-reference expansion queries for one task.

    Mines "section N" references from the *dependency* tasks' evidence and
    renders each as ``"<question>, section N"``.  The retrieval pipeline's
    identifier route turns that into a ``"{Act} section {N}"`` lexical arm,
    so cross-reference resolution stays a first-class deterministic
    capability instead of hoping semantic search finds the referenced
    provision (V2 proposal #9).  References already mentioned in the
    question are skipped.
    """
    try:
        from app.rag.retrieval.reference_extractor import extract_references
    except ImportError:  # pragma: no cover - optional dependency
        return []
    q = question.lower()
    queries: list[str] = []
    seen_sections: set[str] = set()
    base = question.rstrip(". ")
    for chunk in dep_chunks:
        text = str(chunk.get("text") or "")
        if not text:
            continue
        try:
            refs = extract_references(text)
        except Exception as exc:  # best-effort expansion
            logger.debug("_cross_reference_queries: extraction failed (%s)", exc)
            continue
        for ref in refs:
            section = str(getattr(ref, "section", "") or "")
            if not section or section in seen_sections or f"section {section}" in q:
                continue
            seen_sections.add(section)
            queries.append(f"{base}, section {section}".strip())
            if len(queries) >= _CROSS_REF_LIMIT:
                return queries
    return queries


def _run_task_retrieval(
    task_id: str,
    task: Any,
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run task-scoped retrieval for one EvidenceTask (the wave worker body).

    The task object is attached (``evidence_tasks=[task]``) so the per-task
    ``evidence_plan`` retrieval stage can shape the strategy; deterministic
    cross-reference expansion queries mined from dependency evidence are run
    as additional passes and merged into the task's chunk list (deduped by
    ``chunk_id``).

    Returns ``(chunks, summary)`` where *summary* is the per-task result
    record: ``status`` / ``confidence`` / ``failure_reason`` / counts (plan
    item 11 — per-task status, confidence and failure reason).
    """
    from app.rag.tasks import run_retrieval_pipeline

    start = time.monotonic()
    top_k = _safe_int(state.get("top_k"), 10)
    question = (getattr(task, "question", None) or "").strip() or (state.get("query") or "")
    dep_chunks = _dependency_chunks(state, task)
    expansion_queries = _cross_reference_queries(question, dep_chunks)

    result = run_retrieval_pipeline(
        query=question,
        top_k=top_k,
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
        pipeline="agent",
        evidence_tasks=[task],
    )
    chunks: list[dict[str, Any]] = [c for c in (result.get("chunks") or []) if isinstance(c, dict)]
    cross_refs: list[dict[str, Any]] = []
    if expansion_queries:
        seen_ids = {str(c.get("chunk_id")) for c in chunks if c.get("chunk_id")}
        for xq in expansion_queries[:_CROSS_REF_LIMIT]:
            try:
                xr = run_retrieval_pipeline(
                    query=xq,
                    top_k=top_k,
                    collection_name=state.get("collection_name"),
                    filters=state.get("filters"),
                    pipeline="agent",
                    evidence_tasks=[task],
                )
            except Exception as exc:  # best-effort: expansion never fails the task
                logger.warning("execute_task_node: cross-ref expansion failed for %s (%s)", task_id, exc)
                continue
            for chunk in xr.get("chunks") or []:
                if not isinstance(chunk, dict):
                    continue
                cid = str(chunk.get("chunk_id") or id(chunk))
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                chunk.setdefault("via_cross_reference", xq)
                chunks.append(chunk)
                cross_refs.append({"query": xq, "chunk_id": chunk.get("chunk_id")})

    status = "completed" if chunks else "no_results"
    summary: dict[str, Any] = {
        "status": status,
        # Transparent placeholder heuristic (0→0, top_k hits→1.0); the
        # Phase 2 per-task sufficiency rubric replaces this with real
        # coverage/relevance/authority signals.
        "confidence": min(1.0, len(chunks) / max(1, top_k)),
        "failure_reason": None if chunks else "NO_RESULTS",
        "evidence_count": len(chunks),
        "latency_ms": _ms(start),
        "queries": 1 + min(len(expansion_queries), _CROSS_REF_LIMIT) if expansion_queries else 1,
        "cross_references": cross_refs,
    }
    return chunks, summary


def execute_task_node(state: dict[str, Any]) -> dict[str, Any]:
    """P1 (Phase 1): execute the EvidenceTask DAG in parallel waves.

    A *wave* is the set of tasks whose dependencies are all completed;
    wave members run concurrently on a ``ThreadPoolExecutor`` (consistent
    with the retrieval ``apply_stages`` parallelism; ``RAG_AGENT_\
    TASK_PARALLELISM`` / ``cfg.task_parallelism`` disables it).  The next
    wave starts only when every dependency it waits on has finished, so
    dependent tasks see their upstream evidence in ``state["evidence"]``
    and cross-reference expansion can mine it.

    Per-task outcomes land in ``task_results`` (status / confidence /
    failure_reason / counts) and chunks in ``evidence[task_id]``.  Tasks
    that can never run (missing or failed dependencies) are recorded as
    ``failed`` with ``unmet_dependencies`` instead of being silently
    skipped.  Budget counters are consumed here so the downstream gates
    see real usage.
    """
    start = time.monotonic()
    from concurrent.futures import ThreadPoolExecutor

    from app.rag.evidence_task import EvidenceTask
    from app.shared.config import cfg

    tasks = state.get("tasks") or {}
    task_order = state.get("task_order") or []
    evidence: dict[str, list] = dict(state.get("evidence") or {})
    task_results: dict[str, dict[str, Any]] = dict(state.get("task_results") or {})
    budget = dict(state.get("budget") or {})
    parallel = bool(getattr(cfg, "task_parallelism", True))

    parsed: dict[str, Any] = {}
    for task_id, raw in tasks.items():
        if raw is None:
            continue
        try:
            parsed[task_id] = raw if isinstance(raw, EvidenceTask) else EvidenceTask.from_dict(raw)
        except (ValueError, TypeError) as exc:
            logger.warning("execute_task_node: unparsable task %s (%s)", task_id, exc)
            task_results[task_id] = {
                "status": "failed",
                "failure_reason": "UNPARSABLE_TASK",
                "confidence": 0.0,
                "evidence_count": 0,
            }

    # Tasks already completed in a previous round (state carries
    # task_results across retries) are not re-executed.
    completed = {
        tid for tid, tr in task_results.items() if (tr or {}).get("status") == "completed"
    }
    pending = [tid for tid in task_order if tid in parsed and tid not in completed]

    # Budget capacity: ready tasks that no longer fit the ``max_tasks`` cap
    # are *deferred*, not failed — the next targeted-retry round re-enters
    # this node (after the budget gate has re-evaluated) and picks them up.
    max_tasks = _safe_int(budget.get("max_tasks"), 10)
    consumed_tasks = _safe_int(budget.get("consumed_tasks"), 0)
    tasks_this_node = 0  # includes earlier waves of this invocation
    deferred: list[str] = []

    documents_used = 0
    executed: list[str] = []
    waves: list[dict[str, Any]] = []
    while pending:
        ready: list[str] = []
        deferred = []
        for tid in pending:
            if not all(d in completed for d in parsed[tid].dependency):
                continue  # dependencies not finished yet — next wave
            if consumed_tasks + tasks_this_node + len(ready) >= max_tasks:
                deferred.append(tid)  # out of task budget — leave for the retry round
                continue
            ready.append(tid)
        if not ready:
            if deferred:
                # Everything runnable was deferred by the budget cap — stop
                # cleanly instead of overshooting it.
                waves.append({"ready": [], "deferred": list(deferred)})
                break
            # Remaining tasks can never run (a dependency failed or was
            # unparsable) — record the failure explicitly instead of a
            # silent skip.
            for tid in pending:
                task_results[tid] = {
                    "status": "failed",
                    "failure_reason": "UNMET_DEPENDENCIES",
                    "confidence": 0.0,
                    "evidence_count": 0,
                }
            waves.append({"ready": [], "failed": list(pending)})
            break

        # Wave view: workers see the live evidence mapping so dependent
        # tasks can mine wave-1 results for cross-references.
        wave_state = {**state, "evidence": evidence}
        if parallel and len(ready) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(ready))) as pool:
                outcomes = list(
                    pool.map(
                        lambda tid, ws=wave_state: (tid, *_run_task_retrieval(tid, parsed[tid], ws)),
                        ready,
                    )
                )
        else:
            outcomes = [(tid, *_run_task_retrieval(tid, parsed[tid], wave_state)) for tid in ready]

        for tid, chunks, summary in outcomes:
            evidence[tid] = chunks
            task_results[tid] = summary
            documents_used += len(chunks)
            executed.append(tid)
            if summary.get("status") == "completed":
                completed.add(tid)
        waves.append({"ready": ready, "statuses": {tid: task_results[tid].get("status") for tid in ready}})
        tasks_this_node += len(outcomes)
        pending = [tid for tid in pending if tid not in ready]

    completed_count = sum(1 for tr in task_results.values() if (tr or {}).get("status") == "completed")
    failed = sorted(tid for tid, tr in task_results.items() if (tr or {}).get("status") == "failed")
    # This round's deferrals: still pending, never executed, not failed —
    # includes tasks transitively waiting on a deferred dependency.
    deferred_ids = sorted(
        tid
        for tid in pending
        if tid not in executed and (task_results.get(tid) or {}).get("status") != "failed"
    )
    budget["consumed_tasks"] = consumed_tasks + len(executed)
    budget["consumed_documents"] = _safe_int(budget.get("consumed_documents"), 0) + documents_used
    budget["consumed_retrieval_rounds"] = _safe_int(budget.get("consumed_retrieval_rounds"), 0) + (
        1 if executed else 0
    )

    return {
        "evidence": evidence,
        "task_results": task_results,
        "tasks_completed": completed_count,
        "budget": budget,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "execute_task",
                "latency_ms": _ms(start),
                "detail": {
                    "tasks_completed": completed_count,
                    "executed": executed,
                    "failed": failed,
                    "deferred": deferred_ids,
                    "documents": documents_used,
                    "waves": waves,
                    "parallel": parallel,
                },
            },
        ],
    }


def evidence_sufficiency_node(state: dict[str, Any]) -> dict[str, Any]:
    """P2 (Phase 2): per-task 7-signal evidence sufficiency gate.

    Every task's evidence is scored against the rubric in
    :mod:`app.rag.agent.sufficiency` (coverage, relevance, authority,
    specificity, completeness, contradiction, temporal validity).  The
    aggregated verdicts drive routing:

    - sufficient (all gating signals pass on enough tasks) → ``synthesize``
    - otherwise → ``targeted_retry`` (rubric failures map 1:1 onto the
      FailureClassifier taxonomy for diagnosis)
    - budget exhausted at critically low coverage → ``abstain``

    Conflict/temporal/authority aggregates are written to state so
    ``targeted_retry_node`` can classify without re-scoring (item 16).
    """
    start = time.monotonic()
    from app.rag.agent.sufficiency import SufficiencyAssessor, aggregate_verdicts
    from app.rag.evidence_task import EvidenceTask

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

    # --- Phase 2: the 7-signal rubric, per task ------------------------
    assessor = SufficiencyAssessor()
    verdicts = []
    for task_id, raw in tasks.items():
        try:
            task = raw if isinstance(raw, EvidenceTask) else EvidenceTask.from_dict(raw)
        except (ValueError, TypeError) as exc:
            logger.warning("evidence_sufficiency_node: unparsable task %s (%s)", task_id, exc)
            continue
        verdicts.append(assessor.assess_task(task, evidence.get(task_id) or []))
    agg = aggregate_verdicts(verdicts)

    # Sufficient only when the rubric passes on enough tasks AND the
    # verification signals from any previous round are clean.
    sufficient = bool(agg["sufficient"]) and coverage >= 0.5 and citation_ok and not hallucinated
    # Abstain (the proposal's explicit path: "after the maximum retrieval
    # budget → ABSTAIN") when the retry budget is exhausted, the gate still
    # rejects the evidence, AND fewer than half the tasks found *any*
    # evidence — synthesizing from critically uncovered evidence would be
    # worse than abstaining, and retrying forever is not an option.  When
    # most tasks do have evidence, degrade gracefully instead: synthesize
    # from what is covered (the rubric failures stay on state for the
    # caller).  Also abstain when there is nothing to synthesize from at
    # all (no tasks, no evidence).
    abstain_required = (
        budget_exhausted and not sufficient and total_tasks > 0 and coverage < 0.5
    ) or (total_tasks == 0 and not evidence)

    # Aggregates for failure diagnosis (targeted_retry_node reads these).
    authority_values = [
        v["signals"]["authority"]["value"]
        for v in agg["verdicts"]
        if v.get("signals")
    ]
    authority_score = min(authority_values) if authority_values else 1.0
    temporal_conflict = any(
        not v["signals"]["temporal"]["passed"] for v in agg["verdicts"] if v.get("signals")
    )
    return {
        "evidence_coverage": coverage,
        "evidence_sufficient": sufficient,
        "budget_exhausted": budget_exhausted,
        "abstain_required": abstain_required,
        "task_sufficiency": agg["verdicts"],
        "has_conflicts": bool(agg["has_conflicts"]),
        "temporal_conflict": temporal_conflict,
        "authority_score": authority_score,
        "diagnosis_failures": agg["failure_codes"],
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
                    "task_failures": {
                        v["task_id"]: v["failures"] for v in agg["verdicts"] if v["failures"]
                    },
                    "has_conflicts": bool(agg["has_conflicts"]),
                    "authority_score": authority_score,
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
    from app.rag.agent.routing_economics import is_exhausted

    budget = dict(state.get("budget") or {})
    # Phase 3: the exhaustion predicate is shared with the linear retry
    # router so both paths enforce identical economics.
    exhausted = is_exhausted(budget)
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
    # Claim-level verification (item 15) against the merged DAG evidence.
    claim_report = _verify_claims(result.get("answer", ""), merged)
    # Consume the LLM-call budget counter (synthesis is one LLM call) so a
    # later budget gate sees real usage.
    budget = dict(state.get("budget") or {})
    budget["consumed_llm_calls"] = _safe_int(budget.get("consumed_llm_calls"), 0) + 1
    update: dict[str, Any] = {
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
                "detail": {
                    "merged_chunks": len(merged),
                    "groundedness": result.get("groundedness_score", 0.0),
                    "claims": len(claim_report["claims"]) if claim_report else 0,
                },
            },
        ],
    }
    if claim_report:
        update["claims"] = claim_report["claims"]
        update["claim_groundedness"] = claim_report["claim_groundedness"]
        update["unverified_claims"] = claim_report["unverified_claims"]
        # Persist the per-claim verdicts on the response payload too, so
        # callers get claim-level traceability without reading graph state.
        result.setdefault("claim_verification", {
            "claim_groundedness": claim_report["claim_groundedness"],
            "claims": claim_report["claims"],
            "unverified_claims": claim_report["unverified_claims"],
        })
    return update


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

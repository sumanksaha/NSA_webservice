"""Linear-path nodes — thin adapters over the existing RAG services (M3).

classify → plan → retrieve → generate → verify → citation_quality, plus the
retry nodes (expand_query, targeted_retry), finalize, and the auxiliary
reason / multi-hop / KG nodes.  Each node is a plain function
``(state) -> partial RAGState`` reusing the production pipeline entry
points, so the agent path and the legacy path share exactly the same
retrieval, reranking, KG-fusion, generation and verification code.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.rag.agent.nodes.claims import _verify_claims
from app.rag.agent.nodes.common import (
    _consume_budget,
    _enrich_audit_entry,
    _est_tokens,
    _ms,
    _query_for_retrieval,
    _safe_int,
)

logger = logging.getLogger(__name__)

__all__ = [
    "citation_quality_node",
    "classify_node",
    "evidence_node",
    "expand_query_node",
    "finalize_node",
    "generate_node",
    "kg_reason_node",
    "multi_hop_retrieve_node",
    "plan_node",
    "reason_node",
    "retrieve_node",
    "targeted_retry_node",
    "verify_node",
]


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
    # Phase 4 cost telemetry: context + completion tokens for this LLM call.
    token_cost = _est_tokens(
        state.get("query"),
        *(str(c.get("text") or "") for c in state.get("chunks") or [] if isinstance(c, dict)),
        result.get("answer", ""),
    )
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
            _enrich_audit_entry(
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
                token_cost=token_cost,
            ),
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
    lives in :mod:`app.rag.agent.thresholds`.
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
        # 2.11: KG signals set by kg_reason_node / verifier.
        "kg_traversal_failed": bool(state.get("kg_traversal_failed", False)),
        "kg_conflict_unresolved": bool(state.get("kg_conflict_unresolved", False)),
        "kg_lineage_gap": bool(state.get("kg_lineage_gap", False)),
        "kg_entity_unresolved": bool(state.get("kg_entity_unresolved", False)),
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
    deduped: list[str] = []
    for failure in failures:
        if failure not in seen:
            seen.add(failure)
            deduped.append(failure)
    failures = deduped
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
        # str/StrEnum equivalence: RetrievalFailure members ARE str, so mapping.get matches by value.
        failures=failures,  # type: ignore[arg-type]
        query_type=query_type,
        context={
            "collection_name": state.get("collection_name"),
            "kg_paths": state.get("kg_paths") or [],
            "kg_capability": state.get("kg_capability", "actionable_answer"),
        },
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


def kg_reason_node(state: dict[str, Any]) -> dict[str, Any]:
    """2.11 KG as Reasoning Engine — graph node.

    Runs KG traversal for the current query (when the active profile
    enables ``kg_reasoning``) and stores JSON-safe paths + Cypher on
    state for retrieval targeting and audit. Never raises: KG
    unavailable → empty paths with a ``skipped`` audit entry.

    Integrate into the graph between ``plan`` and ``retrieve`` (or
    before ``targeted_retry``) so ``targeted_query`` can reuse
    ``kg_paths``.
    """
    start = time.monotonic()
    query = state.get("query") or ""
    profile = state.get("query_profile") or "standard"
    try:
        from app.rag.planning.profiles import ProfileManager

        enabled = ProfileManager().get_query_profile(profile).kg_reasoning_enabled
    except ValueError:
        # Unknown profile name (likely a typo) — fail closed so a misspelled
        # profile does not silently enable an expensive path `fast` disables.
        logger.warning("kg_reason_node: unknown query_profile %r — KG disabled", profile)
        enabled = False
    except Exception:
        enabled = True
    if not enabled or not query:
        return {
            "kg_paths": [],
            "audit_trail": [
                *(state.get("audit_trail") or []),
                {"node": "kg_reason", "latency_ms": _ms(start), "detail": {"skipped": True}},
            ],
        }
    try:
        from app.rag.planning.kg_reasoner import generate_cypher, reason_from_query

        paths = reason_from_query(query)
        payload = [p.__dict__ for p in paths]
        return {
            "kg_paths": payload,
            "kg_cypher": generate_cypher("cross_reference", {"section": query[:120]}),
            "audit_trail": [
                *(state.get("audit_trail") or []),
                {"node": "kg_reason", "latency_ms": _ms(start), "detail": {"paths": len(payload)}},
            ],
        }
    except Exception as exc:
        logger.warning("kg_reason_node: KG traversal failed (%s)", exc)
        return {
            "kg_paths": [],
            "kg_traversal_failed": True,
            "audit_trail": [
                *(state.get("audit_trail") or []),
                {"node": "kg_reason", "latency_ms": _ms(start), "detail": {"error": str(exc)}},
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
    # FSO advisory (ADR-0003): additive fields only — never mutate answer /
    # citations / groundedness.  Present (dict or None) when the advisory
    # nodes ran; absent otherwise (flag off / legacy states).
    if state.get("fso_advisory_enabled") or state.get("fso_act") is not None:
        response["fso_act"] = state.get("fso_act")
    if state.get("advisory_abstain_reason"):
        response["advisory_abstain_reason"] = state["advisory_abstain_reason"]
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
        # Phase 3: per-requirement sufficiency — which answer requirements in
        # the AnswerRequirementGraph ended up with sufficient evidence.  Also
        # mirrors the serialized requirement graph for observability.
        if state.get("requirement_sufficiency"):
            response["agent"]["requirement_sufficiency"] = state["requirement_sufficiency"]
        qplan = state.get("query_plan") or {}
        if isinstance(qplan, dict) and qplan.get("requirement_graph"):
            response["agent"]["requirement_graph"] = qplan["requirement_graph"]
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
            # Phase 3: serialized AnswerRequirementGraph — requirements with
            # ids/types/answer contracts/dependencies, JSON-safe for the
            # checkpointer.  Sufficiency verdicts and the benchmark carry the
            # requirement_id forward from here.
            "requirement_graph": plan.requirement_graph.to_dict() if plan.requirement_graph is not None else None,
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
    # (Uses the extract_references function — the same seam the DAG path
    # mines via _cross_reference_queries.)
    if query_type in ("cross_reference", "case_law") and chunks:
        try:
            from app.rag.retrieval.reference_extractor import extract_references

            refs = []
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                text = str(chunk.get("text") or "")
                if text:
                    refs.extend(extract_references(text))
            # Build refined query from the first section-bearing
            # cross-reference (bare relation keywords carry no section).
            if refs:
                first_ref = next((r for r in refs if getattr(r, "section", "")), refs[0])
                section = getattr(first_ref, "section", "") or ""
                raw = getattr(first_ref, "raw", "") or ""
                refined = f"{query} AND section {section}" if section else f"{query} AND {raw}"
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

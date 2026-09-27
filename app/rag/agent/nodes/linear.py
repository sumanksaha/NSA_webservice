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

    Reads both views off the shared query-understanding seam: the legacy
    ``query_type`` (back-compat) plus the 14-type ``legal_type`` with its
    confidence (Step 0 seam for universal multihop — routers and the
    multihop node resolve the operative type via
    ``effective_query_type``). A failure degrades to ``"general"`` /
    ``"ambiguous"`` so the graph never stalls on classification.
    """
    start = time.monotonic()
    query = state.get("query") or ""
    query_type = "general"
    legal_type = "ambiguous"
    legal_confidence = 0.0
    detail: dict[str, Any] = {"fallback": False}
    try:
        from app.rag.retrieval import understand

        understood = understand(query)
        query_type = understood.query_type.value
        legal_type = understood.legal_type
        legal_confidence = float(understood.legal_confidence or 0.0)
    except Exception as exc:
        logger.warning("classify_node: classification failed (%s)", exc)
        detail = {"fallback": True, "error": str(exc)}
    return {
        "query_type": query_type,
        "legal_type": legal_type,
        "legal_confidence": legal_confidence,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "classify",
                "latency_ms": _ms(start),
                "detail": {
                    "query_type": query_type,
                    "legal_type": legal_type,
                    "legal_confidence": legal_confidence,
                    **detail,
                },
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

    retrieval_query = _query_for_retrieval(state)
    stashed = state.get("multi_hop_chunks") or []
    stash_update: dict[str, Any] = {}
    if "multi_hop_chunks" in state or "multi_hop_query" in state or "multi_hop_followup" in state:
        stash_update = {"multi_hop_chunks": [], "multi_hop_query": None, "multi_hop_followup": None}
    multi_hop_merged = 0
    multihop_reused = False
    if stashed and state.get("multi_hop_query") == retrieval_query and retrieval_query:
        # Part B reuse: the multi-hop node already retrieved this exact
        # query (pass 1 + follow-ups, merged) — skip the duplicate pipeline
        # call.  Freshness metadata was forwarded by the multi-hop node and
        # already sits on state, so it is kept as-is.  A changed query
        # (e.g. failure-aware targeted retry) falls through to fresh
        # retrieval below.
        chunks = list(stashed)
        multihop_reused = True
        # No budget charge: these exact chunks were already counted by the
        # multi-hop node (re-counting documents would exhaust tight caps a
        # retry early).
        query_type = state.get("query_type", "general")
        retrieval_latency_ms = state.get("retrieval_latency_ms", 0)
        log_id = state.get("log_id")
        evidence_set = state.get("evidence_set")
        budget = _consume_budget(state, retrieval_rounds=0, documents=0)
    else:
        result = run_retrieval_pipeline(
            query=retrieval_query,
            top_k=state.get("top_k", 10),
            collection_name=state.get("collection_name"),
            filters=state.get("filters"),
            pipeline="agent",
        )
        chunks = result.get("chunks", [])
        # Preserve second-pass evidence: fold unseen stashed chunks into the
        # fresh result (fresh results stay primary).
        if stashed and state.get("multi_hop_followup"):
            seen_ids = {c.get("chunk_id") for c in chunks if isinstance(c, dict) and c.get("chunk_id")}
            for c in stashed:
                if isinstance(c, dict) and (not c.get("chunk_id") or c.get("chunk_id") not in seen_ids):
                    chunks.append(c)
                    if c.get("chunk_id"):
                        seen_ids.add(c.get("chunk_id"))
                    multi_hop_merged += 1
        query_type = result.get("query_type") or state.get("query_type", "general")
        retrieval_latency_ms = result.get("retrieval_latency_ms", 0)
        log_id = result.get("log_id")
        # Evidence set is already computed by apply_stages inside
        # run_retrieval_pipeline — forward it to avoid recompute in the
        # evidence_node downstream.
        evidence_set = result.get("evidence_set")
        budget = _consume_budget(state, retrieval_rounds=1, documents=len(chunks))
    return {
        "chunks": chunks,
        "query_type": query_type,
        "retrieval_latency_ms": retrieval_latency_ms,
        "log_id": log_id,
        "evidence_set": evidence_set,
        "budget": budget,
        **stash_update,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "retrieve",
                "latency_ms": _ms(start),
                "detail": {
                    "chunk_count": len(chunks),
                    "retrieval_latency_ms": retrieval_latency_ms,
                    "log_id": log_id,
                    "multi_hop_merged": multi_hop_merged,
                    "multihop_reused": multihop_reused,
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

    query = _query_for_retrieval(state)
    # Phase 3: when the structured path ran, render the final answer from
    # the audited argument — same pipeline, only the prompt input differs
    # (roadmap §21 isolates the reasoning architecture, not the prompt).
    # Any well-formed argument flows through (including fallback skeletons —
    # their uncertainties qualify the answer); only a missing argument
    # keeps plain generation.
    argument = state.get("structured_argument")
    structured_used = isinstance(argument, dict) and bool(argument.get("issue"))
    if structured_used:
        import json as _json

        from app.rag.generation.reasoning_path import reasoning_user_content

        query = reasoning_user_content(query, _json.dumps(argument))
    result = run_generation_pipeline(
        query=query,
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
                        "structured_reasoning": structured_used,
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
        # Codes arrive as taxonomy values (classifier) or UPPERCASE rubric
        # names (sufficiency gate); the recovery seam normalizes both.
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


#: Confidence rank for multihop reference filtering (mirrors
#: ``reference_extractor`` levels so the ``MULTIHOP_CONFIDENCE_MIN`` string
#: resolves without importing the extractor at module load).
_MULTIHOP_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

#: Definition-signalling relations (LOW confidence by construction — no
#: section number).  These never become follow-up targets themselves; they
#: mark a chunk as definition-bearing for the definition branch.
_MULTIHOP_DEFINITION_RELATIONS = frozenset({"as_defined_in", "meaning_of", "interpretation_of"})


def _multihop_settings() -> dict[str, Any]:
    """Resolve multihop tunables via the shared config seam (Pattern A)."""
    try:
        from app.shared.config import cfg

        enabled = bool(cfg.multihop_enabled)
    except Exception:
        enabled = True
    try:
        from app.shared.config import cfg as _cfg

        confidence_min = str(_cfg.multihop_confidence_min or "MEDIUM").strip().upper()
    except Exception:
        confidence_min = "MEDIUM"
    if confidence_min not in _MULTIHOP_CONFIDENCE_RANK:
        confidence_min = "MEDIUM"
    try:
        from app.shared.config import cfg as _cfg2

        max_followups = max(0, int(_cfg2.multihop_max_followups))
    except (TypeError, ValueError):
        max_followups = 1
    except Exception:
        max_followups = 1
    try:
        from app.shared.config import cfg as _cfg3

        max_refs = max(1, int(_cfg3.multihop_max_refs))
    except (TypeError, ValueError):
        max_refs = 2
    except Exception:
        max_refs = 2
    return {
        "enabled": enabled,
        "confidence_min": confidence_min,
        "max_followups": max_followups,
        "max_refs": max_refs,
    }


def _plan_requirement_types(state: dict[str, Any]) -> set[str]:
    """Normalized evidence-requirement types from the query plan.

    Reads ``state["evidence_requirements"]`` (written by ``plan_node``) plus
    the serialized ``query_plan["tasks"]`` — both JSON-safe, no re-plan.
    """
    from app.rag.retrieval import normalize_query_type

    found: set[str] = set()
    for raw in state.get("evidence_requirements") or []:
        if raw:
            found.add(normalize_query_type(str(raw)))
    plan = state.get("query_plan") or {}
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    for task in tasks or []:
        if isinstance(task, dict) and task.get("evidence_requirement"):
            found.add(normalize_query_type(str(task["evidence_requirement"])))
    return found


def _mined_followup_targets(
    chunks: list[dict[str, Any]],
    query: str,
    min_rank: int,
    exclude_canons: set[str] | None = None,
) -> tuple[list[str], int, bool, int]:
    """Mine addressable follow-up targets from retrieved chunks.

    Returns ``(canons, refs_found, definition_relation_seen, covered_skipped)``
    where ``canons`` are deduplicated lowercase canonical refs
    (``"section 18"``, ``"Rule 2.3.1"``) for section/rule/schedule/chapter
    references at or above ``min_rank`` — excluding targets the query itself
    already cites, units already retrieved in these chunks (a follow-up
    re-fetching the same unit mostly replays merge-deduped chunk_ids), and
    any in ``exclude_canons`` (fetched by an earlier follow-up round).
    Definition-signalling relations (``as defined in``/``meaning of``
    without a section) are reported via ``definition_relation_seen``,
    never as targets.
    """
    from app.rag.retrieval.reference_extractor import extract_references

    excluded = exclude_canons or set()
    refs_found = 0
    covered_skipped = 0
    definition_relation_seen = False
    seen_canons: set[str] = set()
    ranked: list[tuple[int, int, str]] = []  # (-rank, span, canon)

    # Sections/rules/schedules the query already cites need no follow-up.
    query_cited: set[tuple[str | None, ...]] = set()
    try:
        for qr in extract_references(query or ""):
            query_cited.add((qr.section, qr.rule, qr.schedule, qr.chapter))
    except Exception:
        query_cited = set()

    # Units already retrieved in these chunks (Step-5 finding: self-mentions
    # like "Section 31" inside Section-31 chunks fired no-op rounds).
    covered: set[tuple[str | None, ...]] = set()
    try:
        from app.rag.retrieval.legal_identity import parse_legal_identity

        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            ident = parse_legal_identity(chunk)
            if ident.section or ident.rule or ident.schedule or ident.chapter:
                covered.add((ident.section, ident.rule, ident.schedule, ident.chapter))
    except Exception as exc:
        logger.warning("multi_hop: covered-unit read failed (%s)", exc)
        covered = set()

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text") or "")
        if not text:
            continue
        try:
            chunk_refs = extract_references(text)
        except Exception:
            continue
        for ref in chunk_refs:
            refs_found += 1
            if (ref.relation or "") in _MULTIHOP_DEFINITION_RELATIONS:
                definition_relation_seen = True
            rank = _MULTIHOP_CONFIDENCE_RANK.get(ref.confidence, 0)
            if rank < min_rank:
                continue
            if not (ref.section or ref.rule or ref.schedule or ref.chapter):
                continue
            if (ref.section, ref.rule, ref.schedule, ref.chapter) in query_cited:
                continue
            if (ref.section, ref.rule, ref.schedule, ref.chapter) in covered:
                covered_skipped += 1
                continue
            canon = ref.canonical_ref()
            if not canon:
                continue
            # Lowercase the leading keyword ("Section 18" -> "section 18")
            # to preserve the historical follow-up query shape.
            canon = canon[0].lower() + canon[1:]
            if canon in seen_canons or canon in excluded:
                continue
            seen_canons.add(canon)
            ranked.append((-rank, ref.span_start, canon))

    ranked.sort()
    return [canon for _, _, canon in ranked], refs_found, definition_relation_seen, covered_skipped


def _build_followup_query(query: str, canons: list[str], *, definition_flavor: bool) -> str:
    """Build the follow-up query from fused canonical refs.

    Definition-flavored follow-ups carry a ``definition`` cue so the
    lexical arm prefers defining provisions over merely mentioning ones.
    """
    clause = " AND ".join(canons)
    if definition_flavor:
        return f"{query} definition AND {clause}"
    return f"{query} AND {clause}"


def multi_hop_retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """Targeted retrieval using mined cross-references — universal (Part B).

    Pass 1 runs standard retrieval for every query type.  Follow-up rounds
    (up to ``MULTIHOP_MAX_FOLLOWUPS``) fire only when the evidence warrants
    them (universal-conditional): mined section/rule/schedule/chapter
    references at or above ``MULTIHOP_CONFIDENCE_MIN`` that neither the
    query nor an earlier round already covered — so reference chains
    (Rule → authorizing section → penalty) resolve iteratively.  The
    operative type resolves via ``effective_query_type`` (Step 0 seam);
    the plan's requirement types and mined definition relations pick the
    follow-up builder (definition-flavored when a DEFINITION requirement
    is present or definition-bearing relations were mined).

    Pass-1 chunks are always stashed on ``multi_hop_chunks`` with the
    pass-1 query on ``multi_hop_query`` so the downstream ``retrieve``
    node reuses them instead of re-running the identical query (Finding 1).
    The fired query (if any) rides on ``multi_hop_followup``; both stash
    keys are cleared by ``retrieve_node`` after use.
    """
    start = time.monotonic()
    from app.rag.tasks import run_retrieval_pipeline

    reasoning = state.get("reasoning", "")
    query = state.get("expanded_query") or state.get("query", "")
    query_type = state.get("query_type", "general")

    # First pass — standard retrieval.
    result = run_retrieval_pipeline(
        query=query,
        top_k=state.get("top_k", 10),
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
        pipeline="agent",
    )
    chunks = result.get("chunks", [])
    last_result = result
    budget = _consume_budget(state, retrieval_rounds=1, documents=len(chunks))

    # Operative type + requirement signals (Steps 0/A seams — pure reads).
    from app.rag.retrieval import effective_query_type

    legal_type = state.get("legal_type", "")
    effective_type = effective_query_type(query_type, legal_type)
    try:
        req_types = _plan_requirement_types(state)
    except Exception as exc:
        logger.warning("multi_hop_retrieve_node: requirement read failed (%s)", exc)
        req_types = set()

    settings = _multihop_settings()
    fired = False
    followups_fired = 0
    followup: str | None = None
    refs_found = 0
    covered_skipped = 0
    refs_used: list[str] = []
    merged_new = 0
    builder = "none"
    reason = "no_refs"
    fetched_canons: set[str] = set()
    definition_relation_seen = False

    def _mine() -> list[str]:
        """Mine one batch of unfetched targets (never raises)."""
        nonlocal refs_found, definition_relation_seen, covered_skipped
        try:
            min_rank = _MULTIHOP_CONFIDENCE_RANK[settings["confidence_min"]]
            canons, n_found, def_rel, n_covered = _mined_followup_targets(
                chunks, query, min_rank, exclude_canons=fetched_canons
            )
        except Exception as exc:
            logger.warning("multi_hop_retrieve_node: cross-ref extraction failed (%s)", exc)
            return []
        refs_found += n_found
        definition_relation_seen = definition_relation_seen or def_rel
        covered_skipped += n_covered
        return canons

    if not settings["enabled"]:
        reason = "disabled"
    else:
        from app.rag.agent.routing_economics import is_exhausted

        if is_exhausted(budget, include_tasks=False):
            reason = "budget_exhausted"
        elif not chunks:
            reason = "no_chunks"
        elif settings["max_followups"] >= 1:
            while followups_fired < settings["max_followups"]:
                if is_exhausted(budget, include_tasks=False):
                    reason = "budget_exhausted" if not fired else "fired"
                    break
                batch = _mine()
                if not batch:
                    reason = "fired" if fired else "no_refs"
                    break
                batch = batch[: settings["max_refs"]]
                definition_flavor = (
                    effective_type == "definition" or "definition" in req_types or definition_relation_seen
                )
                builder = "definition" if definition_flavor else "section"
                followup = _build_followup_query(query, batch, definition_flavor=definition_flavor)
                refs_used.extend(batch)
                fetched_canons.update(batch)
                followup_result = run_retrieval_pipeline(
                    query=followup,
                    top_k=state.get("top_k", 10),
                    collection_name=state.get("collection_name"),
                    filters=state.get("filters"),
                    pipeline="agent",
                )
                last_result = followup_result
                chunks2 = followup_result.get("chunks", [])
                budget = _consume_budget(
                    {**state, "budget": budget},
                    retrieval_rounds=1,
                    documents=len(chunks2),
                )
                # Merge — follow-up chunks that don't duplicate known
                # chunk_ids, preserving RRF score order (id-less chunks
                # never count as duplicates).
                seen = {c.get("chunk_id") for c in chunks if isinstance(c, dict) and c.get("chunk_id")}
                for c in chunks2:
                    if isinstance(c, dict) and (not c.get("chunk_id") or c.get("chunk_id") not in seen):
                        chunks.append(c)
                        if c.get("chunk_id"):
                            seen.add(c.get("chunk_id"))
                        merged_new += 1
                fired = True
                followups_fired += 1
                reason = "fired"
        elif _mine():
            # max_followups == 0 with actionable refs: capped, single pass.
            reason = "followups_capped"

    return {
        "chunks": chunks,
        # Stash for retrieve_node reuse (cleared there after use).
        "multi_hop_chunks": list(chunks),
        "multi_hop_query": query,
        "multi_hop_followup": followup,
        # Freshness metadata for the reuse path (last result wins).
        "evidence_set": last_result.get("evidence_set"),
        "log_id": last_result.get("log_id"),
        "retrieval_latency_ms": last_result.get("retrieval_latency_ms", 0),
        "budget": budget,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "multi_hop_retrieve",
                "latency_ms": _ms(start),
                "detail": {
                    "refined": bool(reasoning),
                    "query_type": query_type,
                    "legal_type": legal_type,
                    "effective_type": effective_type,
                    "requirement_types": sorted(req_types),
                    "multi_hop": fired,
                    "followups_fired": followups_fired,
                    "refs_found": refs_found,
                    "covered_skipped": covered_skipped,
                    "refs_used": refs_used,
                    "followup_query": followup,
                    "fired_reason": reason,
                    "merged_new": merged_new,
                    "builder": builder,
                },
            },
        ],
    }

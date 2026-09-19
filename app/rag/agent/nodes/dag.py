"""DAG-path nodes — EvidenceTask planning, wave execution, gating (P1–P3).

plan_tasks → budget_gate → execute_task → evidence_sufficiency → synthesize,
with abstain as the explicit give-up path.  These nodes own the scheduling
and sufficiency policy; the actual retrieval/generation calls stay thin
delegations to the production pipeline entry points.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.rag.agent.nodes.claims import _verify_claims
from app.rag.agent.nodes.common import (
    _COVERAGE_FLOOR,
    _caller_app,
    _enrich_audit_entry,
    _est_tokens,
    _ms,
    _safe_int,
    _task_token_cost,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_CROSS_REF_LIMIT",
    "_cross_reference_queries",
    "_dependency_chunks",
    "_run_task_retrieval",
    "abstain_node",
    "budget_gate_node",
    "evidence_sufficiency_node",
    "execute_task_node",
    "plan_tasks_node",
    "synthesize_node",
]


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
        # Phase 2 per-task sufficiency rubric refines this with real
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
    TASK_PARALLELISM`` / ``cfg.task_parallelism`` disables it).  Each worker
    runs inside its own Flask app context (propagated from the calling
    thread via :func:`app.rag.agent.nodes.common._caller_app`) so
    ``current_app``-bound retrieval behaves identically on and off the DAG
    path.  The next wave starts only when every dependency it waits on has
    finished, so dependent tasks see their upstream evidence in
    ``state["evidence"]`` and cross-reference expansion can mine it.

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
    completed = {tid for tid, tr in task_results.items() if (tr or {}).get("status") == "completed"}
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
        caller_app = _caller_app()

        def _run_one(tid: str, ws: dict[str, Any]) -> tuple[str, list, dict[str, Any]]:
            # Fresh app context per worker: the executor threads start
            # context-free, and retrieval reads Pattern-A config / db
            # through ``current_app``.
            if caller_app is not None:
                with caller_app.app_context():
                    return (tid, *_run_task_retrieval(tid, parsed[tid], ws))
            return (tid, *_run_task_retrieval(tid, parsed[tid], ws))

        if parallel and len(ready) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(ready))) as pool:
                outcomes = list(pool.map(lambda tid, ws=wave_state: _run_one(tid, ws), ready))
        else:
            outcomes = [_run_one(tid, wave_state) for tid in ready]

        for tid, chunks, summary in outcomes:
            # Phase 4 telemetry: per-task token cost on every result
            # (latency_ms is stamped per-task inside _run_task_retrieval).
            summary["token_cost"] = _task_token_cost(parsed[tid], chunks)
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
        tid for tid in pending if tid not in executed and (task_results.get(tid) or {}).get("status") != "failed"
    )
    budget["consumed_tasks"] = consumed_tasks + len(executed)
    budget["consumed_documents"] = _safe_int(budget.get("consumed_documents"), 0) + documents_used
    budget["consumed_retrieval_rounds"] = _safe_int(budget.get("consumed_retrieval_rounds"), 0) + (1 if executed else 0)

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
    sufficient = bool(agg["sufficient"]) and coverage >= _COVERAGE_FLOOR and citation_ok and not hallucinated
    # Abstain (the proposal's explicit path: "after the maximum retrieval
    # budget → ABSTAIN") when the retry budget is exhausted, the gate still
    # rejects the evidence, AND fewer than half the tasks found *any*
    # evidence — synthesizing from critically uncovered evidence would be
    # worse than abstaining, and retrying forever is not an option.  When
    # most tasks do have evidence, degrade gracefully instead: synthesize
    # from what is covered (the rubric failures stay on state for the
    # caller).  Also abstain when there is nothing to synthesize from at
    # all (no tasks, no evidence).
    abstain_required = (budget_exhausted and not sufficient and total_tasks > 0 and coverage < _COVERAGE_FLOOR) or (
        total_tasks == 0 and not evidence
    )

    # Aggregates for failure diagnosis (targeted_retry_node reads these).
    authority_values = [v["signals"]["authority"]["value"] for v in agg["verdicts"] if v.get("signals")]
    authority_score = min(authority_values) if authority_values else 1.0
    temporal_conflict = any(not v["signals"]["temporal"]["passed"] for v in agg["verdicts"] if v.get("signals"))

    # Phase 3: fold per-task verdicts into per-requirement sufficiency so the
    # benchmark's evidence_completeness (EC) metric has real-run data keyed by
    # AnswerRequirementGraph ids.  Conservative AND semantics: a requirement is
    # sufficient only when every task serving it passed the rubric (multiple
    # tasks per requirement is rare — the planner dedupes — so this mostly
    # degenerates to the single task's verdict).
    requirement_sufficiency: dict[str, bool] = {}
    for v in agg["verdicts"]:
        rid = v.get("requirement_id")
        if not rid:
            continue
        requirement_sufficiency[rid] = requirement_sufficiency.get(rid, True) and bool(v["sufficient"])
    return {
        "requirement_sufficiency": requirement_sufficiency,
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
                    "task_failures": {v["task_id"]: v["failures"] for v in agg["verdicts"] if v["failures"]},
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
    """Synthesize the final answer from merged DAG evidence (P1)."""
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
    # Signals are derived per claim from the evidence chunks themselves
    # (authority / contradiction pairs / repeal flags) inside _verify_claims.
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
            _enrich_audit_entry(
                {
                    "node": "synthesize",
                    "latency_ms": _ms(start),
                    "detail": {
                        "merged_chunks": len(merged),
                        "groundedness": result.get("groundedness_score", 0.0),
                        "claims": len(claim_report["claims"]) if claim_report else 0,
                    },
                },
                token_cost=_est_tokens(
                    state.get("query"),
                    *(str(c.get("text") or "") for c in merged if isinstance(c, dict)),
                    result.get("answer", ""),
                ),
            ),
        ],
    }
    if claim_report:
        update["claims"] = claim_report["claims"]
        update["claim_groundedness"] = claim_report["claim_groundedness"]
        update["unverified_claims"] = claim_report["unverified_claims"]
        # Persist the per-claim verdicts on the response payload too, so
        # callers get claim-level traceability without reading graph state.
        result.setdefault(
            "claim_verification",
            {
                "claim_groundedness": claim_report["claim_groundedness"],
                "claims": claim_report["claims"],
                "unverified_claims": claim_report["unverified_claims"],
            },
        )
    return update

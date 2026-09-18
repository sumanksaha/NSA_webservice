"""Shared state schema for the LangGraph agent pipeline (M3).

``RAGState`` is a :class:`typing.TypedDict` describing everything the
graph nodes read and write.  It mirrors the fields of the legacy
``run_generation_pipeline`` result dict so the agent endpoint can
return the same ``RAGResponse``-schema shape to callers.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AuditEntry(TypedDict, total=False):
    """One step in the agent's execution trail."""

    node: str
    latency_ms: int
    detail: dict[str, Any]


class RAGState(TypedDict, total=False):
    """State flowing through the LangGraph agent pipeline.

    All keys optional (``total=False``) so each node returns only the
    slice it updates and the graph compiler can merge partial updates.
    """

    # --- Input ---
    query: str
    top_k: int
    collection_name: str | None
    filters: dict[str, Any] | None

    # --- Classify ---
    query_type: str

    # --- Plan (Phase 2.1 / Phase 0) ---
    # Serialized plan from plan_node: intent, complexity, and EvidenceTask
    # dicts.  ``complexity`` drives the post-plan router (linear vs DAG).
    query_plan: dict[str, Any] | None
    subquestions: list[str]
    evidence_requirements: list[str]
    dag_valid: bool

    # --- Retrieve ---
    # List of chunk dicts (``RetrievedChunk.to_dict()`` shape) — kept as
    # plain dicts so the state stays JSON-serializable (M5 checkpointing).
    chunks: list[dict[str, Any]]
    retrieval_latency_ms: int
    log_id: str | None
    # Evidence set forwarded from retrieve_node (computed by apply_stages
    # inside run_retrieval_pipeline).  Avoids a redundant select_evidence_set
    # call in evidence_node.
    evidence_set: dict[str, Any] | None
    # --- P1 EvidenceTask DAG ---
    evidence: dict[str, list]  # task_id -> list of evidence chunks
    claims: list[dict]  # list of claim dicts
    final_answer: str | None  # final synthesized answer
    quality: dict  # quality metrics
    plan: Any | None  # structured plan (from QueryPlanner)
    # Evidence Tasks from the QueryPlanner (Phase 2+).  When present,
    # the retrieval pipeline builds a per-task retrieval plan.
    evidence_tasks: Any | None
    # DAG execution state (Phase 0).  ``tasks`` carries serialized
    # EvidenceTask dicts (JSON-safe for checkpointing); ``task_order`` is
    # the topological execution order; ``tasks_completed`` is an audit
    # mirror of how many tasks produced evidence.
    tasks: dict[str, dict[str, Any]]
    task_order: list[str]
    tasks_completed: int
    # Per-task execution records (Phase 1): task_id -> {status, confidence,
    # citations, failure_reason, ...}.  The sufficiency gate and failure
    # diagnosis read these instead of inferring from chunk presence.
    task_results: dict[str, dict[str, Any]]
    # Per-task 7-signal sufficiency verdicts (Phase 2, item 14):
    # list of TaskSufficiency.to_dict() — signals/failures/conflicts per task.
    task_sufficiency: list[dict[str, Any]]
    # Per-requirement sufficiency (Phase 3): requirement_id → all tasks serving
    # that requirement passed the rubric.  Keyed by AnswerRequirementGraph ids
    # so the benchmark's evidence_completeness (EC) metric has real-run data.
    requirement_sufficiency: dict[str, bool]
    # Live verification signals (Phase 2, item 16) aggregated by the gate:
    # has_conflicts — pairwise evidence contradictions found;
    # temporal_conflict — superseded/effective-date conflicts found;
    # authority_score — min per-task authority weight (item 17);
    # diagnosis_failures — rubric failures as FailureClassifier taxonomy codes.
    has_conflicts: bool
    temporal_conflict: bool
    authority_score: float
    diagnosis_failures: list[str]
    # Sufficiency / budget / routing signals shared between the DAG nodes
    # and the conditional edges (kept on state so routers stay pure reads).
    targeted_query: str | None  # failure-aware retry query (P2.6)
    budget_exhausted: bool
    evidence_coverage: float
    evidence_sufficient: bool
    abstain_required: bool
    abstained: bool

    # --- Generate / verify ---
    answer: str
    groundedness: float
    hallucination_detected: bool
    response: dict[str, Any]
    # Claim-level verification (Phase 2, item 15): per-claim entailment
    # verdicts for the generated answer + the share of verified claims.
    claims: list[dict[str, Any]]
    claim_groundedness: float
    unverified_claims: list[str]

    # --- Retry loop ---
    retry_count: int
    expanded_query: str | None
    max_retries: int

    # --- M5 human-in-the-loop (review node) ---
    approved: bool

    # --- Budget controller (P3) ---
    budget: dict[str, Any]  # max_tasks, max_retrieval_rounds, max_documents, max_llm_calls, consumed counters

    # --- Phase 3: budget-aware routing economics ---
    # Decision made in plan_node (route_strategy): strategy, complexity,
    # query_type, budget tier, pinned. _route_after_plan only translates
    # this into a node name; kept for telemetry on the response payload.
    routing_decision: dict[str, Any] | None

    # --- Audit ---
    audit_trail: list[AuditEntry]

    # --- Citation quality gate (multi-signal threshold, 2026-08-26) ---
    # citation_quality_ok is False when the answer cites a chunk_id that was
    # NOT in the retrieved set (hallucinated citation).  ``missing_citations``
    # holds those unretrieved chunk_ids.  ``route_after_verify`` combines this
    # with groundedness + hallucination_detected to decide retry vs finalize.
    citation_quality_ok: bool
    missing_citations: list[str]


def initial_state(
    query: str,
    *,
    top_k: int = 10,
    collection_name: str | None = None,
    filters: dict[str, Any] | None = None,
    max_retries: int = 2,
) -> RAGState:
    """Build the initial ``RAGState`` for a query.

    ``max_retries`` is fixed at 2 (matching the plan's ``retry_count < 2``
    guard) unless overridden — kept on the state so tests can probe the
    conditional edge cheaply.
    """
    return {
        "query": query,
        "top_k": top_k,
        "collection_name": collection_name,
        "filters": filters,
        "query_type": "",
        "query_plan": None,
        "subquestions": [],
        "evidence_requirements": [],
        "dag_valid": True,
        "chunks": [],
        "retrieval_latency_ms": 0,
        "log_id": None,
        "evidence_tasks": None,
        "evidence_set": None,
        "evidence": {},
        "claims": [],
        "final_answer": None,
        "quality": {},
        "plan": None,
        "tasks": {},
        "task_order": [],
        "tasks_completed": 0,
        "task_results": {},
        "task_sufficiency": [],
        "requirement_sufficiency": {},
        "has_conflicts": False,
        "temporal_conflict": False,
        "authority_score": 1.0,
        "diagnosis_failures": [],
        "targeted_query": None,
        "budget_exhausted": False,
        "evidence_coverage": 0.0,
        "evidence_sufficient": False,
        "abstain_required": False,
        "abstained": False,
        "answer": "",
        "groundedness": 0.0,
        "hallucination_detected": False,
        "response": {},
        "claim_groundedness": 0.0,
        "unverified_claims": [],
        "retry_count": 0,
        "expanded_query": None,
        "max_retries": max_retries,
        "budget": {
            "max_tasks": 10,
            "max_retrieval_rounds": 5,
            "max_documents": 50,
            "max_llm_calls": 20,
            "consumed_tasks": 0,
            "consumed_retrieval_rounds": 0,
            "consumed_documents": 0,
            "consumed_llm_calls": 0,
        },
        "routing_decision": None,
        "audit_trail": [],
        "citation_quality_ok": True,
        "missing_citations": [],
    }

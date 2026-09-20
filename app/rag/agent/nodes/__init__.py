"""Graph nodes — thin adapters over the existing RAG services (M3).

Each node is a plain function ``(state: dict[str, Any]) -> partial RAGState``.
They reuse the production pipeline entry points (``run_retrieval_pipeline``
/ ``run_generation_pipeline``) so the agent path and the legacy path share
exactly the same retrieval, reranking, KG-fusion, generation and
verification code — the graph only adds orchestration around them.

Imports inside the functions keep the agent package lazy: the legacy
pipeline never imports LangGraph or this package.

Layout (split from the former 1.4k-line ``nodes.py``):

* :mod:`app.rag.agent.nodes.common` — timing, state accessors, token
  telemetry, budget accounting, app-context capture (no node logic).
* :mod:`app.rag.agent.nodes.claims` — claim-level verification (item 15).
* :mod:`app.rag.agent.nodes.linear` — linear-path adapters
  (classify → plan → retrieve → generate → verify → retry → finalize).
* :mod:`app.rag.agent.nodes.dag` — DAG-path scheduling and gating
  (plan_tasks → budget_gate → execute_task → evidence_sufficiency →
  synthesize → abstain).

This module re-exports the full historic surface so ``nodes.<name>``
attribute access and ``from app.rag.agent.nodes import <name>`` keep
working unchanged.
"""

from __future__ import annotations

from app.rag.agent.nodes.advisory import (
    extract_sections,
    fso_advisory_node,
)
from app.rag.agent.nodes.auditor import AuditDefect, AuditResult, AuditResultDict, audit_argument, auditor_node
from app.rag.agent.nodes.claims import _verify_claims
from app.rag.agent.nodes.common import (
    _BUDGET_DEFAULTS,
    GROUNDEDNESS_THRESHOLD,
    _caller_app,
    _consume_budget,
    _enrich_audit_entry,
    _est_tokens,
    _ms,
    _query_for_retrieval,
    _safe_int,
    _task_token_cost,
    logger,
)
from app.rag.agent.nodes.dag import (
    _CROSS_REF_LIMIT,
    _cross_reference_queries,
    _dependency_chunks,
    _run_task_retrieval,
    abstain_node,
    budget_gate_node,
    evidence_sufficiency_node,
    execute_task_node,
    plan_tasks_node,
    synthesize_node,
)
from app.rag.agent.nodes.linear import (
    citation_quality_node,
    classify_node,
    evidence_node,
    expand_query_node,
    finalize_node,
    generate_node,
    kg_reason_node,
    multi_hop_retrieve_node,
    plan_node,
    reason_node,
    retrieve_node,
    targeted_retry_node,
    verify_node,
)
from app.rag.agent.nodes.reasoning import structured_reasoner_node

__all__ = [
    "GROUNDEDNESS_THRESHOLD",
    "_BUDGET_DEFAULTS",
    "_CROSS_REF_LIMIT",
    "AuditDefect",
    "AuditResult",
    "AuditResultDict",
    "_caller_app",
    "_consume_budget",
    "_cross_reference_queries",
    "_dependency_chunks",
    "_enrich_audit_entry",
    "_est_tokens",
    "_ms",
    "_query_for_retrieval",
    "_run_task_retrieval",
    "_safe_int",
    "_task_token_cost",
    "_verify_claims",
    "abstain_node",
    "audit_argument",
    "auditor_node",
    "budget_gate_node",
    "citation_quality_node",
    "classify_node",
    "evidence_node",
    "evidence_sufficiency_node",
    "execute_task_node",
    "expand_query_node",
    "extract_sections",
    "finalize_node",
    "fso_advisory_node",
    "generate_node",
    "kg_reason_node",
    "logger",
    "multi_hop_retrieve_node",
    "plan_node",
    "plan_tasks_node",
    "reason_node",
    "retrieve_node",
    "structured_reasoner_node",
    "synthesize_node",
    "targeted_retry_node",
    "verify_node",
]

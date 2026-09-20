"""LangGraph agent graph (M3 + M4 + M5).

The graph orchestrates the existing RAG services into a self-correcting
pipeline (Phase 0: exactly one path per query — the plan router picks)::

    classify ──► plan ──┬─ SIMPLE ─────────► retrieve ──► generate ──► verify ──► citation_quality
                        │                    ▲                            │                │   ──► finalize
                        │                    └──── expand_query / targeted_retry ◄────────────┘
                        ├─ cross_ref/case ─► multi_hop_retrieve ──► retrieve ──► …
                        └─ MULTI_PART/HOP ─► plan_tasks ──► budget_gate ──► execute_task
                                              ──► evidence_sufficiency ──► synthesize ──► verify
                                                   │                (abstain / targeted_retry)

M5 (checkpointing + human-in-the-loop):

* ``build_graph(hitl=True)`` inserts a ``review`` node between ``verify``
  and the conditional edge.  ``review`` calls :func:`langgraph.types.interrupt`
  so the graph pauses for a human decision; a resumed ``approved`` value
  routes to ``finalize``, a rejection routes back to ``expand_query``
  (re-generate with a rewritten query).
* ``run_agent(..., thread_id=...)`` invokes with a checkpointer so the
  paused graph state can be resumed by thread id.
* Checkpointer selection: ``RAG_AGENT_CHECKPOINTER`` = ``memory`` (default,
  ``MemorySaver`` — dev/tests, no DB) or ``postgres`` (``PostgresSaver`` —
  prod; requires ``langgraph-checkpoint-postgres`` + psycopg).

All nodes are synchronous (aligns with Flask + Celery).  ``langgraph`` is
imported **only here**, lazily — the legacy pipeline and the rest of the
app never import it (plan §5.1).
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from collections.abc import Callable
from typing import Any  # ponytail: TypedDict unused — precise per-node types overkill for single-impl graph

from app.rag.agent.state import RAGState

# Canonical routing thresholds (single tuning point: thresholds.py).
# Imported under their historic names so tests and routers keep working.
from app.rag.agent.thresholds import (
    CLAIM_GROUNDEDNESS_RETRY_BELOW as CLAIM_GROUNDEDNESS_THRESHOLD,
)
from app.rag.agent.thresholds import (
    GROUNDEDNESS_RETRY_BELOW as GROUNDEDNESS_THRESHOLD,
)
from app.shared.config import cfg

logger = logging.getLogger(__name__)


def route_after_verify(state: RAGState) -> str:
    """Conditional edge: retry (expand / targeted) or finalize.

    Multi-signal threshold (matches the ``citation_quality`` gate's design
    note in state.py):

    - retry budget exhausted → finalize (no infinite loops)
    - low groundedness → expand_query (query-rewriting retry)
    - grounded but citing unretrieved chunks / hallucination flagged →
      targeted_retry (failure-aware retrieval targeting)
    - otherwise → finalize

    Phase 0 fix: the citation-quality signal was computed but never
    consulted — a grounded answer with hallucinated citations finalized
    silently.
    """
    groundedness = float(state.get("groundedness", 0.0))
    retry_count = int(state.get("retry_count", 0))
    max_retries = int(state.get("max_retries", 2))
    if retry_count >= max_retries:
        return "finalize"
    # Phase 3 (item 18): the retrieval-round cap is enforced on the linear
    # path too — each retrieve/generate cycle consumed a round, and once
    # the tier's budget is spent we finalize with what we have instead of
    # retrying past the cap.
    from app.rag.agent.routing_economics import is_exhausted

    # The linear path cannot spend task slots, so a zero task cap (direct
    # tier) must not read as spent here.
    if is_exhausted(state.get("budget"), include_tasks=False):
        return "finalize"
    # Phase 2 (item 15): a claim-verification gate — an answer whose factual
    # claims mostly failed entailment against the evidence is retried with a
    # targeted retrieval before a low-groundedness rewrite is attempted.
    claim_groundedness = state.get("claim_groundedness")
    if (
        claim_groundedness is not None
        and float(claim_groundedness) < CLAIM_GROUNDEDNESS_THRESHOLD
        and state.get("unverified_claims")
    ):
        return "targeted_retry"
    if groundedness < GROUNDEDNESS_THRESHOLD:
        return "expand_query"
    if not state.get("citation_quality_ok", True) or state.get("hallucination_detected", False):
        return "targeted_retry"
    return "finalize"


def route_after_review(state: RAGState) -> str:
    """Conditional edge from the M5 review node.

    ``approved`` (set by the human resume value) → finalize; otherwise
    re-generate via the expand-and-retry loop.
    """
    if state.get("approved"):
        return "finalize"
    return "expand_query"


def route_after_audit(state: RAGState) -> str:
    """Conditional edge after the legal auditor (roadmap §32.3).

    FAIL with revision budget left → back to ``structured_reasoner`` for
    one capped correction pass; otherwise (PASS, exhausted budget, or no
    audit at all) → ``generate``.  The policy lives in
    ``reasoning_path.should_revise`` — this router only translates it.
    """
    from app.rag.generation.reasoning_path import should_revise

    if should_revise(
        state.get("audit_result"),
        state.get("revision_count", 0),
        state.get("max_revisions", 1),
    ):
        return "structured_reasoner"
    return "generate"


def _route_after_evidence(state: RAGState) -> str:
    """P2: Route after the evidence_sufficiency gate.

    Phase 0: the gate runs **before** synthesis (per the V2 proposal —
    don't generate from unchecked evidence), so:

    - sufficient → synthesize (build the answer from DAG evidence)
    - budget exhausted / critically low coverage → abstain (give up)
    - otherwise → targeted_retry (attempt recovery)
    """
    if state.get("abstain_required"):
        return "abstain"
    if state.get("evidence_sufficient"):
        return "synthesize"
    return "targeted_retry"


def _route_after_budget(state: RAGState) -> str:
    """P3: Route after budget gate.

    - budget exhausted → abstain
    - budget OK → execute_task
    """
    if state.get("budget_exhausted"):
        return "abstain"
    return "execute_task"


def _route_after_retry(state: RAGState) -> str:
    """Phase 1: return targeted retries to the path that needed them.

    ``targeted_retry`` is shared by the linear and DAG paths.  On the DAG
    path an insufficiency verdict must loop back to DAG execution —
    ``plan_tasks`` is re-entered (it is a no-op re-read when the plan is
    unchanged), and ``execute_task`` runs only tasks not already marked
    ``completed`` in ``task_results`` — instead of dead-ending into the
    linear ``retrieve → generate`` path where DAG evidence is never
    synthesized.
    """
    if state.get("task_order"):
        return "plan_tasks"
    return "retrieve"


def _route_after_plan(state: RAGState) -> str:
    """Phase 0: exactly one path per query, chosen from the plan.

    Phase 3 (item 18): the decision itself is made *before* routing, in
    ``plan_node`` → :func:`routing_economics.route_strategy` — budget-aware
    (DIRECT override for single-identifier lookups, retry-pinned) and
    persisted on state as ``routing_decision`` for telemetry.  This edge
    only translates the persisted decision into a node name, with the
    Phase 0 rule as a fallback for states that never ran the planner.

    - ``decomposition`` → the EvidenceTask DAG path.
    - ``multi_hop`` → multi-hop retrieval.
    - ``direct`` → the plain linear path.
    """
    decision = state.get("routing_decision")
    if isinstance(decision, dict) and decision.get("strategy"):
        strategy = str(decision["strategy"])
        if strategy == "decomposition":
            return "plan_tasks"
        if strategy == "multi_hop":
            return "multi_hop_retrieve"
        return "retrieve"
    # Fallback: legacy derivation for states without a routing decision.
    plan = state.get("query_plan") or {}
    complexity = str(plan.get("complexity", "")).lower() if isinstance(plan, dict) else ""
    if complexity in ("multi_part", "multi_hop"):
        return "plan_tasks"
    query_type = str(state.get("query_type", "general")).lower()
    if query_type in ("cross_reference", "case_law"):
        return "multi_hop_retrieve"
    return "retrieve"


def review_node(state: RAGState) -> dict[str, Any]:
    """M5 human-in-the-loop gate (only present when ``hitl=True``).

    Pauses the graph with :func:`langgraph.types.interrupt`, surfacing the
    generated answer + groundedness for a human decision.  The resume value
    (``{"approved": bool}`` or a bare bool) lands on the state as
    ``approved``; the conditional ``route_after_review`` edge then decides.
    """
    from langgraph.types import interrupt

    decision = interrupt({
        "message": "Review the grounded answer before release.",
        "query": state.get("query", ""),
        "answer": state.get("answer", ""),
        "groundedness": state.get("groundedness", 0.0),
        "hallucination_detected": state.get("hallucination_detected", False),
        "retry_count": state.get("retry_count", 0),
    })
    approved = bool(decision.get("approved", True)) if isinstance(decision, dict) else bool(decision)
    return {"approved": approved}


def _checkpointer_kind() -> str:
    """Resolve ``RAG_AGENT_CHECKPOINTER`` via the shared config seam."""
    kind: str = cfg.agent_checkpointer
    return kind.lower()


def checkpointer_is_durable() -> bool:
    """Whether the configured checkpointer survives process restarts.

    Only the ``postgres`` checkpointer is durable; the in-process
    ``MemorySaver`` loses paused (interrupted) HITL threads whenever the
    worker restarts (RAG UI audit gap #5).  ``/api/rag/health`` and the
    HITL 202 payloads surface this so operators can verify production
    HITL durability.
    """
    return _checkpointer_kind() == "postgres"


#: In-process MemorySaver singleton — shared across requests so a paused
#: (interrupted) thread can be resumed by a later HTTP call.
_memory_saver: Any | None = None

#: Cached PostgresSaver singleton + the DSN it was built for.  The saver
#: holds one psycopg connection for the life of the process (per worker);
#: recreating it per request leaked connections (one open ``psycopg.connect``
#: per agent call, never closed) and re-ran ``setup()`` DDL every time.
#: ``PostgresSaver`` serializes access with its own internal lock, so sharing
#: one saver across request threads is safe for the sync Flask path (each
#: gunicorn worker process holds its own singleton).
_postgres_saver: Any | None = None
_postgres_dsn: str | None = None
_postgres_setup_done: set[str] = set()
_postgres_lock = threading.Lock()


def _close_postgres_checkpointer() -> None:
    """Close and drop the cached PostgresSaver connection (best-effort).

    Called automatically at process exit (``atexit``) and when the DSN
    changes; tests can also call it (via ``_reset_checkpointers``) to
    avoid leaking real connections across cases.
    """
    global _postgres_saver, _postgres_dsn
    saver, _postgres_saver = _postgres_saver, None
    _postgres_dsn = None
    if saver is None:
        return
    try:
        conn = getattr(saver, "conn", None)
        close = getattr(conn, "close", None)
        if callable(close):
            close()
    except Exception as exc:
        logger.debug("closing PostgresSaver connection failed (%s)", exc)


def _reset_checkpointers() -> None:
    """Drop all cached checkpointers (memory + postgres).

    Test/shutdown helper: closes the postgres connection (if any) so no
    connection survives past the test that created it.
    """
    global _memory_saver
    with _postgres_lock:
        _close_postgres_checkpointer()
        _memory_saver = None


atexit.register(_close_postgres_checkpointer)


def _build_checkpointer(kind: str | None = None) -> Any | None:
    """Build the checkpointer for ``kind`` (memory | postgres | none).

    * ``memory``  — :class:`langgraph.checkpoint.memory.MemorySaver`
      (default; no DB, in-process only — dev/tests).  Cached singleton.
    * ``postgres`` — :class:`langgraph.checkpoint.postgres.PostgresSaver`
      against ``DATABASE_URL``; requires ``langgraph-checkpoint-postgres``
      + ``psycopg`` (psycopg-binary provides libpq).  Cached singleton per
      DSN: the connection is opened once (``autocommit=True`` + ``dict_row``
      factory, mirroring ``PostgresSaver.from_conn_string``) and ``setup()``
      runs once per DSN.  A DSN change closes the old connection before
      opening a new one.  Best-effort: a missing dep / bad DSN degrades to
      ``None`` (no checkpointing) rather than raising.
    * ``none`` — no checkpointer (no resume support).

    Security note (2026 checkpointer advisory): persisted checkpoints
    contain the full ``RAGState`` — user queries, retrieved chunk text,
    generated answers.  Treat restored state as **untrusted input** (never
    render checkpoint bytes without going through the normal sanitize /
    citation-validation path) and as **sensitive data at rest**: production
    Postgres should use encryption at rest, and operators who need less
    exposure should prefer ``RAG_AGENT_CHECKPOINTER=memory`` (nothing
    persisted) or narrow checkpoint retention.  No redaction is applied
    here by design — the resume path needs the full state to continue.
    """
    kind = (kind or _checkpointer_kind()).lower()
    if kind in ("none", ""):
        return None
    if kind == "postgres":
        with _postgres_lock:
            try:
                import psycopg
                from langgraph.checkpoint.postgres import PostgresSaver

                dsn = os.environ.get("DATABASE_URL") or ""
                if not dsn:
                    logger.warning("RAG_AGENT_CHECKPOINTER=postgres but DATABASE_URL unset — no checkpointing")
                    return None
                global _postgres_saver, _postgres_dsn
                # Reuse the cached saver while the DSN is unchanged and the
                # underlying connection is still open.
                if _postgres_saver is not None and _postgres_dsn == dsn:
                    try:
                        if int(getattr(getattr(_postgres_saver, "conn", None), "closed", 0)) == 0:
                            return _postgres_saver
                    except (TypeError, ValueError):
                        pass
                    # Cached connection died — drop it and reconnect below.
                    _close_postgres_checkpointer()
                elif _postgres_saver is not None:
                    # DSN changed — close the old connection before opening
                    # a new one so config rotations never leak.
                    _close_postgres_checkpointer()
                # Mirror from_conn_string: autocommit (setup() DDL must
                # commit; a bare connect() would leave it uncommitted) and
                # dict_row (checkpoint reads index rows by column name).
                from psycopg.rows import dict_row

                conn = psycopg.connect(dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row)
                try:
                    saver = PostgresSaver(conn)
                    if dsn not in _postgres_setup_done:
                        saver.setup()  # idempotent CREATE TABLE IF NOT EXISTS
                        _postgres_setup_done.add(dsn)
                except Exception:
                    try:
                        conn.close()
                    except Exception as exc:
                        logger.debug("closing failed PostgresSaver connection (%s)", exc)
                    raise
                _postgres_saver = saver
                _postgres_dsn = dsn
                return saver
            except Exception as exc:
                logger.warning("PostgresSaver unavailable — no checkpointing (%s)", exc)
                return None
    try:
        from langgraph.checkpoint.memory import MemorySaver

        # Singleton: the in-process saver must be shared across requests so a
        # paused (interrupted) thread can be resumed by a later HTTP call.
        global _memory_saver
        if _memory_saver is None:
            _memory_saver = MemorySaver()
        return _memory_saver
    except Exception:
        return None


def _resolve_evidence_selector(explicit: bool | None) -> bool:
    """Resolve the ``evidence_selector`` topology flag.

    An explicit bool pins the topology (tests / callers that manage the
    flag themselves); ``None`` reads the live ``ENABLE_EVIDENCE_SELECTOR``
    config on every call so flag flips take effect without a restart.
    """
    if explicit is not None:
        return bool(explicit)
    try:
        return bool(cfg.evidence_selector)
    except Exception:
        return False


def _resolve_fso_advisor(explicit: bool | None) -> bool:
    """Resolve the ``fso_advisor`` topology flag (ADR-0003).

    Same contract as :func:`_resolve_evidence_selector`: explicit bool pins
    the topology, ``None`` reads live ``FSO_ADVISOR_ENABLED`` (default off).
    """
    if explicit is not None:
        return bool(explicit)
    try:
        return bool(cfg.fso_advisor_enabled)
    except Exception:
        return False


def _resolve_structured_reasoner(explicit: bool | None) -> bool:
    """Resolve the ``structured_reasoner`` topology flag (roadmap Phase 3).

    Same contract: explicit bool pins, ``None`` reads live
    ``ENABLE_STRUCTURED_REASONER`` (default off).
    """
    if explicit is not None:
        return bool(explicit)
    try:
        return bool(cfg.structured_reasoner)
    except Exception:
        return False


def _resolve_legal_auditor(explicit: bool | None) -> bool:
    """Resolve the ``legal_auditor`` topology flag (roadmap Phase 3).

    Same contract: explicit bool pins, ``None`` reads live
    ``ENABLE_LEGAL_AUDITOR`` (default off).
    """
    if explicit is not None:
        return bool(explicit)
    try:
        return bool(cfg.legal_auditor)
    except Exception:
        return False


def build_graph(
    hitl: bool = False,
    checkpointer: Any | None = None,
    evidence_selector: bool | None = None,
    fso_advisor: bool | None = None,
    structured_reasoner: bool | None = None,
    legal_auditor: bool | None = None,
) -> Any:
    """Build and compile the agent ``StateGraph``.

    Args:
        hitl: Insert the M5 ``review`` (human-in-the-loop) node between
            ``verify`` and the conditional edge.
        checkpointer: A LangGraph checkpointer (e.g. ``MemorySaver`` /
            ``PostgresSaver``) to enable thread resume; ``None`` disables
            checkpointing.
        evidence_selector: Insert the optional ``evidence`` node between
            ``retrieve`` and ``generate``.  ``None`` (default) reads the
            live ``ENABLE_EVIDENCE_SELECTOR`` config; pass an explicit bool
            to pin the topology regardless of config.
        fso_advisor: Insert the deterministic FSO advisory gate (ADR-0003):
            a post-verification ``fso_advisory`` directly before
            ``finalize`` on every terminal path. ``None`` reads the live
            ``FSO_ADVISOR_ENABLED`` config (default off).
        structured_reasoner: Insert the Phase 3 reasoning path
            (``structured_reasoner`` → [``auditor``] → ``generate``) after
            retrieval.  ``None`` reads live ``ENABLE_STRUCTURED_REASONER``
            (default off).
        legal_auditor: Insert the ``auditor`` gate with the capped
            revise loop on the reasoning path.  ``None`` reads live
            ``ENABLE_LEGAL_AUDITOR`` (default off).  Without the reasoner
            flag this flag has no effect.

    Returns the compiled graph; callers ``.invoke(state)`` it.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError(
            "The LangGraph agent pipeline requires 'langgraph'. "
            "Install it (pip install langgraph) to use /api/rag/query/agent."
        ) from exc

    from app.rag.agent import nodes

    builder: StateGraph = StateGraph(RAGState)

    builder.add_node("classify", lambda state, cfg=None: nodes.classify_node(state))
    # Phase 2.1: Query Planning Layer — inserts plan_node between classify
    # and retrieve. Produces subquestions + evidence requirements for
    # downstream nodes (retrieve, multi_hop_retrieve).
    builder.add_node("plan", lambda state, cfg=None: nodes.plan_node(state))
    builder.add_node("retrieve", lambda state, cfg=None: nodes.retrieve_node(state))
    # Multi-hop retrieval node for cross-reference / case-law queries (1.2).
    builder.add_node("multi_hop_retrieve", lambda state, cfg=None: nodes.multi_hop_retrieve_node(state))
    builder.add_node("generate", lambda state, cfg=None: nodes.generate_node(state))
    builder.add_node("verify", lambda state, cfg=None: nodes.verify_node(state))
    # Citation quality gate (2026-08-26): checks if cited chunks are
    # actually in the retrieved set before finalizing.
    builder.add_node("citation_quality", lambda state, cfg=None: nodes.citation_quality_node(state))
    # Phase 2.6: Targeted retry — replaces generic expand_query with
    # failure-aware retrieval targeting.
    builder.add_node("targeted_retry", lambda state, cfg=None: nodes.targeted_retry_node(state))
    builder.add_node("expand_query", lambda state, cfg=None: nodes.expand_query_node(state))
    builder.add_node("finalize", lambda state, cfg=None: nodes.finalize_node(state))
    # P1: EvidenceTask DAG nodes.
    builder.add_node("plan_tasks", lambda state, cfg=None: nodes.plan_tasks_node(state))
    # P3: Budget gate before DAG execution
    builder.add_node("budget_gate", lambda state, cfg=None: nodes.budget_gate_node(state))
    builder.add_node("execute_task", lambda state, cfg=None: nodes.execute_task_node(state))
    builder.add_node("synthesize", lambda state, cfg=None: nodes.synthesize_node(state))
    # P2: Evidence sufficiency gate + abstention
    builder.add_node("evidence_sufficiency", lambda state, cfg=None: nodes.evidence_sufficiency_node(state))
    builder.add_node("abstain", lambda state, cfg=None: nodes.abstain_node(state))
    # FSO advisory gate (ADR-0003, deterministic — no LLM, sub-millisecond).
    fso_on = _resolve_fso_advisor(fso_advisor)
    if fso_on:
        builder.add_node("fso_advisory", lambda state, cfg=None: nodes.fso_advisory_node(state))

    builder.add_edge(START, "classify")
    builder.add_edge("classify", "plan")
    # Phase 0: exactly one path per query, chosen by the plan's complexity.
    builder.add_conditional_edges(
        "plan",
        _route_after_plan,
        {"retrieve": "retrieve", "multi_hop_retrieve": "multi_hop_retrieve", "plan_tasks": "plan_tasks"},
    )
    # multi_hop_retrieve re-runs retrieval with refined query, then
    # merges results into the state before generating.
    builder.add_edge("multi_hop_retrieve", "retrieve")

    # P1 DAG execution: plan_tasks → budget_gate → execute_task →
    # evidence_sufficiency → synthesize.  P2: sufficiency gates synthesis
    # (don't generate from unchecked evidence).
    builder.add_edge("plan_tasks", "budget_gate")
    builder.add_conditional_edges(
        "budget_gate",
        _route_after_budget,
        {"execute_task": "execute_task", "abstain": "abstain"},
    )
    builder.add_edge("execute_task", "evidence_sufficiency")
    builder.add_conditional_edges(
        "evidence_sufficiency",
        _route_after_evidence,
        {"synthesize": "synthesize", "targeted_retry": "targeted_retry", "abstain": "abstain"},
    )
    builder.add_edge("synthesize", "verify")

    # Optional evidence node between retrieve and generate (feature-flagged).
    # The flag is resolved per build (see _resolve_evidence_selector), never
    # frozen at import — see _get_graph for the request-path cache.
    evidence_on = _resolve_evidence_selector(evidence_selector)
    if evidence_on:
        builder.add_node("evidence", nodes.evidence_node)
        builder.add_edge("retrieve", "evidence")

    # Phase 3 reasoning path (roadmap §32.1): structured argument IR with an
    # optional deterministic audit + capped revise loop, flag-gated and
    # default off.  It rewires the linear retrieve → generate hop only —
    # exactly one outgoing edge per node, no fan-out.
    reasoning_on = _resolve_structured_reasoner(structured_reasoner)
    auditor_on = reasoning_on and _resolve_legal_auditor(legal_auditor)
    if reasoning_on:
        builder.add_node("structured_reasoner", lambda state, cfg=None: nodes.structured_reasoner_node(state))
        builder.add_edge("evidence" if evidence_on else "retrieve", "structured_reasoner")
        if auditor_on:
            builder.add_node("auditor", lambda state, cfg=None: nodes.auditor_node(state))
            builder.add_edge("structured_reasoner", "auditor")
            builder.add_conditional_edges(
                "auditor",
                route_after_audit,
                {"structured_reasoner": "structured_reasoner", "generate": "generate"},
            )
        else:
            builder.add_edge("structured_reasoner", "generate")
    elif evidence_on:
        builder.add_edge("evidence", "generate")
    else:
        builder.add_edge("retrieve", "generate")

    builder.add_edge("generate", "verify")
    builder.add_edge("verify", "citation_quality")

    # Post-verification FSO advisory (when on) sits directly before finalize
    # on every terminal path: the citation/verify signals exist by then, and
    # (HITL) the human has already approved the answer the Act attaches to.
    final_step = "fso_advisory" if fso_on else "finalize"
    if fso_on:
        builder.add_edge("fso_advisory", "finalize")
    if hitl:
        # M5: human-in-the-loop gate.  review interrupts; approved → finalize,
        # rejected → expand_query (re-generate with a rewritten query).
        # Same (state, cfg=None) registration shape as every other node —
        # LangGraph tolerates both arities, but uniformity means a future
        # signature change (e.g. config-aware nodes) touches one pattern.
        builder.add_node("review", lambda state, cfg=None: review_node(state))
        builder.add_edge("citation_quality", "review")
        builder.add_conditional_edges(
            "review",
            route_after_review,
            {"expand_query": "expand_query", "finalize": final_step},
        )
    else:
        # Multi-signal threshold: verify → citation_quality → retry/finalize
        builder.add_conditional_edges(
            "citation_quality",
            route_after_verify,
            {"targeted_retry": "targeted_retry", "expand_query": "expand_query", "finalize": final_step},
        )

    # P2: abstain terminal path
    if fso_on:
        builder.add_edge("abstain", "fso_advisory")
    else:
        builder.add_edge("abstain", "finalize")

    # Phase 1: retries return to the path that raised them — the DAG path
    # re-enters plan_tasks (no-op re-read) → budget_gate → execute_task,
    # which executes only the not-yet-completed tasks; the linear path
    # re-retrieves as before.
    builder.add_conditional_edges(
        "targeted_retry",
        _route_after_retry,
        {"plan_tasks": "plan_tasks", "retrieve": "retrieve"},
    )
    builder.add_edge("expand_query", "retrieve")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


#: Request-path graph cache, keyed by ``(hitl, evidence_selector, fso_advisor)``.
#: The compiled topology depends on all three flags, so all are part of the
#: key: flipping ``ENABLE_EVIDENCE_SELECTOR`` or ``FSO_ADVISOR_ENABLED`` at
#: runtime compiles (at most) one additional graph instead of serving a stale
#: topology — no restart needed.  Graphs carrying a checkpointer are never
#: cached here (the saver identity can change across calls); they are built
#: fresh per call as before.
_graph_cache: dict[tuple[bool, bool, bool, bool, bool], Any] = {}
_graph_cache_lock = threading.Lock()


def _get_graph(
    hitl: bool = False,
    evidence_selector: bool | None = None,
    fso_advisor: bool | None = None,
    checkpointer: Any | None = None,
    structured_reasoner: bool | None = None,
    legal_auditor: bool | None = None,
) -> Any:
    """Return the compiled graph for this request's flag combination.

    ``evidence_selector=None`` / ``fso_advisor=None`` resolve the live config
    on every call, so the served topology always matches the current flags.
    Raises ``ImportError`` (with the install hint) when langgraph is missing.
    """
    resolved = _resolve_evidence_selector(evidence_selector)
    resolved_fso = _resolve_fso_advisor(fso_advisor)
    resolved_reasoning = _resolve_structured_reasoner(structured_reasoner)
    resolved_auditor = _resolve_legal_auditor(legal_auditor)
    if checkpointer is not None:
        return build_graph(
            hitl=hitl,
            checkpointer=checkpointer,
            evidence_selector=resolved,
            fso_advisor=resolved_fso,
            structured_reasoner=resolved_reasoning,
            legal_auditor=resolved_auditor,
        )
    key = (bool(hitl), resolved, resolved_fso, resolved_reasoning, resolved_auditor)
    with _graph_cache_lock:
        graph = _graph_cache.get(key)
        if graph is None:
            graph = build_graph(
                hitl=hitl,
                evidence_selector=resolved,
                fso_advisor=resolved_fso,
                structured_reasoner=resolved_reasoning,
                legal_auditor=resolved_auditor,
            )
            _graph_cache[key] = graph
        return graph


def _reset_graph_cache() -> None:
    """Drop all cached compiled graphs (test helper)."""
    with _graph_cache_lock:
        _graph_cache.clear()


# Compiled once at import for introspection/tests (plan §5.2).  Lazy:
# importing this module is the only place langgraph gets imported, so the
# rest of the app is untouched when it is missing.  The request path
# (``run_agent`` / ``resume_agent``) does NOT use these directly — it goes
# through ``_get_graph``, which resolves the live topology flags per call.
try:
    agent_graph: Any = build_graph(hitl=False)
    agent_graph_hitl: Any = build_graph(hitl=True)
except ImportError:  # pragma: no cover - langgraph optional
    agent_graph = None
    agent_graph_hitl = None


def run_agent(
    state: RAGState,
    *,
    thread_id: str | None = None,
    hitl: bool = False,
    checkpointer: Any | None = None,
    fso_advisor: bool | None = None,
) -> dict[str, Any]:
    """Invoke the agent graph on an initial state.

    Args:
        state: Initial ``RAGState``.
        thread_id: When set, invoke under a checkpointer with this thread
            id so a paused (interrupted) run can be resumed later.  The
            checkpointer comes from *checkpointer* if given, else from the
            ``RAG_AGENT_CHECKPOINTER`` config (memory default).
        hitl: Use the M5 human-in-the-loop variant (review interrupt).
        checkpointer: Optional explicit checkpointer (bypasses config).
        fso_advisor: Pin the FSO advisory topology (``None`` = live
            ``FSO_ADVISOR_ENABLED`` config).

    Returns:
        The ``RAGResponse``-schema dict on completion (unwrapped from the
        final state — use :func:`_completed_payload` in
        :mod:`app.rag.agent.service`, which also accepts full-state
        results, rather than re-unwrapping here).  When the graph pauses
        at the M5 review interrupt, returns the raw state carrying the
        ``__interrupt__`` key (LangGraph convention) instead — the caller
        should detect it and surface the review request.

    The graph comes from the flag-aware ``_get_graph`` cache, so the live
    ``ENABLE_EVIDENCE_SELECTOR`` / ``FSO_ADVISOR_ENABLED`` values are
    honoured on every call.
    """
    if thread_id:
        # Rebuild with a checkpointer so resume works across requests.
        cp = checkpointer if checkpointer is not None else _build_checkpointer()
        graph = _get_graph(hitl, fso_advisor=fso_advisor, checkpointer=cp)
        result: dict[str, Any] = graph.invoke(
            state,
            config={"configurable": {"thread_id": thread_id}},
        )
    else:
        # Raises ImportError with the install hint when langgraph is missing.
        graph = _get_graph(hitl, fso_advisor=fso_advisor)
        result = graph.invoke(state)

    # Contract (M3): a completed run returns the ``RAGResponse``-schema
    # dict.  A paused M5 run returns the raw state carrying ``__interrupt__``
    # so the caller can surface the review request.
    if "__interrupt__" in result:
        return result
    response: dict[str, Any] = result.get("response") or {}
    return response


def resume_agent(
    thread_id: str,
    *,
    approved: bool = True,
    hitl: bool = True,
    checkpointer: Any | None = None,
    fso_advisor: bool | None = None,
) -> dict[str, Any]:
    """Resume a paused M5 run by thread id.

    Re-invokes the graph under the same thread id with a
    ``Command(resume=...)`` carrying the human decision.  Returns the final
    state (``response`` set) or the next ``__interrupt__`` if it pauses again.

    ``fso_advisor`` pins the advisory topology so a per-request
    ``fso_advisory:true`` run that paused at ``review`` resumes on the same
    topology (``None`` = live ``FSO_ADVISOR_ENABLED``).
    """
    from langgraph.types import Command

    cp = checkpointer if checkpointer is not None else _build_checkpointer()
    if cp is None:
        raise ValueError("Resume requires a checkpointer (RAG_AGENT_CHECKPOINTER=memory|postgres).")
    graph = _get_graph(hitl, fso_advisor=fso_advisor, checkpointer=cp)
    result: dict[str, Any] = graph.invoke(
        Command(resume={"approved": approved}),
        config={"configurable": {"thread_id": thread_id}},
    )
    return result


# Re-export for tests / convenience.
route_fn: Callable[[RAGState], str] = route_after_verify

"""Budget-aware complexity routing economics (V2 plan Phase 3, item 18).

The proposal's Priority 6: a production graph must know *how much
computation a query is worth* before spending it — "What is Section 12?"
should never enter the decomposition/DAG machinery, and a MULTI_HOP query
should not be held to the same budget as a direct lookup.

Three pure, deterministic pieces (no LLM, fully unit-testable):

- :func:`route_strategy` — pick the execution strategy from the query
  plan's complexity, the classifier's query type, and an explicit
  DIRECT override for single-identifier lookups.  Once a flow has started
  retrying, the strategy is *pinned* so a mid-flow re-plan can never
  flip-flop between the linear and DAG paths.
- :data:`BUDGET_TIERS` + :func:`apply_budget_tier` — per-strategy budget
  ceilings (tasks / retrieval rounds / documents / LLM calls).  Tiers are
  applied shrink-only: an explicit caller- or operator-supplied cap is
  never raised, so the tiers can only *tighten* policy.
- :func:`is_exhausted` — the single exhaustion predicate shared by the
  DAG ``budget_gate`` node and the linear retry router, so both paths
  enforce the same economics.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Direct-query detection (the "What is Section 12?" override)
# ---------------------------------------------------------------------------

#: Any statutory identifier mention — section/rule/regulation/article/clause.
_SECTION_RE = re.compile(r"\b(?:section|sec\.?|rule|regulation|article|clause)\s+\d+", re.IGNORECASE)

#: Coordinating conjunctions (mirrors the planner's complexity signal): a
#: query that joins clauses is a genuine multi-part question even when it
#: cites a single section.
_CONJUNCTION_RE = re.compile(r"\b(?:and|or|but|however|whereas|while)\b", re.IGNORECASE)


def _single_identifier_lookup(query: str) -> bool:
    """True for one-identifier, one-clause lookups (e.g. "What is Section 12?").

    Exactly one statutory reference, no coordinating conjunctions — these
    queries cite a single provision and ask a single question about it, so
    decomposition adds cost without adding coverage.
    """
    if not query:
        return False
    if _CONJUNCTION_RE.search(query):
        return False
    return len(_SECTION_RE.findall(query)) == 1


# ---------------------------------------------------------------------------
# Budget tiers
# ---------------------------------------------------------------------------

#: Per-tier budget ceilings.  ``direct`` covers plain lookups (no DAG
#: tasks at all, few LLM calls); ``moderate`` covers MULTI_PART
#: decomposition; ``deep`` covers MULTI_HOP DAGs and the iterative
#: multi-hop retrieval path.
BUDGET_TIERS: dict[str, dict[str, int]] = {
    "direct": {"max_tasks": 0, "max_retrieval_rounds": 3, "max_documents": 30, "max_llm_calls": 4},
    "moderate": {"max_tasks": 4, "max_retrieval_rounds": 4, "max_documents": 60, "max_llm_calls": 8},
    "deep": {"max_tasks": 8, "max_retrieval_rounds": 5, "max_documents": 90, "max_llm_calls": 16},
}

#: ``initial_state`` seeds the *deepest* tier as the ceiling; the planner
#: shrinks it to the query's tier.  (Keeps tier application monotonic.)
BUDGET_CEILING: dict[str, int] = BUDGET_TIERS["deep"]

_STRATEGY_TIERS: dict[str, str] = {
    "direct": "direct",
    "multi_hop": "deep",
    "decomposition": "moderate",
}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


def route_strategy(
    plan: dict[str, Any] | None,
    query_type: str,
    query: str = "",
    *,
    retry_count: int = 0,
    prior_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose the execution strategy for a query.

    Returns a JSON-safe decision dict persisted on state as
    ``routing_decision``::

        {"strategy": "direct"|"decomposition"|"multi_hop",
         "complexity": <plan complexity, lowercased>,
         "query_type": <classifier query type, lowercased>,
         "tier": <budget tier>,
         "pinned": bool}

    - MULTI_PART / MULTI_HOP plans → ``decomposition`` (the EvidenceTask
      DAG path) — unless the query is really a single-identifier lookup
      (the DIRECT override), which would otherwise leak "What is
      Section 12?" into the expensive pipeline.
    - cross_reference / case_law classifier types → ``multi_hop``.
    - everything else → ``direct`` (plain retrieve → generate).

    Once ``retry_count > 0`` and a prior decision exists, the prior
    strategy is **pinned**: a re-planned retry must not switch paths
    mid-flow (the retry routing in the graph depends on stability).
    """
    prior: dict[str, Any] = prior_decision if isinstance(prior_decision, dict) else {}
    if retry_count and prior.get("strategy"):
        return {
            "strategy": str(prior["strategy"]),
            "complexity": str(prior.get("complexity", "")),
            "query_type": str(prior.get("query_type", "")),
            "tier": str(prior.get("tier", "")),
            "pinned": True,
        }

    plan_dict = plan if isinstance(plan, dict) else {}
    complexity = str(plan_dict.get("complexity", "")).lower()
    qt = str(query_type or "general").lower()

    if complexity in ("multi_part", "multi_hop") and not _single_identifier_lookup(query):
        strategy = "decomposition"
    elif qt in ("cross_reference", "case_law"):
        strategy = "multi_hop"
    else:
        strategy = "direct"

    # Decomposition tier keys off the planner's complexity: MULTI_HOP
    # (chained dependencies, iterative retrieval) earns the deep tier;
    # MULTI_PART (independent parallel tasks) stays moderate.
    if strategy == "decomposition":
        tier = "deep" if complexity == "multi_hop" else "moderate"
    else:
        tier = _STRATEGY_TIERS[strategy]

    return {
        "strategy": strategy,
        "complexity": complexity,
        "query_type": qt,
        "tier": tier,
        "pinned": False,
    }


# ---------------------------------------------------------------------------
# Budget application + exhaustion
# ---------------------------------------------------------------------------


def apply_budget_tier(budget: dict[str, Any] | None, tier: str) -> dict[str, Any]:
    """Apply a tier's ceilings to a budget dict, shrink-only.

    Every cap becomes ``min(current, tier_cap)``: fresh flows are set to
    the tier policy, and a re-entered flow (or an operator-supplied
    override) can only be tightened, never raised.  Unknown tiers fall
    back to the deep ceiling so application is always safe.
    """
    caps = BUDGET_TIERS.get(tier, BUDGET_CEILING)
    out = dict(budget or {})
    for key, cap in caps.items():
        out[key] = min(_safe_int(out.get(key), cap), cap)
    return out


def is_exhausted(budget: dict[str, Any] | None, *, include_tasks: bool = True) -> bool:
    """True when any consumed counter has reached its cap.

    Shared by ``budget_gate_node`` (DAG path) and ``route_after_verify``
    (linear path) so both enforce identical economics.

    ``include_tasks=False`` is for the linear path, which never consumes
    task slots: a zero-cap ``max_tasks`` (direct tier) must not read as
    "spent" on a path that cannot spend tasks.
    """
    b = budget if isinstance(budget, dict) else {}
    pairs = (
        ("consumed_tasks", "max_tasks", 10),
        ("consumed_retrieval_rounds", "max_retrieval_rounds", 5),
        ("consumed_documents", "max_documents", 50),
        ("consumed_llm_calls", "max_llm_calls", 20),
    )
    for consumed, cap_key, default in pairs:
        if consumed == "consumed_tasks" and not include_tasks:
            continue
        if _safe_int(b.get(consumed, 0), 0) >= _safe_int(b.get(cap_key, default), default):
            return True
    return False

"""Requirement-level, atomicity, efficiency, and evidence-completeness metrics.

These extend the Phase 4 kind-level benchmark (decompositionBenchmark) with
the richer metrics the reviewer's architecture calls for:

    requirement_coverage     RC  = gold requirements correctly represented / total gold
    atomicity_score          AS  = 1 - multi_claim_tasks / total_tasks
    decomposition_efficiency DE  = useful_requirements / total_generated_requirements
    evidence_completeness    EC  = requirements_with_sufficient_evidence / mandatory_requirements

The metrics operate on :class:`DecompositionBenchmark` entries augmented with
requirement-level gold expectations (optional) and prediction metadata (recorded
by :func:`record_requirement_prediction`).

Design principle (per RAG_IMPROVEMENTS.md Phase 3): decompose into independently
verifiable answer requirements, then derive retrieval questions from those.
These metrics measure how well the planner does that, rather than how closely it
matches a gold list of subquestions.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.rag.evidence_task import AnswerRequirementGraph

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.rag.evaluation.benchmark import QueryBenchmark

# ---------------------------------------------------------------------------
# Requirement-level gold expectations (optional extension of GoldEntry)
# ---------------------------------------------------------------------------

#: A gold answer requirement: what the ideal decomposition should produce for
#: one independently verifiable answer requirement.  Distinct from
#: ``gold_task_kinds`` (which is kind-level); this is *requirement-level* with
#: explicit answer types, evidence requirements, and mandatory flags.
GoldRequirement = dict[str, Any]


def _gold_requirements(entry: dict[str, Any]) -> list[GoldRequirement]:
    """Extract requirement-level gold from a GoldEntry (empty list if absent)."""
    return list(entry.get("gold_requirements") or [])


# ---------------------------------------------------------------------------
# Requirement matching
# ---------------------------------------------------------------------------

def _count_matched_requirements(
    pred_graph: AnswerRequirementGraph,
    gold_reqs: list[GoldRequirement],
) -> int:
    """Count gold requirements that have a matching predicted requirement.

    Matching is **order-insensitive**: requirement ids are planner-internal
    labels (``r1`` vs ``R1`` vs ``R4``) — the numbering order says nothing
    about decomposition quality.  Matching is by evidence type, with the
    subject check applied only where it can be honest:

    - A gold type occurring **once** matches any unused prediction of that
      type.  The planner's per-requirement subjects are still weak (often
      just the Act name), so requiring subject alignment there would measure
      subject phrasing, not decomposition.
    - A gold type occurring **multiple times** (e.g. the two comparative
      sides) requires the matched predictions' subjects to be compatible —
      one same-type prediction cannot satisfy two distinct gold requirements.

    Each gold requirement is matched at most once, and each predicted
    requirement satisfies at most one gold requirement (multiset-aware).
    """
    type_counts: Counter = Counter(str(g.get("type", "")) for g in gold_reqs)
    used: set[int] = set()
    matched = 0
    # Pass 1: repeated gold types — subject compatibility required.
    for gold_req in gold_reqs:
        gold_type = str(gold_req.get("type", ""))
        if type_counts[gold_type] <= 1:
            continue
        gold_subject = str(gold_req.get("subject", "")).strip().lower()
        match_idx = next(
            (
                i
                for i, pred in enumerate(pred_graph.requirements)
                if i not in used
                and pred.type.value == gold_type
                and (not gold_subject or _subjects_compatible(pred.subject, gold_subject))
            ),
            None,
        )
        if match_idx is not None:
            used.add(match_idx)
            matched += 1
    # Pass 2: single-occurrence gold types — type-only.
    for gold_req in gold_reqs:
        gold_type = str(gold_req.get("type", ""))
        if type_counts[gold_type] > 1:
            continue
        match_idx = next(
            (
                i
                for i, pred in enumerate(pred_graph.requirements)
                if i not in used and pred.type.value == gold_type
            ),
            None,
        )
        if match_idx is not None:
            used.add(match_idx)
            matched += 1
    return matched


def _subjects_compatible(pred_subject: str, gold_subject: str) -> bool:
    """Lenient subject compatibility check.

    True when one subject's content words are a **subset** of the other's —
    the planner may phrase the same requirement more narrowly ('FSS Act') or
    more broadly ('small food businesses under the FSS Act') than gold, but a
    genuinely different requirement ('small food businesses' vs 'large food
    manufacturers') is a subset in neither direction.  Mere word overlap is
    NOT enough: {food} alone would match every food-law subject.
    """
    stop = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or"}

    def words(s: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in stop}

    pw, gw = words(pred_subject or ""), words(gold_subject or "")
    if not pw or not gw:
        return True  # nothing to compare against — defer to the type check
    return pw <= gw or gw <= pw


def pred_req_type_matches(pred_req: AnswerRequirement, gold_req: GoldRequirement) -> bool:
    """Check that the predicted requirement's type matches the gold type."""
    return pred_req.type.value == str(gold_req.get("type", ""))


# ---------------------------------------------------------------------------
# Metric computations
# ---------------------------------------------------------------------------

def requirement_coverage(
    pred_graph: AnswerRequirementGraph,
    gold_reqs: list[GoldRequirement],
) -> float:
    """RC = |gold requirements correctly represented| / |total gold requirements|.

    A gold requirement is 'correctly represented' when the planner produced a
    requirement with the same id AND the same evidence type.  This rewards
    completeness of requirement extraction without penalizing reasonable
    variations in how a requirement is phrased.
    """
    if not gold_reqs:
        return 1.0  # no gold requirements → nothing to miss
    matched = _count_matched_requirements(pred_graph, gold_reqs)
    return round(matched / len(gold_reqs), 4)


def atomicity_score(tasks: list[Any]) -> float:
    """AS = 1 - multi_claim_tasks / total_tasks.

    A 'multi-claim task' is one whose objective/question captures more than one
    independently verifiable proposition.  The heuristic used here is a simple
    linguistic check: a task whose question contains multiple terminal punctuations
    ( '?', '.', ';' ) separated by conjunctions is likely multi-claim.

    When there are no tasks, returns 1.0 (nothing to atomize).
    """
    if not tasks:
        return 1.0
    multi = 0
    for task in tasks:
        question = getattr(task, "question", "") or ""
        objective = getattr(task, "objective", "") or ""
        text = f"{question} {objective}".lower()
        # Heuristic: sentence-like splits with conjunctions suggest multiple claims
        parts = [p.strip() for p in text.replace("?", ".").split(".") if p.strip()]
        conjunctive = any(
            any(conj in p for conj in ("and", "or", "but", "also", "further"))
            for p in parts
        )
        if conjunctive and len(parts) >= 2:
            multi += 1
    return round(1.0 - multi / len(tasks), 4)


def decomposition_efficiency(
    pred_graph: AnswerRequirementGraph,
    gold_reqs: list[GoldRequirement],
) -> float:
    """DE = useful_requirements / total_generated_requirements.

    A 'useful' requirement is one that matches a gold requirement (by id + type).
    Extra requirements that don't map to gold reduce efficiency; missing gold
    requirements don't (they're captured by requirement_coverage instead).
    """
    if not pred_graph.requirements:
        return 1.0  # nothing generated → no waste
    useful = _count_matched_requirements(pred_graph, gold_reqs)
    return round(useful / len(pred_graph.requirements), 4)


def evidence_completeness(
    pred_graph: AnswerRequirementGraph,
    sufficiency_map: dict[str, bool] | None = None,
) -> float:
    """EC = |mandatory requirements with sufficient evidence| / |mandatory requirements|.

    ``sufficiency_map`` maps requirement id → True when sufficient evidence was
    found for that requirement.  When absent, all mandatory requirements are
    assumed insufficient (conservative: 0.0).

    Non-mandatory requirements are not counted — they're nice-to-have context,
    not blockers for a defensible answer.
    """
    mandatory = [r for r in pred_graph.requirements if r.mandatory]
    if not mandatory:
        return 1.0  # no mandatory requirements → nothing to fail
    if sufficiency_map is None:
        return 0.0  # conservative: no sufficiency data → nothing deemed sufficient
    sufficient = sum(
        1 for r in mandatory
        if sufficiency_map.get(r.id, False) is True
    )
    return round(sufficient / len(mandatory), 4)


# ---------------------------------------------------------------------------
# Prediction recording (requirement-level)
# ---------------------------------------------------------------------------

def record_requirement_prediction(
    benchmark_entry: "QueryBenchmark",
    requirement_graph: AnswerRequirementGraph,
) -> None:
    """Record requirement-graph-level prediction metadata on a benchmark entry.

    Populates ``entry.predicted_requirements`` (list of requirement dicts) and
    ``entry.predicted_requirement_ids`` (set of requirement ids) so the richer
    metrics can be computed without re-running the planner.
    """
    benchmark_entry.predicted_requirement_graph = requirement_graph
    benchmark_entry.predicted_requirement_ids = {r.id for r in requirement_graph.requirements}
    benchmark_entry.predicted_requirements = [
        {
            "id": r.id,
            "type": r.type.value,
            "question": r.question,
            "answer_type": r.answer_type,
            "mandatory": r.mandatory,
            "evidence_required": list(r.evidence_required),
        }
        for r in requirement_graph.requirements
    ]


# ---------------------------------------------------------------------------
# Full requirement-level report
# ---------------------------------------------------------------------------

@dataclass
class RequirementLevelMetrics:
    """Requirement-level decomposition quality metrics for one query."""

    query: str
    requirement_coverage: float       # RC
    atomicity_score: float            # AS
    decomposition_efficiency: float   # DE
    evidence_completeness: float      # EC
    mandatory_count: int
    predicted_count: int
    gold_count: int
    multi_claim_task_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "requirement_coverage": self.requirement_coverage,
            "atomicity_score": self.atomicity_score,
            "decomposition_efficiency": self.decomposition_efficiency,
            "evidence_completeness": self.evidence_completeness,
            "mandatory_count": self.mandatory_count,
            "predicted_count": self.predicted_count,
            "gold_count": self.gold_count,
            "multi_claim_task_count": self.multi_claim_task_count,
        }


def compute_requirement_level_metrics(
    entry: "QueryBenchmark",
    requirement_graph: AnswerRequirementGraph | None = None,
    sufficiency_map: dict[str, bool] | None = None,
) -> RequirementLevelMetrics | None:
    """Compute requirement-level metrics for a single benchmark entry.

    Args:
        entry: Benchmark entry that may carry ``gold_requirements``.
        requirement_graph: The planner's AnswerRequirementGraph.  If None,
            falls back to ``entry.predicted_requirement_graph`` if present.
        sufficiency_map: Optional mapping requirement_id → evidence sufficient.

    Returns:
        Metrics, or None when the entry has no gold requirements to score
        against (requirement-level metrics are undefined when there's no gold).
    """
    gold_reqs = _gold_requirements(entry.__dict__ if hasattr(entry, "__dict__") else {})
    if not gold_reqs:
        return None

    if requirement_graph is None:
        requirement_graph = getattr(entry, "predicted_requirement_graph", None)

    if requirement_graph is None:
        logger.warning(
            "compute_requirement_level_metrics: no requirement graph for %r",
            entry.query,
        )
        return RequirementLevelMetrics(
            query=entry.query,
            requirement_coverage=0.0,
            atomicity_score=0.0,
            decomposition_efficiency=0.0,
            evidence_completeness=0.0,
            mandatory_count=0,
            predicted_count=0,
            gold_count=len(gold_reqs),
            multi_claim_task_count=0,
        )

    tasks = requirement_graph.derived_tasks
    rc = requirement_coverage(requirement_graph, gold_reqs)
    as_ = atomicity_score(tasks)
    de = decomposition_efficiency(requirement_graph, gold_reqs)
    ec = evidence_completeness(requirement_graph, sufficiency_map)

    # Count multi-claim tasks for transparency
    multi = sum(
        1 for t in tasks
        if _is_multi_claim_task(t)
    )

    return RequirementLevelMetrics(
        query=entry.query,
        requirement_coverage=rc,
        atomicity_score=as_,
        decomposition_efficiency=de,
        evidence_completeness=ec,
        mandatory_count=sum(1 for r in requirement_graph.requirements if r.mandatory),
        predicted_count=len(requirement_graph.requirements),
        gold_count=len(gold_reqs),
        multi_claim_task_count=multi,
    )


def _is_multi_claim_task(task: Any) -> bool:
    """Heuristic: does this task capture more than one proposition?"""
    question = getattr(task, "question", "") or ""
    objective = getattr(task, "objective", "") or ""
    text = f"{question} {objective}".lower()
    parts = [p.strip() for p in text.replace("?", ".").split(".") if p.strip()]
    if len(parts) < 2:
        return False
    return any(
        any(conj in p for conj in ("and", "or", "but", "also", "further"))
        for p in parts
    )


# ---------------------------------------------------------------------------
# Aggregate report across a benchmark
# ---------------------------------------------------------------------------

def requirement_level_report(
    benchmark: "Any",  # DecompositionBenchmark
    sufficiency_map: dict[str, dict[str, bool]] | None = None,
) -> dict[str, Any]:
    """Compute requirement-level metrics aggregated across a benchmark.

    Args:
        benchmark: A DecompositionBenchmark whose entries may carry
            ``gold_requirements``.
        sufficiency_map: Optional mapping query → {requirement_id: bool}.

    Returns:
        Aggregated metrics plus per-query breakdown.
    """
    entries = getattr(benchmark, "queries", [])
    per_query: list[RequirementLevelMetrics] = []
    for entry in entries:
        q_sufficiency = {}
        if sufficiency_map:
            q_sufficiency = sufficiency_map.get(entry.query, {})
        m = compute_requirement_level_metrics(entry, sufficiency_map=q_sufficiency)
        if m is not None:
            per_query.append(m)

    if not per_query:
        return {"error": "No entries with gold requirements in benchmark"}

    rc_vals = [m.requirement_coverage for m in per_query]
    as_vals = [m.atomicity_score for m in per_query]
    de_vals = [m.decomposition_efficiency for m in per_query]
    ec_vals = [m.evidence_completeness for m in per_query]

    def mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "total_scored_queries": len(per_query),
        "requirement_coverage": mean(rc_vals),
        "atomicity_score": mean(as_vals),
        "decomposition_efficiency": mean(de_vals),
        "evidence_completeness": mean(ec_vals),
        "per_query": [m.to_dict() for m in per_query],
    }

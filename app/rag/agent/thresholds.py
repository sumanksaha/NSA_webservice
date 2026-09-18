"""Single source of truth for agent routing/gating thresholds.

Every numeric gate the graph routes on lives here — previously the same
``0.5`` appeared in four places (claim groundedness, coverage floor,
sufficient-task ratio) and ``0.7`` in two, with no way to tell deliberate
tuning from copy-paste.  Tune here; the nodes, routers, and rubric read
from this module.

Conventions:

* ``*_RETRY_BELOW`` — fall below this and the graph retries.
* ``*_AT_LEAST`` — reach this to proceed (synthesize/finalize).
* Values are plain module constants (not env-driven): routing economics
  must stay deterministic and unit-testable without app config.
"""

from __future__ import annotations

#: Retry the linear path (``expand_query``) when overall groundedness falls
#: below this.  Read by ``route_after_verify`` (graph.py).
GROUNDEDNESS_RETRY_BELOW: float = 0.7

#: Route to ``targeted_retry`` when the share of answer claims entailed by
#: the evidence falls below this.  Read by ``route_after_verify``.
CLAIM_GROUNDEDNESS_RETRY_BELOW: float = 0.5

#: The DAG path synthesizes an answer only when task coverage (tasks with
#: non-empty evidence / total tasks) reaches this.  Below it — with the
#: retry budget exhausted — the graph abstains instead of guessing.
#: Read by ``evidence_sufficiency_node`` (both the synthesize floor and the
#: abstain ceiling are this one constant).
EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST: float = 0.5

#: Share of rubric-sufficient tasks required for aggregate sufficiency
#: (``aggregate_verdicts`` default).
SUFFICIENT_TASK_RATIO: float = 0.5

#: Per-signal rubric floors for :class:`SufficiencyAssessor` — a signal
#: passes at ``>=`` its threshold (contradiction/temporal pass at ``<=``,
#: i.e. a zero-tolerance ``0.0`` ceiling on conflict ratios).
SUFFICIENCY_SIGNAL_THRESHOLDS: dict[str, float] = {
    "coverage": 0.2,  # >= 1 solid chunk (narrow subquestions need one good provision)
    "relevance": 0.35,  # median retrieval score (reranker-normalized floor)
    "authority": 0.5,  # any statute + no low-authority-only evidence
    "specificity": 0.6,  # section-stamped or numeric evidence share
    "completeness": 1.0,  # all contract required fields retrievable
    "contradiction": 0.0,  # conflict ratio must stay below this
    "temporal": 0.0,  # temporal-restriction conflict ratio below this
}

"""Gold decomposition dataset (Phase 4, plan item 19).

Hand-authored expectations for :class:`app.rag.evaluation.benchmark.DecompositionBenchmark`.
Each entry pairs a query with the *ideal* decomposition under the V2
architecture (EvidenceTask taxonomy + DAG semantics):

- ``gold_task_kinds``: the evidence-requirement kinds an ideal decomposition
  should produce (order-insensitive; compared as a multiset).
- ``gold_dependencies``: which kinds depend on which, keyed by kind.  When a
  kind appears twice, dependencies are compared positionally within the kind.
- ``gold_entities``: entities the tasks should scope to (informational, used
  for evidence-level matching later; not scored by the benchmark yet).

Expectations are calibrated against the deterministic planner's *design*
intent, not its current output — entries where the planner under- or
over-decomposes are exactly the signal the benchmark exists to surface
(e.g. the multi-requirement query below, which currently drops the penalty
task).

Extend this list as new query families are supported; keep it pure-Python so
it stays versioned, typed, and importable without I/O.
"""

from __future__ import annotations

from typing import Any

GoldEntry = dict[str, Any]

#: Queries whose *ideal* decomposition is known, with expected task kinds,
#: dependency edges (kind -> kinds it depends on), and scoping entities.
GOLD_DECOMPOSITION: list[GoldEntry] = [
    {
        "query": "What is the penalty for late filing of an annual return under the FSS Act?",
        "query_class": "penalty_lookup",
        "domain": "food_safety",
        "gold_task_kinds": ["provision", "penalty"],
        "gold_dependencies": {"penalty": ["provision"], "provision": []},
        "gold_entities": ["annual return", "FSS Act", "penalty"],
    },
    {
        "query": (
            "What are the penalties for late filing of annual return under the "
            "FSS Act, and what exceptions exist?"
        ),
        "query_class": "multi_requirement",
        "domain": "food_safety",
        # Two distinct requirements (penalty AND exception) + the governing
        # provision — the planner currently under-decomposes this to
        # provision + exception, dropping the penalty task.
        "gold_task_kinds": ["provision", "penalty", "exception"],
        "gold_dependencies": {
            "provision": [],
            "penalty": ["provision"],
            "exception": ["provision"],
        },
        "gold_entities": ["annual return", "FSS Act", "penalty", "exception"],
    },
    {
        "query": (
            "Compare the licensing requirements for small food businesses and "
            "large food manufacturers."
        ),
        "query_class": "comparative",
        "domain": "food_safety",
        # A comparative query decomposes into one atomic task per side, with
        # no dependency between them (parallel retrieval).  The planner
        # currently collapses this to a single provision task.
        "gold_task_kinds": ["condition", "condition"],
        "gold_dependencies": {"condition": []},
        "gold_entities": ["licensing", "small food business", "food manufacturer"],
    },
    {
        "query": "Was Section 12 of the FSS Act amended after 2020?",
        "query_class": "temporal_lookup",
        "domain": "food_safety",
        "gold_task_kinds": ["provision"],
        "gold_dependencies": {"provision": []},
        "gold_entities": ["Section 12", "FSS Act"],
        "gold_temporal_scope": "after 2020",
    },
    {
        "query": "What is Section 12?",
        "query_class": "direct_lookup",
        "domain": "food_safety",
        "gold_task_kinds": ["provision"],
        "gold_dependencies": {"provision": []},
        "gold_entities": ["Section 12"],
    },
]

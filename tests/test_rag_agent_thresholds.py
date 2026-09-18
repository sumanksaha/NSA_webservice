"""Single-source-of-truth tests for agent routing/gating thresholds.

Every numeric gate the graph routes on is defined once in
``app.rag.agent.thresholds``; the historic names in ``nodes`` / ``graph`` /
``sufficiency`` are aliases.  These tests pin the aliasing so a future edit
can't silently fork a copy.
"""

from __future__ import annotations


def test_threshold_aliases_match_canonical():
    from app.rag.agent import thresholds as t
    from app.rag.agent.graph import (
        CLAIM_GROUNDEDNESS_THRESHOLD as GRAPH_CLAIM,
    )
    from app.rag.agent.graph import (
        GROUNDEDNESS_THRESHOLD as GRAPH_GROUNDEDNESS,
    )
    from app.rag.agent.nodes import GROUNDEDNESS_THRESHOLD as NODE_GROUNDEDNESS
    from app.rag.agent.nodes.common import _COVERAGE_FLOOR
    from app.rag.agent.sufficiency import (
        CLAIM_GROUNDEDNESS_THRESHOLD as SUF_CLAIM,
    )
    from app.rag.agent.sufficiency import (
        THRESHOLDS,
    )

    assert NODE_GROUNDEDNESS == GRAPH_GROUNDEDNESS == t.GROUNDEDNESS_RETRY_BELOW == 0.7
    assert SUF_CLAIM == GRAPH_CLAIM == t.CLAIM_GROUNDEDNESS_RETRY_BELOW == 0.5
    assert THRESHOLDS is t.SUFFICIENCY_SIGNAL_THRESHOLDS
    assert _COVERAGE_FLOOR == t.EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST == 0.5


def test_aggregate_default_ratio_is_canonical():
    import inspect

    from app.rag.agent import thresholds as t
    from app.rag.agent.sufficiency import aggregate_verdicts

    default = inspect.signature(aggregate_verdicts).parameters["min_sufficient_ratio"].default
    assert default == t.SUFFICIENT_TASK_RATIO == 0.5

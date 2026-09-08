"""2.3 — Adaptive Retrieval Strategy (Intelligence Layer).

Maps query types to retrieval tool combinations. Replaces the
"always run full pipeline" approach with goal-directed retrieval.

ponytail: minimal version — deterministic strategy selection.
Upgrade path: learned retrieval policy if benchmark shows gains.
"""

from __future__ import annotations

from enum import StrEnum


class RetrievalTool(StrEnum):
    IDENTIFIER = "identifier"
    DENSE = "dense"
    BM25 = "bm25"
    KG = "kg"
    TEMPORAL = "temporal"
    CROSS_REF = "cross_ref"
    CASE_LAW = "case_law"


# Strategy: which tools to use per query type.
RETRIEVAL_STRATEGIES: dict[str, list[RetrievalTool]] = {
    "section_lookup": [RetrievalTool.IDENTIFIER, RetrievalTool.BM25],
    "identification": [RetrievalTool.IDENTIFIER, RetrievalTool.BM25],
    "lookup": [RetrievalTool.IDENTIFIER, RetrievalTool.DENSE],
    "definition": [RetrievalTool.DENSE, RetrievalTool.CROSS_REF],
    "prohibition": [RetrievalTool.DENSE, RetrievalTool.BM25, RetrievalTool.KG],
    "duty": [RetrievalTool.DENSE, RetrievalTool.BM25, RetrievalTool.KG],
    "right": [RetrievalTool.DENSE, RetrievalTool.BM25],
    "power": [RetrievalTool.IDENTIFIER, RetrievalTool.DENSE],
    "penalty": [RetrievalTool.IDENTIFIER, RetrievalTool.DENSE, RetrievalTool.KG],
    "exception": [RetrievalTool.DENSE, RetrievalTool.KG, RetrievalTool.CROSS_REF],
    "procedure": [RetrievalTool.DENSE, RetrievalTool.BM25],
    "applicability": [RetrievalTool.IDENTIFIER, RetrievalTool.DENSE, RetrievalTool.TEMPORAL],
    "comparison": [RetrievalTool.DENSE, RetrievalTool.BM25, RetrievalTool.KG],
    "temporal": [RetrievalTool.TEMPORAL, RetrievalTool.KG],
    "jurisdiction": [RetrievalTool.IDENTIFIER, RetrievalTool.DENSE, RetrievalTool.KG],
    "cross_reference": [RetrievalTool.IDENTIFIER, RetrievalTool.KG, RetrievalTool.CROSS_REF],
    "case_law": [RetrievalTool.CASE_LAW, RetrievalTool.DENSE],
    "multi_hop": [RetrievalTool.IDENTIFIER, RetrievalTool.DENSE, RetrievalTool.KG],
    "fact_pattern": [RetrievalTool.DENSE, RetrievalTool.BM25],
    "compliance_assessment": [RetrievalTool.DENSE, RetrievalTool.BM25, RetrievalTool.KG],
}


def get_strategy(query_type: str) -> list[RetrievalTool]:
    """Return the retrieval tools for a given query type."""
    return RETRIEVAL_STRATEGIES.get(query_type, [RetrievalTool.DENSE, RetrievalTool.BM25])

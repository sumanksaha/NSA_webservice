"""2.5 — Evidence Coverage Optimizer (Intelligence Layer).

Promotes evidence selection from optional enrichment to central generation-stage component.
Maximizes coverage of required evidence types rather than just relevance.

ponytail: deterministic coverage optimization.
Upgrade path: ML-based evidence selection if needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class EvidenceCoverageOptimizer:
    """Optimizes evidence selection to maximize coverage of evidence requirements."""

    def __init__(self) -> None:
        pass

    def optimize(
        self,
        query: str,
        subquestions: list[str],
        evidence_requirements: list[str],
        chunks: list[dict],
    ) -> list[dict]:
        """Optimize evidence selection to cover all requirements."""
        if not chunks:
            return []

        # Step 1: Classify each chunk by evidence type coverage
        chunk_info: list[dict] = []
        for chunk in chunks:
            chunk_evidence = self._extract_evidence_types(chunk)
            coverage_score = self._coverage_score(chunk_evidence, evidence_requirements)
            relevance_score = self._relevance_score(chunk, query)
            priority = self._calculate_priority(chunk, query, evidence_requirements, chunk_evidence)
            chunk_info.append({
                "chunk_id": chunk.get("chunk_id", "unknown"),
                "section": chunk.get("section"),
                "coverage_score": coverage_score,
                "relevance_score": relevance_score,
                "priority": priority,
                "evidence_types": chunk_evidence,
                "chunk": chunk,
            })

        # Step 2: Sort by priority (high to low)
        chunk_info.sort(key=lambda x: x["priority"], reverse=True)
        return [info["chunk"] for info in chunk_info]

    def _extract_evidence_types(self, chunk: dict) -> list[str]:
        """Extract evidence types from chunk text."""
        types: set[str] = set()
        chunk_text = chunk.get("text", "").lower()

        if any(t in chunk_text for t in ["section", "article", "provision"]):
            types.add("PROVISION")
        if any(t in chunk_text for t in ["penalty", "fine", "punishment"]):
            types.add("PENALTY_PROVISION")
        if any(t in chunk_text for t in ["authority", "enforcement", "power"]):
            types.add("AUTHORITY_PROVISION")
        if any(t in chunk_text for t in ["definition", "means", "refers to"]):
            types.add("DEFINITION")
        if any(t in chunk_text for t in ["exception", "unless", "except"]):
            types.add("EXCEPTION")
        if any(t in chunk_text for t in ["reference", "see also", "see"]):
            types.add("CROSS_REFERENCE")
        if any(t in chunk_text for t in ["case", "precedent", "court"]):
            types.add("CASE_LAW")
        if any(t in chunk_text for t in ["temporal", "before", "after", "historical"]):
            types.add("TEMPORAL")
        if any(t in chunk_text for t in ["jurisdiction", "state", "district"]):
            types.add("JURISDICTION")

        return list(types)

    def _coverage_score(self, chunk_evidence: list[str], requirements: list[str]) -> float:
        """Calculate coverage score (0-1) based on overlap."""
        if not chunk_evidence or not requirements:
            return 0.0
        covered = sum(1 for req in requirements if req in chunk_evidence)
        return min(1.0, covered / len(requirements))

    def _relevance_score(self, chunk: dict, query: str) -> float:
        """Simple relevance score based on query-chunk overlap."""
        chunk_text = chunk.get("text", "").lower()
        overlap_words = [w for w in query.lower().split() if w in chunk_text]
        return min(1.0, len(overlap_words) * 0.1)

    def _calculate_priority(
        self,
        chunk: dict,
        query: str,
        requirements: list[str],
        chunk_evidence: list[str],
    ) -> float:
        """Calculate priority score for a chunk."""
        coverage_score = self._coverage_score(chunk_evidence, requirements)
        relevance_score = self._relevance_score(chunk, query)
        types_covered = len(set(chunk_evidence))
        types_needed = len(set(requirements))
        diversity_score = min(1.0, types_covered / max(1, types_needed))
        return coverage_score * 0.7 + relevance_score * 0.3 + diversity_score * 0.2

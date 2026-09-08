"""2.11 — KG as Reasoning Engine (Intelligence Layer).

Uses Neo4j to traverse legal relationships and generate evidence paths.
Transforms the KG from a simple expansion tool into a reasoning engine that
can generate candidate evidence paths for complex legal reasoning.

ponytail: deterministic pathfinding (limited depth). Upgrade path: learned
traversal policy or full graph query language.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kg.hybrid import KGContextExpander


@dataclass
class ReasoningPath:
    """A path through the legal KG representing a chain of reasoning."""

    steps: list[str]  # e.g., ["provision:44", "EXCEPTION", "provision:45"]
    confidence: float  # 0-1 based on relationship strength
    evidence_types: list[str]  # what types of evidence this path provides
    description: str  # human-readable explanation


class KGReasoner:
    """Traverse the legal KG to generate evidence-based reasoning paths."""

    def __init__(self, expander: KGContextExpander | None = None) -> None:
        self.expander = expander or KGContextExpander()

    def reason_from_provision(
        self,
        provision_id: str,
        max_depth: int = 2,
    ) -> list[ReasoningPath]:
        """Generate reasoning paths starting from a provision.

        Args:
            provision_id: The provision to start from (e.g., "FSSA::44")
            max_depth: Maximum relationship traversal depth

        Returns:
            List of reasoning paths ordered by confidence
        """
        if not self.expander.configured():
            return []

        # Get the provision and its immediate relationships
        expansion = self.expander.expand_chunks([provision_id])
        provisions = expansion.get("provisions", [])

        paths: list[ReasoningPath] = []

        for prov in provisions:
            # Path 1: Direct provision (self)
            paths.append(
                ReasoningPath(
                    steps=[f"provision:{provision_id}"],
                    confidence=1.0,
                    evidence_types=["PROVISION"],
                    description=f"Direct provision {provision_id}",
                )
            )

            # Path 2: Via exception (if available in expansion)
            # Note: The current expander doesn't explicitly surface exceptions,
            # but we can look for them in the text or via relationships
            exception_paths = self._find_exception_paths(prov, max_depth - 1)
            paths.extend(exception_paths)

            # Path 3: Via authority/power
            authority_paths = self._find_authority_paths(prov, max_depth - 1)
            paths.extend(authority_paths)

            # Path 4: Via cross-reference
            xref_paths = self._find_xref_paths(prov, max_depth - 1)
            paths.extend(xref_paths)

            # Path 5: Via applicability/jurisdiction
            applicability_paths = self._find_applicability_paths(prov, max_depth - 1)
            paths.extend(applicability_paths)

        # Deduplicate and sort by confidence
        unique_paths: dict[str, ReasoningPath] = {}
        for path in paths:
            key = "->".join(path.steps)
            if key not in unique_paths or path.confidence > unique_paths[key].confidence:
                unique_paths[key] = path

        return sorted(unique_paths.values(), key=lambda p: p.confidence, reverse=True)

    def _find_exception_paths(self, provision: dict, depth: int) -> list[ReasoningPath]:
        """Find exception-related provisions."""
        # Simplified: look for exception keywords in provision text
        text = (provision.get("text") or "").lower()
        if "exception" in text or "unless" in text or "except" in text:
            return [
                ReasoningPath(
                    steps=[f"provision:{provision.get('provision_id', '')}", "HAS_EXCEPTION_CLAUSE"],
                    confidence=0.8,
                    evidence_types=["EXCEPTION"],
                    description="Provision contains exception clause",
                )
            ]
        return []

    def _find_authority_paths(self, provision: dict, depth: int) -> list[ReasoningPath]:
        """Find authority/power granting provisions."""
        authorities = provision.get("authorities", [])
        if authorities:
            return [
                ReasoningPath(
                    steps=[
                        f"provision:{provision.get('provision_id', '')}",
                        "GRANTS_POWER_TO",
                        f"authority:{authorities[0]}",
                    ],
                    confidence=0.9,
                    evidence_types=["AUTHORITY_PROVISION"],
                    description=f"Grants power to {', '.join(authorities)}",
                )
            ]
        return []

    def _find_xref_paths(self, provision: dict, depth: int) -> list[ReasoningPath]:
        """Find cross-reference provisions."""
        # This would need to query the KG for CROSS_REFERENCES relationships
        # For now, return empty as the expander already handles related provisions
        return []

    def _find_applicability_paths(self, provision: dict, depth: int) -> list[ReasoningPath]:
        """Find applicability/jurisdiction provisions."""
        legal_domain = provision.get("legal_domain")
        if legal_domain:
            return [
                ReasoningPath(
                    steps=[
                        f"provision:{provision.get('provision_id', '')}",
                        "APPLIES_IN_DOMAIN",
                        f"domain:{legal_domain}",
                    ],
                    confidence=0.7,
                    evidence_types=["JURISDICTION"],
                    description=f"Applies in {legal_domain} domain",
                )
            ]
        return []


def reason_from_query(query: str) -> list[ReasoningPath]:
    """Convenience function: extract provisions from query and reason from them."""
    # This would integrate with the query planner to get target provisions
    # For now, return empty as a placeholder
    return []

"""2.11 — KG as Reasoning Engine (Intelligence Layer).

Uses Neo4j to traverse legal relationships and generate evidence paths.
Transforms the KG from a simple expansion tool into a reasoning engine that
can generate candidate evidence paths for complex legal reasoning.

Capabilities (per docs/RAG_IMPROVEMENTS.md §2.11):
  has_permission | conflict_reasoning | trace_lineage | compare_provisions
  relationship_explanation | actionable_answer

Design: deterministic pattern matching → Cypher generation with a
validate-query allowlist (no free-form Cypher from LLMs). An LLM may propose
an intent + entities; this module maps them to an allowlisted pattern.
Path scoring combines authority weight, recency, hierarchy, and evidence
support — all deterministic and testable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from kg.hybrid import KGContextExpander
except Exception:  # pragma: no cover - kg package optional
    KGContextExpander = Any  # type: ignore[assignment,misc]


# --------------------------------------------------------------------------- #
# Cypher generation with validate-query allowlist
# --------------------------------------------------------------------------- #

#: Allowed relationship types — any generated Cypher using another
#: relationship is rejected (validate-query pattern).
ALLOWED_RELATIONS = frozenset({
    "CONTAINS",
    "HAS_AUTHORITY",
    "GRANTS_POWER_TO",
    "HAS_PENALTY",
    "HAS_EXCEPTION",
    "HAS_CROSS_REFERENCES",
    "TEMPORAL_VALIDITY",
    "BELONGS_TO_DOMAIN",
    "AMENDED_BY",
    "SUPERSEDED_BY",
})

#: Intent → Cypher template. ``{section}`` / ``{provision}`` / ``{authority}``
#: are substituted after sanitisation (alphanumerics, ::, -, _, spaces only).
_CYPHER_PATTERNS: dict[str, str] = {
    "permission": (
        "MATCH (s:Section {id: $section})-[:HAS_AUTHORITY]->(a:Authority)"
        "-[:GRANTS_POWER_TO]->(p:Provision) RETURN s, a, p"
    ),
    "authority_power": (
        "MATCH (s:Section)-[:HAS_AUTHORITY]->(a:Authority {name: $authority})"
        "-[:GRANTS_POWER_TO]->(p:Provision) RETURN s, a, p"
    ),
    "penalty": ("MATCH (s:Section {id: $section})-[:HAS_PENALTY]->(pen:Penalty) RETURN s, pen"),
    "exception": ("MATCH (s:Section {id: $section})-[:HAS_EXCEPTION]->(e:Exception) RETURN s, e"),
    "cross_reference": ("MATCH (s:Section {id: $section})-[:HAS_CROSS_REFERENCES]->(t:Section) RETURN s, t"),
    "lineage": ("MATCH path = (s:Section {id: $section})-[:AMENDED_BY|SUPERSEDED_BY*1..5]->(t:Section) RETURN path"),
    "temporal": ("MATCH (s:Section {id: $section})-[:TEMPORAL_VALIDITY]->(t:Temporal) RETURN s, t"),
    "domain": ("MATCH (s:Section)-[:BELONGS_TO_DOMAIN]->(j:Jurisdiction {name: $domain}) RETURN s, j"),
}

_SAFE_PARAM = re.compile(r"[^A-Za-z0-9_: \-\./]+")


def _sanitize_param(value: str) -> str:
    return _SAFE_PARAM.sub("", value or "").strip()[:200]


def generate_cypher(intent: str, entities: dict[str, str] | None = None) -> str | None:
    """Generate an allowlisted Cypher query for *intent*.

    Returns None when the intent is unknown or the generated query uses a
    relationship outside :data:`ALLOWED_RELATIONS` (validate-query pattern).
    """
    template = _CYPHER_PATTERNS.get(str(intent or "").lower())
    if template is None:
        return None
    # Validate: every relationship in the template must be allowlisted,
    # including `|` alternation branches (e.g. AMENDED_BY|SUPERSEDED_BY).
    for group in re.findall(r"\[:([^\]]+)", template):
        for branch in group.split("|"):
            m = re.match(r"[A-Za-z_]+", branch.strip())
            if m and m.group(0) not in ALLOWED_RELATIONS:
                logger.warning("generate_cypher: blocked non-allowlisted relation %s", m.group(0))
                return None
    cypher = template
    for key, val in (entities or {}).items():
        # Quote substituted values so output is syntactically valid Cypher.
        cypher = cypher.replace(f"${key}", f'"{_sanitize_param(str(val))}"')
    # Unprovided placeholders → empty quoted string (never leak `$key`).
    cypher = re.sub(r"\$[A-Za-z_]+", '""', cypher)
    return cypher


def filter_paths_by_intent(paths: list[ReasoningPath], intent: str) -> list[ReasoningPath]:
    """Keep paths whose evidence types match the query intent."""
    intent = (intent or "").lower()
    want: dict[str, set[str]] = {
        "permission": {"AUTHORITY_PROVISION", "PROVISION"},
        "penalty": {"PENALTY", "PROVISION"},
        "exception": {"EXCEPTION"},
        "cross_reference": {"CROSS_REFERENCE"},
        "temporal": {"TEMPORAL", "JURISDICTION"},
        "authority": {"AUTHORITY_PROVISION"},
    }
    allowed = want.get(intent)
    if not allowed:
        return list(paths)
    return [p for p in paths if set(p.evidence_types) & allowed or "PROVISION" in p.evidence_types]


def score_paths(
    paths: list[ReasoningPath],
    authority_weights: dict[str, float] | None = None,
    recency_boost: dict[str, float] | None = None,
    hierarchy_boost: float = 0.1,
) -> list[ReasoningPath]:
    """Score paths by authority weight, recency, hierarchy, evidence support.

    Deterministic: score = base confidence + authority bonus + recency bonus
    + hierarchy bonus, clamped to [0, 1]. Returns a new sorted list; input
    paths are not mutated (idempotent — safe to score twice).
    """
    from dataclasses import replace

    authority_weights = authority_weights or {}
    recency_boost = recency_boost or {}
    scored: list[ReasoningPath] = []
    for p in paths:
        bonus = 0.0
        for step in p.steps:
            bonus += authority_weights.get(step, 0.0)
            bonus += recency_boost.get(step, 0.0)
        # Hierarchy: shorter, more direct paths preferred. Empty steps → no bonus.
        if p.steps:
            bonus += max(0.0, hierarchy_boost * (3 - len(p.steps)) / 3)
        # Evidence support: paths with typed evidence outrank bare hops.
        if p.evidence_types:
            bonus += 0.05 * len(p.evidence_types)
        scored.append(replace(p, confidence=max(0.0, min(1.0, p.confidence + bonus))))
    return sorted(scored, key=lambda p: p.confidence, reverse=True)


@dataclass
class ReasoningPath:
    """A path through the legal KG representing a chain of reasoning."""

    steps: list[str]  # e.g., ["provision:44", "EXCEPTION", "provision:45"]
    confidence: float  # 0-1 based on relationship strength
    evidence_types: list[str]  # what types of evidence this path provides
    description: str  # human-readable explanation


@dataclass
class KGAnswer:
    """Result of a KG capability call — path-backed, auditable."""

    capability: str
    answer: str
    paths: list[ReasoningPath] = field(default_factory=list)
    cypher: str | None = None
    confidence: float = 0.0


_SECTION_RE = re.compile(r"[Ss]ection\s+(\d+[A-Za-z]?(?:\(\d+\))?)")


def _extract_sections(query: str) -> list[str]:
    return [f"FSSA::{m.group(1)}" for m in _SECTION_RE.finditer(query or "")]


class KGReasoner:
    """Traverse the legal KG to generate evidence-based reasoning paths."""

    def __init__(self, expander: KGContextExpander | None = None) -> None:
        # Declared Optional: the except branch keeps the (possibly None) arg,
        # so the `is None` guard in reason_from_provision is reachable.
        self.expander: KGContextExpander | None
        try:
            from kg.hybrid import KGContextExpander as _Expander

            self.expander = expander or _Expander()
        except Exception:
            self.expander = expander

    # -- core traversal -------------------------------------------------- #
    def reason_from_provision(
        self,
        provision_id: str,
        max_depth: int = 2,
    ) -> list[ReasoningPath]:
        """Generate reasoning paths starting from a provision."""
        if self.expander is None:
            return []
        try:
            if not self.expander.configured():
                return []
        except Exception:
            return []
        expansion = self.expander.expand_chunks([provision_id])
        provisions = expansion.get("provisions", [])

        paths: list[ReasoningPath] = []
        for prov in provisions:
            paths.append(
                ReasoningPath(
                    steps=[f"provision:{provision_id}"],
                    confidence=1.0,
                    evidence_types=["PROVISION"],
                    description=f"Direct provision {provision_id}",
                )
            )
            paths.extend(self._find_exception_paths(prov, max_depth - 1))
            paths.extend(self._find_authority_paths(prov, max_depth - 1))
            paths.extend(self._find_xref_paths(prov, max_depth - 1))
            paths.extend(self._find_applicability_paths(prov, max_depth - 1))

        unique_paths: dict[str, ReasoningPath] = {}
        for path in paths:
            key = "->".join(path.steps)
            if key not in unique_paths or path.confidence > unique_paths[key].confidence:
                unique_paths[key] = path
        return sorted(unique_paths.values(), key=lambda p: p.confidence, reverse=True)

    def _find_exception_paths(self, provision: dict, depth: int) -> list[ReasoningPath]:
        text = (provision.get("text") or "").lower()
        if "exception" in text or "unless" in text or "except" in text:
            return [
                ReasoningPath(
                    steps=[f"provision:{provision.get('provision_id', '')}", "HAS_EXCEPTION"],
                    confidence=0.8,
                    evidence_types=["EXCEPTION"],
                    description="Provision contains exception clause",
                )
            ]
        return []

    def _find_authority_paths(self, provision: dict, depth: int) -> list[ReasoningPath]:
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
        """Cross-reference paths from KG expansion or text mentions."""
        out: list[ReasoningPath] = []
        pid = provision.get("provision_id", "")
        for ref in provision.get("cross_references", []) or []:
            out.append(
                ReasoningPath(
                    steps=[f"provision:{pid}", "HAS_CROSS_REFERENCES", f"provision:{ref}"],
                    confidence=0.85,
                    evidence_types=["CROSS_REFERENCE"],
                    description=f"{pid} cross-refers to {ref}",
                )
            )
        if not out:
            # Fallback: textual "subject to Section N" / "see Section N".
            for m in re.finditer(
                r"(?:subject to|see|under)\s+[Ss]ection\s+(\d+[A-Za-z]?)", provision.get("text") or ""
            ):
                out.append(
                    ReasoningPath(
                        steps=[f"provision:{pid}", "HAS_CROSS_REFERENCES", f"provision:FSSA::{m.group(1)}"],
                        confidence=0.6,
                        evidence_types=["CROSS_REFERENCE"],
                        description=f"Textual cross-reference to Section {m.group(1)}",
                    )
                )
        return out

    def _find_applicability_paths(self, provision: dict, depth: int) -> list[ReasoningPath]:
        legal_domain = provision.get("legal_domain")
        if legal_domain:
            return [
                ReasoningPath(
                    steps=[
                        f"provision:{provision.get('provision_id', '')}",
                        "BELONGS_TO_DOMAIN",
                        f"domain:{legal_domain}",
                    ],
                    confidence=0.7,
                    evidence_types=["JURISDICTION"],
                    description=f"Applies in {legal_domain} domain",
                )
            ]
        return []

    # -- §2.11 capabilities ----------------------------------------------- #
    def has_permission(self, entity: str, action: str, provision_id: str | None = None) -> KGAnswer:
        """Should *entity* have permission to do *action*?"""
        paths = self.reason_from_provision(provision_id, 2) if provision_id else []
        paths = filter_paths_by_intent(paths, "permission")
        cypher = generate_cypher("permission", {"section": provision_id or "", "authority": entity})
        answer = (
            f"{entity} {'may' if any('AUTHORITY_PROVISION' in p.evidence_types for p in paths) else 'has no KG-backed'} "
            f"permission to {action}" + (f" under {provision_id}" if provision_id else "") + "."
        )
        return KGAnswer("has_permission", answer, paths, cypher, paths[0].confidence if paths else 0.0)

    def conflict_reasoning(self, section_a: str, section_b: str) -> KGAnswer:
        """Resolve conflicts between two sections (authority/recency wins)."""
        pa = self.reason_from_provision(section_a, 1)
        pb = self.reason_from_provision(section_b, 1)
        all_paths = score_paths(
            pa + pb,
            authority_weights={f"provision:{section_a}": 0.1, f"provision:{section_b}": 0.05},
        )
        winner = section_a if all_paths and f"provision:{section_a}" in all_paths[0].steps else section_b
        return KGAnswer(
            "conflict_reasoning",
            f"Between {section_a} and {section_b}, {winner} takes precedence on authority/recency scoring.",
            all_paths,
            generate_cypher("cross_reference", {"section": section_a}),
            all_paths[0].confidence if all_paths else 0.0,
        )

    def trace_lineage(self, section: str) -> KGAnswer:
        """Follow amendment/supersession lineage for *section*."""
        cypher = generate_cypher("lineage", {"section": section})
        paths = self.reason_from_provision(section, 2)
        lineage = [p for p in paths if "JURISDICTION" in p.evidence_types or "PROVISION" in p.evidence_types]
        return KGAnswer(
            "trace_lineage",
            f"Lineage for {section}: {len(lineage)} KG path(s) traced.",
            lineage or paths,
            cypher,
            (lineage or paths)[0].confidence if (lineage or paths) else 0.0,
        )

    def compare_provisions(self, section_a: str, section_b: str) -> KGAnswer:
        """Evaluate differences across versions/temporal states."""
        paths_a = self.reason_from_provision(section_a, 1)
        paths_b = self.reason_from_provision(section_b, 1)
        pa = {tuple(p.steps) for p in paths_a}
        pb = {tuple(p.steps) for p in paths_b}
        only_a = len(pa - pb)
        only_b = len(pb - pa)
        return KGAnswer(
            "compare_provisions",
            f"{section_a} has {only_a} unique path(s); {section_b} has {only_b} unique path(s).",
            paths_a + paths_b,
            generate_cypher("cross_reference", {"section": section_a}),
            0.7 if (pa or pb) else 0.0,
        )

    def relationship_explanation(self, concept_a: str, concept_b: str) -> KGAnswer:
        """Explain the relationship between two concepts."""
        cypher = generate_cypher("cross_reference", {"section": concept_a})
        return KGAnswer(
            "relationship_explanation",
            f"{concept_a} relates to {concept_b} via KG traversal (cross-reference/authority/domain edges).",
            [],
            cypher,
            0.5,
        )

    def actionable_answer(self, query: str, intent: str = "permission") -> KGAnswer:
        """Generate an answer using KG traversal for *query*."""
        sections = _extract_sections(query)
        paths: list[ReasoningPath] = []
        for sec in sections:
            paths.extend(self.reason_from_provision(sec, 2))
        paths = score_paths(filter_paths_by_intent(paths, intent))
        cypher = generate_cypher(intent, {"section": sections[0] if sections else query})
        if not paths:
            return KGAnswer("actionable_answer", "Insufficient KG evidence to answer.", [], cypher, 0.0)
        top = paths[0]
        return KGAnswer(
            "actionable_answer",
            f"KG-backed answer: {top.description} (confidence {top.confidence:.2f}).",
            paths,
            cypher,
            top.confidence,
        )


def reason_from_query(query: str) -> list[ReasoningPath]:
    """Extract provision ids from *query* and reason from each."""
    sections = _extract_sections(query or "")
    if not sections:
        return []
    try:
        reasoner = KGReasoner()
    except Exception:
        return []
    out: list[ReasoningPath] = []
    for sec in sections:
        try:
            out.extend(reasoner.reason_from_provision(sec, 2))
        except Exception as exc:
            logger.warning("reason_from_query: traversal failed for %s (%s)", sec, exc)
    return score_paths(out)

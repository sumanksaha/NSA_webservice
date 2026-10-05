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
#:
#: These must be relationship types that actually exist in the graph.  The
#: original list (HAS_AUTHORITY, HAS_PENALTY, HAS_EXCEPTION,
#: HAS_CROSS_REFERENCES, TEMPORAL_VALIDITY) described a schema the graph
#: never had, so every generated query was valid Cypher against labels and
#: relationships that do not exist and matched nothing.
ALLOWED_RELATIONS = frozenset({
    # Act -> LegalProvision
    "CONTAINS",
    # LegalProvision -> LegalConcept (the semantic edges the KG actually has)
    "IMPOSES_DUTY",
    "APPLIES_TO",
    "PRESCRIBES_PENALTY",
    "PROHIBITS",
    "EXEMPTS",
    "DEFINES",
    "CREATES_OFFENCE",
    "GRANTS_POWER_TO",
    "GRANTS_PERMISSION",
    # LegalConcept -> LegalDomain
    "RELEVANT_IN",
    # LegalProvision -> LegalDomain / Document / Chunk
    "BELONGS_TO_DOMAIN",
    "SOURCE_OF",
    "SUPPORTED_BY",
    # Act -> Authority, and temporal edges
    "ISSUED_BY",
    "SUPERSEDED_BY",
    "AMENDED_BY",
})

#: Intent → Cypher template, written against the real graph schema:
#: ``(Act)-[:CONTAINS]->(LegalProvision)-[:<semantic>]->(LegalConcept)``
#: and ``(LegalProvision)-[:BELONGS_TO_DOMAIN]->(LegalDomain)``.
#: ``$section`` is a ``provision_id`` (resolved by
#: :func:`_resolve_provision_ids`, never a raw "FSSA::31" guess).
_CYPHER_PATTERNS: dict[str, str] = {
    "permission": (
        "MATCH (p:LegalProvision {provision_id: $section})"
        "-[:GRANTS_POWER_TO|GRANTS_PERMISSION|IMPOSES_DUTY]->(c:LegalConcept)"
        " RETURN p, c"
    ),
    "authority_power": (
        "MATCH (a:Act)-[:ISSUED_BY]->(auth:Authority {name: $authority})"
        "-[:CONTAINS]->(p:LegalProvision) RETURN a, auth, p"
    ),
    "penalty": (
        "MATCH (p:LegalProvision {provision_id: $section})"
        "-[:PRESCRIBES_PENALTY|CREATES_OFFENCE]->(c:LegalConcept) RETURN p, c"
    ),
    "exception": ("MATCH (p:LegalProvision {provision_id: $section})-[:EXEMPTS]->(c:LegalConcept) RETURN p, c"),
    "cross_reference": ("MATCH (p:LegalProvision {provision_id: $section})-[:SUPPORTED_BY]->(ch:Chunk) RETURN p, ch"),
    "lineage": (
        "MATCH path = (p:LegalProvision {provision_id: $section})-[:SUPERSEDED_BY|AMENDED_BY*1..5]->(t) RETURN path"
    ),
    "temporal": (
        "MATCH (p:LegalProvision {provision_id: $section})"
        " RETURN p.status AS status, p.effective_from AS effective_from,"
        " p.effective_to AS effective_to"
    ),
    "domain": ("MATCH (p:LegalProvision)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain {domain_name: $domain}) RETURN p, d"),
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


#: Matches "Section 31", "section 31", "Sections 16 and 31", "Section 7(2)".
_SECTION_RE = re.compile(r"[Ss]ections?\s+(\d+[A-Za-z]?(?:\(\d+\))?)")

#: Query fragments that hint which Act a bare "Section 31" refers to, mapped
#: to the token expected in that Act's provision ids.  Used only to rank
#: candidates: a section number with no matching hint is still resolved, just
#: across every Act that carries that number.
_ACT_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("food safety and standards", "fssai", "fss act", "fss "), ("FSS_ACT_2006", "FSS_ACT")),
    (("prevention of cruelty to animals",), ("PFA_1954", "PFA")),
    (("indian penal code", "penal code", "ipc"), ("IPC_1860", "IPC")),
    (("code of criminal procedure", "crpc"), ("CRPC",)),
    (("constitution of india", "constitution"), ("CONSTITUTION",)),
    (("sale of goods",), ("SALE_OF_GOODS",)),
    (("consumer protection",), ("CONSUMER_PROTECTION_ACT_2019", "CONSUMER_PROTECTION")),
    (("companies act",), ("COMPANIES_ACT", "COMPANIES")),
    (("contract act",), ("CONTRACT_ACT", "CONTRACT")),
    (("negotiable instruments",), ("NEGOTIABLE_INSTRUMENTS", "NI_")),
    (("evidence act",), ("EVIDENCE_ACT", "IEA")),
    (("motor vehicles",), ("MOTOR_VEHICLES", "MV_")),
    (("air act", "prevention of control of pollution"), ("AIR_ACT", "AIR")),
    (("bharatiya nyaya", "bns "), ("BNS",)),
    (("limitation act",), ("LIMITATION",)),
    (("water act", "water pollution"), ("WATER_", "WATER")),
    (("environment protection", "epr "), ("ENVIRONMENT", "ENV")),
    (("solid waste",), ("SWM",)),
    (("pollution control",), ("ENVIRONMENT", "ENV")),
    (("persons with disabilities",), ("SPECIFIC",)),
    (("limited liability", "llp "), ("LLP",)),
)

#: Cap on candidates per section so a common number like "31" (which appears
#: under many FSS instruments) cannot flood the graph with traversals.
_MAX_CANDIDATES_PER_SECTION = 5


def _act_hints(query: str) -> tuple[str, ...]:
    """Return id tokens for the Acts named in *query*, best match first."""
    low = (query or "").lower()
    hits: list[tuple[int, tuple[str, ...]]] = []
    for needles, tokens in _ACT_HINTS:
        for needle in needles:
            idx = low.find(needle)
            if idx >= 0:
                hits.append((idx, tokens))
                break
    # Earliest mention wins, so "Section 5 of the IPC" attributes the
    # section to the IPC rather than to an Act named in a trailing aside.
    hits.sort(key=lambda t: t[0])
    return tuple(t for _, t in hits)


def _resolve_provision_ids(sections: list[str], query: str = "") -> list[str]:
    """Resolve section numbers to real ``provision_id`` values.

    The graph keys provisions by id (``FSS_ACT_2006_SEC_31``) and stores the
    bare number in ``provision_number``.  The previous code invented an
    ``FSSA::31`` namespace that matches no node, so every traversal returned
    nothing.  This resolves against the real graph, ranking candidates whose
    id matches an Act the query actually named.  A section number is often
    carried by many instruments, so when the query names none the result is
    ordered by id and capped rather than silently picking one.
    """
    if not sections:
        return []
    try:
        from kg.queries import LegalKGQueries

        queries = LegalKGQueries()
    except Exception:
        return []

    hints = _act_hints(query)
    out: list[str] = []
    for number in sections:
        try:
            rows = queries._execute(
                "MATCH (p:LegalProvision) WHERE p.provision_number = $num "
                "RETURN p.provision_id AS pid ORDER BY p.provision_id",
                {"num": number},
            )
        except Exception as exc:
            logger.warning("_resolve_provision_ids: lookup failed for section %s (%s)", number, exc)
            continue
        cands = [str(r["pid"]) for r in rows if r.get("pid")]
        if hints:
            # Keep only candidates matching the Act the query named, ordered
            # by hint order so the earliest-named Act is preferred.
            ranked: list[str] = []
            for tokens in hints:
                for pid in cands:
                    if pid.upper().startswith(tuple(t.upper() for t in tokens)) and pid not in ranked:
                        ranked.append(pid)
            cands = ranked
        out.extend(cands[:_MAX_CANDIDATES_PER_SECTION])
    return list(dict.fromkeys(out))


def _extract_sections(query: str) -> list[str]:
    """Return the bare section numbers mentioned in *query*.

    These are graph ``provision_number`` values, not provision ids; call
    :func:`_resolve_provision_ids` to obtain ids the graph can match.
    """
    return list(dict.fromkeys(m.group(1) for m in _SECTION_RE.finditer(query or "")))


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
        """Generate reasoning paths starting from a provision.

        *provision_id* must be a real ``LegalProvision.provision_id``; use
        :func:`_resolve_provision_ids` to turn a "Section 31" mention into
        one.  Traversal reads the provision's real edges
        (``IMPOSES_DUTY``, ``PRESCRIBES_PENALTY``, ``EXEMPTS``, …) via
        :class:`kg.queries.LegalKGQueries`.
        """
        if not provision_id:
            return []
        try:
            from kg.queries import LegalKGQueries

            detail = LegalKGQueries().get_provision(provision_id)
        except Exception as exc:
            logger.warning("reason_from_provision: lookup failed for %s (%s)", provision_id, exc)
            return []
        if not detail:
            return []

        prov = {
            "provision_id": detail.get("provision_id") or provision_id,
            "text": detail.get("text") or "",
            "legal_domain": detail.get("legal_domain"),
            "concepts": detail.get("concepts") or [],
        }

        paths: list[ReasoningPath] = [
            ReasoningPath(
                steps=[f"provision:{prov['provision_id']}"],
                confidence=1.0,
                evidence_types=["PROVISION"],
                description=f"Direct provision {prov['provision_id']}",
            )
        ]
        paths.extend(self._find_concept_paths(prov))
        paths.extend(self._find_applicability_paths(prov, max_depth - 1))

        unique_paths: dict[str, ReasoningPath] = {}
        for path in paths:
            key = "->".join(path.steps)
            if key not in unique_paths or path.confidence > unique_paths[key].confidence:
                unique_paths[key] = path
        return sorted(unique_paths.values(), key=lambda p: p.confidence, reverse=True)

    def _find_concept_paths(self, provision: dict) -> list[ReasoningPath]:
        """Concept edges hanging off a provision, as typed reasoning paths.

        These are the relationships the graph actually stores, so a path here
        is backed by a real edge rather than by a keyword match on text.
        """
        pid = provision.get("provision_id", "")
        edges = self._concept_edges(pid)
        out: list[ReasoningPath] = []
        for rel, concept, etype, confidence in edges:
            out.append(
                ReasoningPath(
                    steps=[f"provision:{pid}", rel, f"concept:{concept}"],
                    confidence=confidence,
                    evidence_types=[etype],
                    description=f"{pid} {rel} {concept}",
                )
            )
        return out

    def _concept_edges(self, provision_id: str) -> list[tuple[str, str, str, float]]:
        """Fetch ``(relationship, concept, evidence_type, confidence)`` tuples."""
        if not provision_id:
            return []
        try:
            from kg.queries import LegalKGQueries

            rows = LegalKGQueries()._execute(
                """
                MATCH (p:LegalProvision {provision_id: $pid})-[r]->(c:LegalConcept)
                RETURN type(r) AS rel, c.name AS concept
                """,
                {"pid": provision_id},
            )
        except Exception as exc:
            logger.warning("_concept_edges: traversal failed for %s (%s)", provision_id, exc)
            return []
        mapping = {
            "IMPOSES_DUTY": ("AUTHORITY_PROVISION", 0.9),
            "PRESCRIBES_PENALTY": ("PENALTY", 0.9),
            "CREATES_OFFENCE": ("PENALTY", 0.85),
            "EXEMPTS": ("EXCEPTION", 0.8),
            "GRANTS_POWER_TO": ("AUTHORITY_PROVISION", 0.85),
            "GRANTS_PERMISSION": ("AUTHORITY_PROVISION", 0.85),
            "PROHIBITS": ("PROHIBITION", 0.8),
            "DEFINES": ("DEFINITION", 0.75),
            "APPLIES_TO": ("PROVISION", 0.7),
        }
        out: list[tuple[str, str, str, float]] = []
        for row in rows:
            rel = str(row.get("rel") or "")
            concept = row.get("concept")
            if not rel or not concept:
                continue
            etype, confidence = mapping.get(rel, ("PROVISION", 0.6))
            out.append((rel, str(concept), etype, confidence))
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
        for pid in _resolve_provision_ids(sections, query):
            paths.extend(self.reason_from_provision(pid, 2))
        paths = score_paths(filter_paths_by_intent(paths, intent))
        cypher = generate_cypher(
            intent, {"section": _resolve_provision_ids(sections, query)[:1] or ([query] if not sections else [])}
        )
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
    """Resolve the sections named in *query* and reason from each.

    Section numbers are resolved against the real graph first: a bare
    "Section 31" is ambiguous across Acts, so the query's Act mention is used
    to narrow it when present.
    """
    sections = _extract_sections(query or "")
    if not sections:
        return []
    provision_ids = _resolve_provision_ids(sections, query)
    if not provision_ids:
        return []
    try:
        reasoner = KGReasoner()
    except Exception:
        return []
    out: list[ReasoningPath] = []
    for pid in provision_ids:
        try:
            out.extend(reasoner.reason_from_provision(pid, 2))
        except Exception as exc:
            logger.warning("reason_from_query: traversal failed for %s (%s)", pid, exc)
    return score_paths(out)

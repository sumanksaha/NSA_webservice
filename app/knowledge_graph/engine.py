"""Knowledge-graph extraction engine (plan.md Phase 14).

``build_graph_for_case`` walks a ``CaseFile`` (or ``Adjudication``) record
and all of its linked child records (Sample, Inspection, Annexure, Evidence,
Bill) — extracting **nodes** (Case, FBO, Inspector, Sample, Lab, LegalSection,
Evidence) and **directed edges** (INSPECTED_BY, SAMPLED_FROM, TESTED_AT,
VIOLATED_SECTION, SUPPORTED_BY).

The graph is returned in Cytoscape.js-compatible JSON and — when the case
is a ``case_file`` — persisted to the ``entity`` / ``relationship`` tables
so it can be queried server-side without re-extraction.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.extensions import db

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Node / edge type constants (mirror the Cytoscape.js "type" field)
# --------------------------------------------------------------------------- #

CASE = "case"
FBO = "fbo"
INSPECTOR = "inspector"
SAMPLE = "sample"
LAB = "lab"
SECTION = "section"
EVIDENCE = "evidence"
ANCILLARY = "ancillary"  # bills, annexure docs, etc.

# Directed edge types
INSPECTED_BY = "INSPECTED_BY"
SAMPLED_FROM = "SAMPLED_FROM"
TESTED_AT = "TESTED_AT"
VIOLATED_SECTION = "VIOLATED_SECTION"
SUPPORTED_BY = "SUPPORTED_BY"
REFERENCES = "REFERENCES"

# Cytoscape.js style defaults per node type
_NODE_STYLE: dict[str, dict[str, str]] = {
    CASE: {"color": "#0b3d91", "shape": "ellipse", "icon": "fa-solid fa-file-text"},
    FBO: {"color": "#0b6e4f", "shape": "ellipse", "icon": "fa-solid fa-building"},
    INSPECTOR: {"color": "#5a4fff", "shape": "ellipse", "icon": "fa-solid fa-user-shield"},
    SAMPLE: {"color": "#c77d0a", "shape": "box", "icon": "fa-solid fa-vial"},
    LAB: {"color": "#8e24aa", "shape": "box", "icon": "fa-solid fa-flask"},
    SECTION: {"color": "#b3261e", "shape": "box", "icon": "fa-solid fa-scroll"},
    EVIDENCE: {"color": "#455a64", "shape": "box", "icon": "fa-solid fa-photo-film"},
}

_EDGE_STYLE: dict[str, str] = {
    INSPECTED_BY: "#5a4fff",
    SAMPLED_FROM: "#c77d0a",
    TESTED_AT: "#8e24aa",
    VIOLATED_SECTION: "#b3261e",
    SUPPORTED_BY: "#0b6e4f",
    REFERENCES: "#455a64",
}


@dataclass
class KGNode:
    """One graph node."""

    id: str
    label: str
    type: str
    source_table: str | None = None
    source_id: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class KGEdge:
    """One directed edge."""

    source: str
    target: str
    type: str
    weight: float = 1.0
    label: str | None = None


class KnowledgeGraphEngine:
    """Extract entities and relationships for a case into a Cytoscape.js graph.

    Follows the same persistence contract as ``TimelineEngine``: only
    ``case_file`` graphs are persisted to DB (via ``Entity``/``Relationship``
    models); adjudication graphs are ephemeral.
    """

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def build_graph_for_case(self, case_id: int, case_type: str = "case_file") -> dict:
        """Build the full knowledge graph for a case.

        Args:
            case_id: Primary key of the ``CaseFile`` or ``Adjudication``.
            case_type: ``"case_file"`` or ``"adjudication"``.

        Returns:
            A dict with keys: ``case_id``, ``case_type``, ``case_number``,
            ``nodes`` (list of ``{data: {id, label, type, ...}}``), and
            ``edges`` (list of ``{data: {source, target, type, label}}``)
            in Cytoscape.js element format.
        """
        from app.shared.case_resolver import CaseResolver

        resolved = CaseResolver().resolve(case_id, kind=case_type)
        if resolved is None or resolved.record is None:
            return {
                "case_id": case_id,
                "case_type": case_type,
                "case_number": "",
                "nodes": [],
                "edges": [],
                "error": "Case not found",
            }

        record = resolved.record
        table_name = resolved.record.__tablename__
        nodes: list[KGNode] = []
        edges: list[KGEdge] = []

        # --- Core case node ---
        case_node = KGNode(
            id=f"{case_type}:{record.id}",
            label=record.case_number or f"Case #{record.id}",
            type=CASE,
            source_table=table_name,
            source_id=record.id,
            metadata={"case_number": record.case_number, "created_at": _iso(record.created_at)},
        )
        nodes.append(case_node)

        # --- FBO node ---
        fbo_name = (
            getattr(record, "manufacturer_fbo_name", None)
            or getattr(record, "manufacturer_name", None)
            or getattr(record, "fbo_name", None)
            or ""
        )
        fbo_node: KGNode | None = None
        if fbo_name:
            fbo_node = KGNode(
                id=f"fbo:{case_node.id}",
                label=fbo_name,
                type=FBO,
                metadata={
                    "fssai_license": getattr(record, "manufacturer_fssai", None)
                    or getattr(record, "fssai_license", None),
                    "address": getattr(record, "manufacturer_address", None) or getattr(record, "fbo_address", None),
                },
            )
            nodes.append(fbo_node)
            edges.append(KGEdge(source=case_node.id, target=fbo_node.id, type=SUPPORTED_BY))

        # --- Inspector (FSO) node ---
        inspector_name = (
            getattr(record, "food_safety_officer_name", None) or getattr(record, "food_safety_officer", None) or ""
        )
        if inspector_name:
            inspector_node = KGNode(
                id=f"inspector:{case_node.id}",
                label=inspector_name,
                type=INSPECTOR,
                source_table="fso",
            )
            nodes.append(inspector_node)
            edges.append(KGEdge(source=case_node.id, target=inspector_node.id, type=INSPECTED_BY))

        # --- Linked Sample node ---
        sample_id = getattr(record, "sample_id", None)
        if sample_id is not None:
            sample = db.session.get(_import_sample(), sample_id)
            if sample is not None:
                sample_node = KGNode(
                    id=f"sample:{sample.id}",
                    label=sample.sample_code or f"Sample #{sample.id}",
                    type=SAMPLE,
                    source_table="sample",
                    source_id=sample.id,
                    metadata={
                        "sample_name": sample.sample_name,
                        "collection_date": _iso(sample.collection_date),
                    },
                )
                nodes.append(sample_node)
                # Link sample to FBO if we have one, otherwise to the case node
                edge_target = fbo_node.id if fbo_node is not None else case_node.id
                edges.append(KGEdge(source=edge_target, target=sample_node.id, type=SAMPLED_FROM))

                # --- Lab node (from record's Lab_Registration_No) ---
                lab_name = getattr(record, "Lab_Registration_No", None) or ""
                if lab_name:
                    lab_node = KGNode(
                        id=f"lab:{case_node.id}",
                        label=lab_name,
                        type=LAB,
                        metadata={"registration_no": lab_name},
                    )
                    nodes.append(lab_node)
                    edges.append(KGEdge(source=sample_node.id, target=lab_node.id, type=TESTED_AT))

        # --- Legal section nodes ---
        sections_str = getattr(record, "applicable_sections", None) or ""
        section_numbers = _extract_sections(sections_str)

        # Adjudication: section_55, section_56, section_58, section_63, section_64
        if not section_numbers:
            for sec_field in ("section_55", "section_56", "section_58", "section_63", "section_64"):
                if getattr(record, sec_field, "no") == "yes":
                    section_numbers.append(sec_field.replace("section_", ""))

        # Also parse section references from problem text
        problem_text = getattr(record, "problem", None) or ""
        section_numbers.extend(_extract_sections(problem_text))

        for sec_num in _dedupe(section_numbers):
            sec_node = KGNode(
                id=f"section:{case_node.id}:{sec_num}",
                label=f"Section {sec_num}",
                type=SECTION,
                metadata={"section_number": sec_num, "act": "FSS Act, 2006"},
            )
            nodes.append(sec_node)
            edges.append(KGEdge(source=case_node.id, target=sec_node.id, type=VIOLATED_SECTION))

        # --- Evidence nodes ---
        ev_nodes, ev_edges = self._extract_evidence(case_id, case_type)
        nodes.extend(ev_nodes)
        edges.extend(ev_edges)

        # --- Annexure nodes ---
        ann_nodes, ann_edges = self._extract_annexures(case_id, case_type)
        nodes.extend(ann_nodes)
        edges.extend(ann_edges)

        # --- Bill nodes (case_file only, linked via Sample) ---
        bill_nodes, bill_edges = self._extract_bills(case_id, case_type)
        nodes.extend(bill_nodes)
        edges.extend(bill_edges)

        # Deduplicate by id (preserve order)
        unique_nodes = _dedupe_nodes(nodes)
        unique_edges = _dedupe_edges(edges)

        # Persist to DB if case_file
        if resolved.case_type == "case_file" and resolved.case_id is not None:
            self._persist(table_name, resolved.case_id, unique_nodes, unique_edges)

        return self._serialize(resolved, unique_nodes, unique_edges)

    # ------------------------------------------------------------------ #
    # Linked-record extraction
    # ------------------------------------------------------------------ #

    def _extract_evidence(self, case_id: int, case_type: str) -> tuple[list[KGNode], list[KGEdge]]:
        """Extract Evidence nodes linked to the case."""
        from app.models import Evidence

        kw = {"case_id": case_id} if case_type == "case_file" else {"adjudication_id": case_id}
        nodes: list[KGNode] = []
        edges: list[KGEdge] = []
        case_node_id = f"{case_type}:{case_id}"
        for ev in Evidence.query.filter_by(**kw).order_by(Evidence.uploaded_at.asc()).all():
            label = ev.caption or ev.filename or ev.evidence_type
            node_id = f"evidence:{ev.id}"
            nodes.append(
                KGNode(
                    id=node_id,
                    label=label[:80],
                    type=EVIDENCE,
                    source_table="evidence",
                    source_id=ev.id,
                    metadata={
                        "evidence_type": ev.evidence_type,
                        "verification_status": ev.verification_status,
                        "file_size": ev.file_size,
                        "uploaded_at": _iso(ev.uploaded_at),
                    },
                )
            )
            edges.append(KGEdge(source=case_node_id, target=node_id, type=SUPPORTED_BY, weight=0.9))
        return nodes, edges

    def _extract_annexures(self, case_id: int, case_type: str) -> tuple[list[KGNode], list[KGEdge]]:
        """Extract Annexure nodes linked to the case."""
        from app.models import Annexure

        kw = {"case_id": case_id} if case_type == "case_file" else {"adjudication_id": case_id}
        nodes: list[KGNode] = []
        edges: list[KGEdge] = []
        case_node_id = f"{case_type}:{case_id}"
        for annex in Annexure.query.filter_by(**kw).order_by(Annexure.uploaded_at.asc()).all():
            label = annex.caption or annex.filename
            node_id = f"annexure:{annex.id}"
            nodes.append(
                KGNode(
                    id=node_id,
                    label=label[:80],
                    type=ANCILLARY,
                    source_table="annexures",
                    source_id=annex.id,
                    metadata={
                        "page_count": annex.page_count,
                        "file_size": annex.file_size,
                        "mime_type": annex.mime_type,
                        "annexure_letter": annex.annexure_letter,
                    },
                )
            )
            edges.append(KGEdge(source=case_node_id, target=node_id, type=REFERENCES))
        return nodes, edges

    def _extract_bills(self, case_id: int, case_type: str) -> tuple[list[KGNode], list[KGEdge]]:
        """Extract Bill nodes linked via Sample on a case_file."""
        nodes: list[KGNode] = []
        edges: list[KGEdge] = []
        if case_type != "case_file":
            return nodes, edges

        case_node_id = f"{case_type}:{case_id}"

        from app.models import CaseFile

        cf = db.session.get(CaseFile, case_id)
        if cf and cf.sample_id:
            sample = db.session.get(_import_sample(), cf.sample_id)
            if sample is not None:
                for bill in getattr(sample, "bills", []):
                    node_id = f"bill:{bill.id}"
                    nodes.append(
                        KGNode(
                            id=node_id,
                            label=f"Bill {bill.id} ({bill.Name})",
                            type=ANCILLARY,
                            source_table="bills",
                            source_id=bill.id,
                            metadata={
                                "total_bill": _safe_float(getattr(bill, "Total_bill", None)),
                                "submission_date": _iso(bill.Submission_date),
                            },
                        )
                    )
                    edges.append(KGEdge(source=case_node_id, target=node_id, type=REFERENCES))
        return nodes, edges

    # ------------------------------------------------------------------ #
    # Persistence (case_file only)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _persist(
        source_table: str,
        source_id: int | None,
        nodes: list[KGNode],
        edges: list[KGEdge],
    ) -> None:
        """Persist nodes + edges to the entity/relationship tables (idempotent)."""
        from app.models import Entity, Relationship

        try:
            # Clear previous graph for this case (filter_by is parameterised)
            Entity.query.filter_by(source_table=source_table, source_id=source_id).delete(synchronize_session=False)
            db.session.flush()

            # Map node ids → new entity PKs
            id_map: dict[str, int] = {}
            for node in nodes:
                meta_json = json.dumps(node.metadata) if node.metadata else None
                entity = Entity(  # type: ignore[call-arg]
                    entity_type=node.type,
                    name=node.label,
                    source_table=node.source_table or source_table,
                    source_id=node.source_id or source_id,
                    metadata_json=meta_json,
                )
                db.session.add(entity)
                db.session.flush()
                id_map[node.id] = entity.id

            # Persist edges
            for edge in edges:
                src = id_map.get(edge.source)
                tgt = id_map.get(edge.target)
                if src is None or tgt is None:
                    continue
                db.session.add(
                    Relationship(  # type: ignore[call-arg]
                        source_id=src,
                        target_id=tgt,
                        relationship_type=edge.type,
                        weight=edge.weight,
                    )
                )

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("Knowledge graph persistence failed for %s:%s: %s", source_table, source_id, exc)

    # ------------------------------------------------------------------ #
    # Serialization (Cytoscape.js element format)
    # ------------------------------------------------------------------ #

    def _serialize(
        self,
        resolved: Any,
        nodes: list[KGNode],
        edges: list[KGEdge],
    ) -> dict:
        """Build the Cytoscape.js JSON payload."""
        cy_nodes = []
        for n in nodes:
            style = _NODE_STYLE.get(
                n.type,
                {"color": "#607d8b", "shape": "ellipse", "icon": "fa-solid fa-circle"},
            )
            cy_nodes.append({
                "data": {
                    "id": n.id,
                    "label": n.label,
                    "type": n.type,
                    "source_table": n.source_table,
                    "source_id": n.source_id,
                    "color": style["color"],
                    "shape": style["shape"],
                    "icon": style["icon"],
                    **(n.metadata or {}),
                }
            })

        cy_edges = []
        for e in edges:
            cy_edges.append({
                "data": {
                    "source": e.source,
                    "target": e.target,
                    "type": e.type,
                    "label": e.label or e.type,
                    "weight": e.weight,
                    "color": _EDGE_STYLE.get(e.type, "#607d8b"),
                }
            })

        return {
            "case_id": resolved.case_id,
            "adjudication_id": resolved.adjudication_id,
            "case_type": resolved.case_type,
            "case_number": resolved.case_number,
            "nodes": cy_nodes,
            "edges": cy_edges,
            "node_count": len(cy_nodes),
            "edge_count": len(cy_edges),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _safe_float(value) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _import_sample():
    """Lazy import to avoid circular dependencies."""
    from app.models import Sample

    return Sample


def _iso(dt) -> str | None:
    """Serialize a datetime to ISO format, or None."""
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _extract_sections(text: str) -> list[str]:
    """Extract section numbers from a comma-separated string."""
    if not text:
        return []
    results: list[str] = []
    for part in text.split(","):
        part = part.strip()
        if part.isdigit():
            results.append(part)
    return results


def _dedupe(items: list[str]) -> list[str]:
    """Deduplicate a list preserving order."""
    return list(dict.fromkeys(items))


def _dedupe_nodes(nodes: list[KGNode]) -> list[KGNode]:
    seen: set[str] = set()
    result: list[KGNode] = []
    for n in nodes:
        if n.id not in seen:
            seen.add(n.id)
            result.append(n)
    return result


def _dedupe_edges(edges: list[KGEdge]) -> list[KGEdge]:
    seen: set[tuple[str, str, str]] = set()
    result: list[KGEdge] = []
    for e in edges:
        key = (e.source, e.target, e.type)
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result

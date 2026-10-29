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

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_

from app.extensions import db

from app.knowledge_graph.errors import GraphBuildError

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
AMENDS = "AMENDS"
OVERRULES = "OVERRULES"
SUPPORTS = "SUPPORTS"
CITES = "CITES"
APPLIES_TO_JURISDICTION = "APPLIES_TO_JURISDICTION"

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
        resolved, nodes, edges = self._build_graph(case_id, case_type)
        if resolved is None or resolved.record is None:
            return {
                "case_id": case_id,
                "case_type": case_type,
                "case_number": "",
                "nodes": [],
                "edges": [],
                "error": "Case not found",
            }
        table_name = resolved.record.__tablename__
        source_id = resolved.case_id if resolved.case_type == "case_file" else resolved.adjudication_id
        if source_id is not None:
            self._persist(table_name, source_id, nodes, edges, graph_origin=resolved.case_type)
        return self._serialize(resolved, nodes, edges)

    def _build_graph(self, case_id: int, case_type: str = "case_file"):
        """Extract nodes + edges for a case without persisting (shared by build/upsert).

        Returns ``(resolved, nodes, edges)`` where ``resolved`` may be ``None``
        (and ``nodes``/``edges`` empty) when the case is not found.
        """
        from app.shared.case_resolver import CaseResolver

        resolved = CaseResolver().resolve(case_id, kind=case_type)
        if resolved is None or resolved.record is None:
            return resolved, [], []

        record: Any = resolved.record
        table_name = record.__tablename__
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

        sec_nodes: list[KGNode] = []
        section_ids_by_number: dict[str, str] = {}
        for sec_num in _dedupe(section_numbers):
            section_row = self._get_section_row(sec_num)
            act = section_row.act if (section_row is not None and section_row.act) else "FSS Act, 2006"
            meta: dict[str, Any] = {
                "section_number": sec_num,
                "act": act,
                "effective_date": _iso(section_row.effective_date) if section_row is not None else None,
                "expiry_date": _iso(section_row.expiry_date) if section_row is not None else None,
                "full_text": section_row.full_text if section_row is not None else None,
                "jurisdiction": section_row.jurisdiction if section_row is not None else None,
                "vector_id": None,  # populated lazily via attach_vector_id()
            }
            if section_row is not None:
                meta["source_table"] = "kg_sections"
                meta["source_id"] = section_row.id
            sec_node = KGNode(
                id=f"section:{case_node.id}:{sec_num}",
                label=f"Section {sec_num}",
                type=SECTION,
                source_table="kg_sections" if section_row is not None else None,
                source_id=section_row.id if section_row is not None else None,
                metadata=meta,
            )
            sec_nodes.append(sec_node)
            section_ids_by_number[sec_num] = sec_node.id
        nodes.extend(sec_nodes)

        # Case -> each section (violated / applied)
        for sec_node in sec_nodes:
            edges.append(KGEdge(source=case_node.id, target=sec_node.id, type=VIOLATED_SECTION))

        # --- Legal cross-reference edges (CITES / AMENDS / OVERRULES) ---
        edges.extend(self._section_reference_edges(record, case_node.id, sec_nodes, section_ids_by_number))
        # --- Jurisdiction edges (APPLIES_TO_JURISDICTION) ---
        edges.extend(self._jurisdiction_edges(sec_nodes, case_node.id))

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

        # Deduplicate by id (preserve order); the caller persists.
        unique_nodes = _dedupe_nodes(nodes)
        unique_edges = _dedupe_edges(edges)
        return resolved, unique_nodes, unique_edges

    def upsert_graph(self, case_id: int, case_type: str = "case_file") -> dict:
        """Incrementally rebuild the graph for ``case_id`` only when it changed.

        Change detection compares ``record.updated_at`` against the stored
        ``graph_last_modified`` of the persisted graph.  When nothing changed
        the method is a no-op.  Otherwise the graph is rebuilt and written
        with :meth:`_bulk_upsert` (bulk inserts, stale edges removed).

        Returns a status dict; raises :class:`GraphBuildError` when the case
        does not exist or the database fails.
        """
        from app.shared.case_resolver import CaseResolver

        resolved = CaseResolver().resolve(case_id, kind=case_type)
        if resolved is None or resolved.record is None:
            raise GraphBuildError(f"Case not found: {case_id} (kind={case_type})")

        record: Any = resolved.record
        table_name = record.__tablename__
        source_id = record.id
        graph_origin = resolved.case_type
        updated_at = getattr(record, "updated_at", None) or datetime.now(UTC)

        Entity, Relationship = self._models_for(graph_origin)
        last_modified = (
            db.session
            .query(db.func.max(Entity.graph_last_modified))
            .filter_by(source_table=table_name, source_id=source_id)
            .scalar()
        )
        if last_modified is not None and updated_at <= last_modified:
            return {
                "status": "unchanged",
                "case_id": case_id,
                "case_type": graph_origin,
                "graph_last_modified": _iso(last_modified),
            }

        _, nodes, edges = self._build_graph(case_id, case_type)
        try:
            self._bulk_upsert(
                table_name,
                source_id,
                graph_origin,
                nodes,
                edges,
                modified_at=updated_at,
            )
        except GraphBuildError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            db.session.rollback()
            logger.error("Knowledge graph upsert failed for %s:%s: %s", table_name, source_id, exc)
            raise GraphBuildError(f"Failed to upsert graph for {graph_origin}:{case_id}: {exc}") from exc

        return {
            "status": "upserted",
            "case_id": case_id,
            "case_type": graph_origin,
            "nodes": len(nodes),
            "edges": len(edges),
            "graph_last_modified": _iso(updated_at),
        }

    @staticmethod
    def _models_for(graph_origin: str):
        if graph_origin == "adjudication":
            from app.models import AdjudicationEntity as Entity, AdjudicationRelationship as Relationship
        else:
            from app.models import Entity, Relationship
        return Entity, Relationship

    def _bulk_upsert(
        self,
        source_table: str,
        source_id: int,
        graph_origin: str,
        nodes: list[KGNode],
        edges: list[KGEdge],
        modified_at: datetime | None = None,
    ) -> None:
        """Write ``nodes``/``edges`` with bulk ops, removing stale edges.

        Existing entities for ``source_table``/``source_id`` are replaced and
        relationships that referenced the old entity PKs (source or target no
        longer present) are deleted, then the new graph is bulk-inserted.
        """
        modified_at = modified_at or datetime.now(UTC)
        Entity, Relationship = self._models_for(graph_origin)

        try:
            old_entity_ids = [e.id for e in Entity.query.filter_by(source_id=source_id).all()]
            Entity.query.filter_by(source_id=source_id).delete(synchronize_session=False)
            db.session.flush()

            entity_objs = []
            id_map: dict[str, int] = {}
            for node in nodes:
                meta = dict(node.metadata or {})
                meta["graph_last_modified"] = _iso(modified_at)
                meta["kg_node_id"] = node.id
                entity = Entity(
                    entity_type=node.type,
                    name=node.label,
                    source_table=node.source_table or source_table,
                    source_id=node.source_id or source_id,
                    metadata_json=json.dumps(meta) if meta else None,
                    graph_last_modified=modified_at,
                )
                entity_objs.append(entity)
                id_map[node.id] = entity  # PK assigned after flush below

            db.session.bulk_save_objects(entity_objs)
            db.session.flush()
            for node, entity in zip(nodes, entity_objs):
                id_map[node.id] = entity.id

            if old_entity_ids:
                # Remove stale edges: source or target entity no longer exists.
                Relationship.query.filter(
                    or_(
                        Relationship.source_id.in_(old_entity_ids),
                        Relationship.target_id.in_(old_entity_ids),
                    )
                ).delete(synchronize_session=False)

            rel_objs = []
            for edge in edges:
                src = id_map.get(edge.source)
                tgt = id_map.get(edge.target)
                if src is None or tgt is None:
                    continue
                rel_objs.append(
                    Relationship(
                        source_id=src,
                        target_id=tgt,
                        relationship_type=edge.type,
                        weight=edge.weight,
                        graph_last_modified=modified_at,
                    )
                )
            db.session.bulk_save_objects(rel_objs)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

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
                ),
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
                ),
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
                        ),
                    )
                    edges.append(KGEdge(source=case_node_id, target=node_id, type=REFERENCES))
        return nodes, edges

    # ------------------------------------------------------------------ #
    # Persistence (case_file only)
    # ------------------------------------------------------------------ #

    def _persist(
        self,
        source_table: str,
        source_id: int | None,
        nodes: list[KGNode],
        edges: list[KGEdge],
        graph_origin: str = "case_file",
        modified_at: datetime | None = None,
    ) -> None:
        """Persist nodes + edges to entity/relationship tables (idempotent).

        ``graph_origin`` selects ``entity``/``relationship`` for ``case_file``
        or ``adjudication_entity``/``adjudication_relationship`` for
        ``adjudication`` graphs.  ``modified_at`` stamps ``graph_last_modified``
        on every row written so incremental upserts can detect stale graphs.
        """
        if graph_origin == "adjudication":
            from app.models import AdjudicationEntity as Entity, AdjudicationRelationship as Relationship
        else:
            from app.models import Entity, Relationship

        modified_at = modified_at or datetime.now(UTC)
        try:
            # Clear the previous graph for this case.  Every row written for a
            # graph carries ``source_id == source_id`` (the case/adjudication
            # primary key) -- including nodes whose ``source_table`` differs
            # (e.g. the inspector row, sourced from ``fso``) -- so we delete by
            # source_id and let the FK cascade remove the old edges.
            Entity.query.filter_by(source_id=source_id).delete(synchronize_session=False)
            db.session.flush()

            # Map node ids → new entity PKs
            id_map: dict[str, int] = {}
            for node in nodes:
                meta = dict(node.metadata or {})
                meta["graph_last_modified"] = _iso(modified_at)
                meta["kg_node_id"] = node.id
                meta_json = json.dumps(meta) if meta else None
                entity = Entity(
                    entity_type=node.type,
                    name=node.label,
                    source_table=node.source_table or source_table,
                    source_id=node.source_id or source_id,
                    metadata_json=meta_json,
                    graph_last_modified=modified_at,
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
                    Relationship(
                        source_id=src,
                        target_id=tgt,
                        relationship_type=edge.type,
                        weight=edge.weight,
                        graph_last_modified=modified_at,
                    ),
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
                },
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
                },
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

    def __init__(self) -> None:
        self._vector_links: dict[str, str] = {}
        self._kg_cache: Any = None  # Redis cache, set lazily by _cache()

    # ------------------------------------------------------------------ #
    # Vector linkage (lazy) + legal cross-reference helpers
    # ------------------------------------------------------------------ #

    def attach_vector_id(self, node_id: str, text: str) -> str | None:
        """Lazily compute and cache the vector-store id for a node.

        Returns ``None`` when the embedding model is unavailable (offline /
        CI).  Callers merge the returned id into node metadata on demand.
        """
        if node_id in self._vector_links:
            return self._vector_links[node_id]
        try:
            self._embed_text(text)
        except Exception as exc:  # pragma: no cover - offline fallback
            logger.debug("Vector linkage skipped for %s: %s", node_id, exc)
            return None
        vector_id = f"vec:{node_id}"
        self._vector_links[node_id] = vector_id
        return vector_id

    def _embed_text(self, text: str) -> list[float]:
        """Embed a single string with ``sentence-transformers/all-mpnet-base-v2``.

        The import is intentionally lazy so the engine imports cleanly without
        torch installed.
        """
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        model = SentenceTransformer("all-mpnet-base-v2")
        return model.encode([text])[0].tolist()

    def _safe_embed(self, text: str) -> list[float]:
        try:
            return self._embed_text(text)
        except Exception:  # pragma: no cover - offline fallback
            return _fallback_vector(text)

    def _get_section_row(self, section_number: str):
        """Fetch the ORM ``Section`` for enrichment (None when not seeded)."""
        from app.models import Section

        return db.session.query(Section).filter_by(section_number=section_number).order_by(Section.id.asc()).first()

    def _section_reference_edges(
        self,
        record: Any,
        case_node_id: str,
        sec_nodes: list[KGNode],
        section_ids_by_number: dict[str, str],
    ) -> list[KGEdge]:
        """CITES / AMENDS / OVERRULES edges between sections on this case.

        Target ids use the canonical ``section:{case_id}:<ref id>`` format.
        Explicit references come from ``record.section_references`` (a list of
        ``{"id": "<section number>", "type": "cites|amends|overrules"}`` or
        plain section-number strings); otherwise CITES edges are inferred from
        section numbers mentioned in ``record.problem``.
        """
        refs = getattr(record, "section_references", None)
        edges: list[KGEdge] = []
        if refs:
            for ref in refs:
                if isinstance(ref, dict):
                    ref_id = str(ref.get("id") or ref.get("section") or "").strip()
                    kind = str(ref.get("type", "cites")).strip().lower()
                else:
                    ref_id = str(ref).strip()
                    kind = "cites"
                if not ref_id or ref_id not in section_ids_by_number:
                    continue
                etype = {"amends": AMENDS, "overrules": OVERRULES}.get(kind, CITES)
                edges.append(KGEdge(source=case_node_id, target=f"section:{case_node_id}:{ref_id}", type=etype))
            return edges

        # Fallback: infer CITES from section numbers mentioned in the problem text.
        mentioned = set(_extract_sections(getattr(record, "problem", None) or ""))
        for sec_node in sec_nodes:
            sec_num = (sec_node.metadata or {}).get("section_number") if sec_node.metadata else None
            for mentioned_num in mentioned:
                if mentioned_num != sec_num and mentioned_num in section_ids_by_number:
                    edges.append(
                        KGEdge(
                            source=sec_node.id,
                            target=section_ids_by_number[mentioned_num],
                            type=CITES,
                        )
                    )
        return edges

    def _jurisdiction_edges(self, sec_nodes: list[KGNode], case_node_id: str) -> list[KGEdge]:
        """APPLIES_TO_JURISDICTION edges from sections to their jurisdiction."""
        edges: list[KGEdge] = []
        for sec_node in sec_nodes:
            jurisdiction = (sec_node.metadata or {}).get("jurisdiction") if sec_node.metadata else None
            if jurisdiction:
                edges.append(KGEdge(source=sec_node.id, target=case_node_id, type=APPLIES_TO_JURISDICTION))
        return edges

    # ------------------------------------------------------------------ #
    # Hybrid retrieval (vector nearest-neighbor + graph BFS)
    # ------------------------------------------------------------------ #

    def hybrid_search(
        self,
        query: str,
        max_hops: int = 2,
        k: int = 5,
        vector_store: Any | None = None,
    ) -> dict:
        """Hybrid retrieval: vector nearest-neighbor search + graph BFS.

        Embeds ``query``, finds the nearest persisted node embeddings (seed
        nodes with similarity scores) via ``vector_store`` (or the default
        in-memory store), then traverses the persisted graph up to
        ``max_hops`` with BFS and returns the subgraph as JSON plus scores.

        Raises :class:`GraphBuildError` on vector-store or DB failure.
        """
        max_hops = max(0, int(max_hops))
        k = max(1, int(k))
        query = (query or "").strip()
        if not query:
            return {
                "query": "",
                "max_hops": max_hops,
                "k": k,
                "seed_nodes": [],
                "nodes": [],
                "edges": [],
                "scores": {},
            }

        store = vector_store or self._default_vector_store()
        try:
            seeds = store.search(query, k)  # list[(node_id, score)]
        except GraphBuildError:
            raise
        except Exception as exc:
            raise GraphBuildError(f"Vector search failed: {exc}") from exc

        seed_ids = [node_id for node_id, _ in seeds]
        scores = {node_id: score for node_id, score in seeds}

        entities = self._all_entities()
        node_by_id = {row[0]: row for row in entities}
        pk_to_node = {row[-1]: row[0] for row in entities}

        adjacency: dict[str, list[tuple[str, str, float | None]]] = {}
        for src_pk, tgt_pk, rel_type, weight in self._all_relationships():
            src = pk_to_node.get(src_pk)
            tgt = pk_to_node.get(tgt_pk)
            if src is None or tgt is None:
                continue
            adjacency.setdefault(src, []).append((tgt, rel_type, weight))

        visited: set[str] = set(seed_ids)
        frontier: list[str] = list(seed_ids)
        bfs_edges: list[tuple[str, str, str, float | None]] = []
        for _ in range(max_hops):
            next_frontier: list[str] = []
            for src in frontier:
                for tgt, rel_type, weight in adjacency.get(src, []):
                    bfs_edges.append((src, tgt, rel_type, weight))
                    if tgt not in visited:
                        visited.add(tgt)
                        next_frontier.append(tgt)
            if not next_frontier:
                break
            frontier = next_frontier

        hy_nodes = []
        for node_id in sorted(visited):
            row = node_by_id.get(node_id)
            if row is None:
                continue
            hy_nodes.append({
                "id": node_id,
                "label": row[1],
                "type": row[2],
                "source_table": row[3],
                "source_id": row[4],
                "metadata": row[5] or {},
                "score": scores.get(node_id),
            })

        seen_edges: set[tuple[str, str, str]] = set()
        hy_edges = []
        for src, tgt, rel_type, weight in bfs_edges:
            if src not in node_by_id or tgt not in node_by_id:
                continue
            key = (src, tgt, rel_type)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            hy_edges.append({"source": src, "target": tgt, "type": rel_type, "weight": weight})

        return {
            "query": query,
            "max_hops": max_hops,
            "k": k,
            "seed_nodes": seed_ids,
            "nodes": hy_nodes,
            "edges": hy_edges,
            "scores": scores,
        }

    def _default_vector_store(self) -> Any:
        return _DefaultVectorStore(self)

    def _all_entities(self) -> list[tuple]:
        """All persisted entities (both origins) as (node_id, label, type,
        source_table, source_id, metadata, pk) tuples."""
        from app.models import AdjudicationEntity, Entity

        rows: list[tuple] = []
        for cls in (Entity, AdjudicationEntity):
            for row in db.session.query(cls).all():
                meta = _parse_json(row.metadata_json)
                node_id = meta.get("kg_node_id") or f"{row.entity_type}:{row.id}"
                rows.append((node_id, row.name, row.entity_type, row.source_table, row.source_id, meta, row.id))
        return rows

    def _all_relationships(self) -> list[tuple]:
        """All persisted relationships as (src_pk, tgt_pk, type, weight)."""
        from app.models import AdjudicationRelationship, Relationship

        rows: list[tuple] = []
        for cls in (Relationship, AdjudicationRelationship):
            for row in db.session.query(cls).all():
                rows.append((row.source_id, row.target_id, row.relationship_type, row.weight))
        return rows

    def cached_node(self, node_id: str) -> KGNode | None:
        """Fetch a node, consulting the Redis cache (kg:node:{id}) first."""
        cache = self._cache()
        if cache is not None:
            try:
                cached = cache.get(node_id)
                if cached is not None:
                    return KGNode(**cached)
            except Exception:  # pragma: no cover - cache is best effort
                logger.debug("KG cache get failed for %s", node_id, exc_info=True)
        for row in self._all_entities():
            if row[0] != node_id:
                continue
            _nid, label, ntype, source_table, source_id, meta, _pk = row
            node = KGNode(
                id=node_id,
                label=label,
                type=ntype,
                source_table=source_table,
                source_id=source_id,
                metadata=meta or {},
            )
            if cache is not None:
                try:
                    cache.set(node_id, node)
                except Exception:  # pragma: no cover - cache is best effort
                    logger.debug("KG cache set failed for %s", node_id, exc_info=True)
            return node
        return None

    def _cache(self):
        """Lazily connect to Redis (best effort; None when unavailable)."""
        if getattr(self, "_kg_cache_resolved", False):
            return self._kg_cache
        try:
            from flask import current_app

            from app.knowledge_graph.cache import KGCache

            self._kg_cache = KGCache(current_app.config.get("REDIS_URL"))
        except Exception:  # pragma: no cover - redis not configured/running
            logger.debug("KG Redis cache unavailable", exc_info=True)
            self._kg_cache = None
        self._kg_cache_resolved = True
        return self._kg_cache


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


def _parse_json(value) -> dict:
    """Parse a JSON blob defensively, returning ``{}`` on any failure."""
    if not value:
        return {}
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


class _DefaultVectorStore:
    """In-memory nearest-neighbor store over persisted KG node texts.

    Embeddings are computed lazily via the engine (sentence-transformers
    ``all-mpnet-base-v2`` when available, otherwise a deterministic
    hash-based fallback so offline/CI runs never depend on the model).
    """

    def __init__(self, engine: KnowledgeGraphEngine) -> None:
        self.engine = engine

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        entities = self.engine._all_entities()
        if not entities:
            return []

        query_vec = self.engine._safe_embed(query)
        scored = []
        for node_id, _label, _ntype, _source_table, _source_id, meta, _pk in entities:
            text = _node_text(_label, meta)
            doc_vec = self.engine._safe_embed(text)
            scored.append((node_id, _cosine(query_vec, doc_vec)))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]

    def _safe_embed(self, text: str) -> list[float]:
        try:
            return self.engine._embed_text(text)
        except Exception:  # pragma: no cover - offline fallback
            return _fallback_vector(text)


def _node_text(label: str | None, meta: dict | None) -> str:
    parts = [label or ""]
    if meta:
        for key in ("full_text", "section_number", "act", "jurisdiction", "name", "sample_name", "evidence_type"):
            val = meta.get(key)
            if val:
                parts.append(str(val))
    return " ".join(parts)


def _fallback_vector(text: str) -> list[float]:
    """Deterministic pseudo-embedding used when the model is unavailable."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [(byte / 255.0) * 2.0 - 1.0 for byte in digest[:64]]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def _dedupe_edges(edges: list[KGEdge]) -> list[KGEdge]:
    seen: set[tuple[str, str, str]] = set()
    result: list[KGEdge] = []
    for e in edges:
        key = (e.source, e.target, e.type)
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result

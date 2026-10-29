"""Schema adapter mapping engine :class:`KGNode`/:class:`KGEdge` objects to the
canonical schema defined in :file:`knowledge_graph.json`.

The canonical schema declares:

* entity types: ``document``, ``authority``, ``evidence``, ``section``,
  ``reference``
* relationship types: ``SUPPORTED_BY``, ``INSPECTED_BY``, ``SAMPLED_FROM``,
  ``TESTED_AT``, ``VIOLATED_SECTION``, ``REFERENCES``, ``CITES``, ``AMENDS``,
  ``OVERRULES``, ``APPLIES_TO_JURISDICTION``
* a node-type map from the engine's historical constants (``case``, ``fbo``,
  ``inspector``, ...) to the canonical keys.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("knowledge_graph.json")

_DEFAULT_SCHEMA: dict[str, Any] = {
    "entity_types": ["document", "authority", "evidence", "section", "reference"],
    "relationship_types": [
        "SUPPORTED_BY",
        "INSPECTED_BY",
        "SAMPLED_FROM",
        "TESTED_AT",
        "VIOLATED_SECTION",
        "REFERENCES",
        "CITES",
        "AMENDS",
        "OVERRULES",
        "APPLIES_TO_JURISDICTION",
    ],
    "node_type_map": {
        "case": "document",
        "fbo": "authority",
        "inspector": "authority",
        "sample": "evidence",
        "lab": "authority",
        "evidence": "evidence",
        "section": "section",
        "ancillary": "reference",
    },
}


def load_schema() -> dict[str, Any]:
    """Load the canonical schema, falling back to built-ins on any error."""
    try:
        with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
            schema = json.load(fh)
        schema.setdefault("entity_types", _DEFAULT_SCHEMA["entity_types"])
        schema.setdefault("relationship_types", _DEFAULT_SCHEMA["relationship_types"])
        schema.setdefault("node_type_map", _DEFAULT_SCHEMA["node_type_map"])
        return schema
    except (OSError, ValueError) as exc:
        logger.warning("Could not load %s (%s); using built-in schema", SCHEMA_PATH, exc)
        return dict(_DEFAULT_SCHEMA)


_SCHEMA = load_schema()

_RAW_ENTITY_TYPES = _SCHEMA.get("entity_types", [])
ENTITY_TYPES: set[str] = {(item.get("key") if isinstance(item, dict) else str(item)) for item in _RAW_ENTITY_TYPES}
RELATIONSHIP_TYPES: set[str] = set(_SCHEMA.get("relationship_types", []))
NODE_TYPE_MAP: dict[str, str] = _SCHEMA.get("node_type_map", {})


def to_canonical(node_type: str) -> str:
    """Map an engine node type to its canonical entity type."""
    return NODE_TYPE_MAP.get(node_type, node_type)


def to_canonical_edge(edge_type: str) -> str:
    """Return the canonical relationship label (engine edge labels are already
    canonical relationship-type strings)."""
    return edge_type


def canonical_entity_types() -> set[str]:
    return set(ENTITY_TYPES)


def canonical_relationship_types() -> set[str]:
    return set(RELATIONSHIP_TYPES)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def validate_node(node: Any) -> list[str]:
    """Return validation problems for a node (empty list means valid).

    A node must have an id and a type that maps (via :func:`to_canonical`)
    to an entity type declared in the schema.
    """
    errors: list[str] = []
    node_id = _attr(node, "id")
    node_type = _attr(node, "type")
    if not node_id:
        errors.append("node missing id")
    if not node_type:
        errors.append("node missing type")
    else:
        canonical = to_canonical(str(node_type))
        if canonical not in ENTITY_TYPES:
            errors.append(
                f"node {node_id!r}: type {node_type!r} maps to {canonical!r}, not in knowledge_graph.json entity types"
            )
    return errors


def validate_edge(edge: Any) -> list[str]:
    """Return validation problems for an edge (empty list means valid).

    An edge must have a source, a target, and a type declared in the schema.
    """
    errors: list[str] = []
    source = _attr(edge, "source")
    target = _attr(edge, "target")
    edge_type = _attr(edge, "type")
    if not source:
        errors.append("edge missing source")
    if not target:
        errors.append("edge missing target")
    if not edge_type:
        errors.append("edge missing type")
    elif edge_type not in RELATIONSHIP_TYPES:
        errors.append(
            f"edge {source!r} -> {target!r}: type {edge_type!r} not in knowledge_graph.json relationship types"
        )
    return errors


def validate_graph(nodes: list[Any], edges: list[Any]) -> list[str]:
    """Validate every node/edge plus id uniqueness across the graph."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for node in nodes:
        node_id = _attr(node, "id")
        for problem in validate_node(node):
            errors.append(f"node {node_id!r}: {problem}")
        if node_id:
            if node_id in seen_ids:
                errors.append(f"duplicate node id {node_id!r}")
            seen_ids.add(node_id)
    for edge in edges:
        source = _attr(edge, "source")
        target = _attr(edge, "target")
        for problem in validate_edge(edge):
            errors.append(f"edge {source!r} -> {target!r}: {problem}")
    return errors

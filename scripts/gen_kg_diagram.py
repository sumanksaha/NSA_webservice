"""Generate a Mermaid entity-relationship diagram for the knowledge graph.

Reads the canonical schema from ``app/knowledge_graph/knowledge_graph.json``
(and the engine's edge/edge constants) and writes a Mermaid diagram to
``docs/knowledge_graph/diagram.mmd``.

Run::

    python scripts/gen_kg_diagram.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "app" / "knowledge_graph" / "knowledge_graph.json"
OUT_PATH = ROOT / "docs" / "knowledge_graph" / "diagram.mmd"

#: Edge label -> short description for the diagram.
EDGE_DESCRIPTIONS = {
    "SUPPORTED_BY": "supports",
    "INSPECTED_BY": "inspected by",
    "SAMPLED_FROM": "sampled from",
    "TESTED_AT": "tested at",
    "VIOLATED_SECTION": "violates",
    "REFERENCES": "references",
    "CITES": "cites",
    "AMENDS": "amends",
    "OVERRULES": "overrules",
    "APPLIES_TO_JURISDICTION": "applies to jurisdiction",
}

#: Canonical entity type -> Mermaid style class.
ENTITY_STYLES = {
    "document": "caseStyle",
    "authority": "authorityStyle",
    "evidence": "evidenceStyle",
    "section": "sectionStyle",
    "reference": "referenceStyle",
}

#: Representative source for each entity type (documentation purposes).
EXAMPLE_SOURCE = {
    "document": "CaseFile / Adjudication",
    "authority": "FBO / Inspector / Lab",
    "evidence": "Sample / Evidence",
    "section": "kg_sections",
    "reference": "Bills / Annexures",
}


def load_schema() -> dict:
    with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def entity_class(entity_type: str) -> str:
    return ENTITY_STYLES.get(entity_type, "defaultStyle")


def build_diagram(schema: dict) -> str:
    lines: list[str] = []
    lines.append("erDiagram")
    lines.append("%%{init: {'theme': 'base'}}%%")
    lines.append("")

    # Entities
    for entity_type in schema.get("entity_types", []):
        key = entity_type.get("key") if isinstance(entity_type, dict) else entity_type
        desc = (
            entity_type.get("description", "")
            if isinstance(entity_type, dict)
            else ""
        )
        source = EXAMPLE_SOURCE.get(key, "")
        lines.append(f'    {key} "{key}" {{')
        lines.append(f'        string description "{desc}"')
        lines.append(f'        string source "{source}"')
        lines.append("    }")
        lines.append("")

    # Relationships (one relationship line per edge type, between the entity
    # types it most plausibly connects — documented as illustrative).
    edge_pairs = [
        ("SUPPORTED_BY", "document", "authority"),
        ("INSPECTED_BY", "document", "authority"),
        ("SAMPLED_FROM", "authority", "evidence"),
        ("TESTED_AT", "evidence", "authority"),
        ("VIOLATED_SECTION", "document", "section"),
        ("REFERENCES", "document", "reference"),
        ("CITES", "section", "section"),
        ("AMENDS", "section", "section"),
        ("OVERRULES", "section", "section"),
        ("APPLIES_TO_JURISDICTION", "section", "document"),
    ]
    for rel_type, src, tgt in edge_pairs:
        if rel_type not in schema.get("relationship_types", []):
            continue
        rel = EDGE_DESCRIPTIONS.get(rel_type, rel_type)
        lines.append(f"    {src} ||--o{{ {tgt} : \"{rel}\"")
        lines.append("")

    # Classes
    lines.append("")
    for key, style in ENTITY_STYLES.items():
        lines.append(f'    classDef {style} fill:#f9f,stroke:#333,stroke-width:2px;')

    return "\n".join(lines)


def main() -> None:
    schema = load_schema()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(build_diagram(schema) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

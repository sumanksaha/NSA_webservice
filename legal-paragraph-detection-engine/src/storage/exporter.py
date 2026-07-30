"""Paragraph export module

Provides functionality to export parsed legal paragraphs to various formats,
including JSON, with options for compact representation and metadata preservation.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class LegalParagraph:
    """Represents a complete legal paragraph with all metadata."""

    id: str
    paragraph_text: str
    section: str
    clause: str
    subclause: str
    subclause_roman: str
    paragraph_type: str
    citations: list[dict[str, str]]
    parent_id: Optional[str]
    children: list[str]
    hierarchy_depth: int
    word_count: int
    document_type: str
    document_metadata: dict[str, Any]
    extraction_timestamp: str
    confidence_scores: dict[str, float]


class ParagraphExporter:
    """Exports parsed legal paragraphs to various formats."""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def export_to_json(self, paragraphs: list[dict[str, Any]], filename: str = "output.json") -> str:
        """Export paragraphs to JSON format with complete metadata."""

        output_path = Path(self.output_dir) / f"{filename}.json"

        with open(str(output_path), "w", encoding="utf-8") as f:
            json.dump(paragraphs, f, indent=2, ensure_ascii=False)

        return str(output_path)

        return output_path

    def export_to_compact_json(self, paragraphs: list[dict[str, Any]], filename: str = "compact.json") -> str:
        """Export paragraphs in compact JSON format (optimized for RAG indexing)."""

        # Create compact representation focusing on RAG-relevant fields
        compact_data = []

        for para in paragraphs:
            compact_dict = {
                "id": para["paragraph_id"],
                "text": para["text"],
                "section": para["section"],
                "clause": para["clause"],
                "subclause": para["subclause"],
                "type": para["paragraph_type"],
                "citations": para["citations"],
                "depth": para["hierarchy_depth"],
                "doc_type": para["document_type"],
                "metadata": para.get("document_metadata", {}),
                "embed_key": f"{para['document_type']}|{para['section']}|{para['clause']}|{para['subclause']}",
            }
            compact_data.append(compact_dict)

        output_path = Path(self.output_dir) / f"{filename}.json"

        with open(str(output_path), "w", encoding="utf-8") as f:
            json.dump(compact_data, f, ensure_ascii=False)

        return str(output_path)

    def export_hierarchy_report(self, paragraphs: list[dict[str, Any]], filename: str = "hierarchy.json") -> str:
        """Export detailed hierarchy information for analysis."""

        hierarchy_data = {
            "summary": {
                "total_paragraphs": len(paragraphs),
                "document_types": list(set(p["document_type"] for p in paragraphs)),
                "paragraph_types": list(set(p["paragraph_type"] for p in paragraphs)),
            },
            "by_document_type": {},
            "by_hierarchy_level": defaultdict(list),
            "top_level_sections": [],
        }

        for para in paragraphs:
            # Group by document type
            doc_type = para["document_type"]
            if doc_type not in hierarchy_data["by_document_type"]:
                hierarchy_data["by_document_type"][doc_type] = []
            hierarchy_data["by_document_type"][doc_type].append({
                "id": para["paragraph_id"],
                "section": para["section"],
                "clause": para["clause"],
                "type": para["paragraph_type"],
                "word_count": para["word_count"],
            })

            # Group by hierarchy level
            level_key = str(para["hierarchy_depth"])
            hierarchy_data["by_hierarchy_level"][level_key].append({
                "id": para["paragraph_id"],
                "text": para["text"][:200] + "...",
            })

            # Track top-level sections
            if not para["section"] or para["hierarchy_depth"] <= 1:
                hierarchy_data["top_level_sections"].append({
                    "id": para["paragraph_id"],
                    "text": para["text"][:300] + "...",
                })

        output_path = Path(self.output_dir) / f"{filename}.json"

        with open(str(output_path), "w", encoding="utf-8") as f:
            json.dump(hierarchy_data, f, indent=2, ensure_ascii=False)

        return str(output_path)

    def export_with_metadata(
        self, paragraphs: list[dict[str, Any]], filename: str = "output_with_metadata.json"
    ) -> str:
        """Export paragraphs with complete processing metadata."""

        export_data = {
            "metadata": {
                "export_timestamp": datetime.now(UTC).isoformat(),
                "version": "1.0.0",
                "engine_version": "legal_paragraph_detection_engine",
                "total_paragraphs": len(paragraphs),
            },
            "processing_config": {
                "mode": "accurate",
                "confidence_threshold": 0.7,
                "preserve_citations": True,
                "normalize_text": True,
                "cache_size": 1000,
            },
            "paragraphs": paragraphs,
        }

        output_path = Path(self.output_dir) / f"{filename}.json"

        with open(str(output_path), "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        return str(output_path)


# End of exporter.py

"""Tests for the Phase 1 chunk audit (scripts/enrichment/audit_chunks.py).

Covers the pure audit metrics (duplicates, empties, missing fields,
heuristics), severity classification, markdown rendering, and the CLI wiring.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "enrichment"))

from audit_chunks import (
    _classify_severity,
    audit_points,
    main,
    render_markdown,
)

ROOT = Path(__file__).resolve().parents[1]


def _point(
    cid: str,
    *,
    text: str = "Some legal text.",
    document_id: str = "doc-1",
    uri: str = "file:///corpus/a.pdf",
    section: str | None = "32",
    content_hash: str = "h",
    citations: list | None = None,
    references: list | None = None,
    entities: list | None = None,
) -> dict:
    payload = {
        "chunk_id": cid,
        "document_id": document_id,
        "document_uri": uri,
        "chunk_text": text,
        "chunk_index": 0,
        "document_type": "regulation",
        "section_number": section,
        "content_hash": content_hash,
        "citations": citations or [],
        "references": references or [],
        "entities": entities or [],
    }
    return {"id": cid, "payload": payload}


def test_total_and_unique_ids() -> None:
    pts = [_point("a"), _point("b"), _point("c"), _point("c")]
    r = audit_points(pts)
    assert r["summary"]["total_chunks"] == 4
    assert r["summary"]["unique_chunk_ids"] == 3
    # duplicate_chunk_ids = redundant instances (total - unique) = 2 for "c" x2
    assert r["metrics"]["duplicate_chunk_ids"] == 2
    assert r["metrics"]["total_chunks"] == 4


def test_empty_and_missing_fields() -> None:
    pts = [
        _point("a", text="   ", section=None),
        _point("b", text="x" * 50, section=None),
        _point("c", text="y" * 4000),
    ]
    r = audit_points(pts)
    assert r["metrics"]["empty_chunks"] == 1
    assert r["metrics"]["unusually_short_chunks"] == 1
    assert r["metrics"]["unusually_long_chunks"] == 1
    assert r["metrics"]["missing_section_info"] == 2  # a (blank) + b
    # Page numbers are absent from the §5.1 payload schema entirely.
    assert r["metrics"]["missing_page_info"] == 3
    assert r["severity"]["missing_section_info"] == "HIGH"
    assert r["severity"]["missing_page_info"] == "HIGH"


def test_duplicate_content_detected() -> None:
    pts = [
        _point("a", text="Same content here."),
        _point("b", text="Same   content here."),  # whitespace-normalized same
        _point("c", text="Different content."),
    ]
    r = audit_points(pts)
    assert r["metrics"]["duplicate_content_groups"] == 1
    assert r["metrics"]["duplicate_content_chunks"] == 2


def test_citations_references_entities_flags() -> None:
    pts = [
        _point("a", citations=["Section 55"], references=["Section 56"], entities=["FSSAI"]),
        _point("b", text="Subject to section 12 and section 13 of the Act."),
    ]
    r = audit_points(pts)
    assert r["metrics"]["chunks_with_citations"] == 1
    assert r["metrics"]["chunks_with_references"] == 1
    assert r["metrics"]["chunks_with_entities"] == 1
    assert r["metrics"]["multi_provision_chunks"] == 1


def test_broken_payloads_and_missing_required_keys() -> None:
    pts = [
        {"id": "bad", "payload": "not-a-dict"},
        _point("ok"),
    ]
    r = audit_points(pts)
    assert r["metrics"]["broken_payloads"] == 1
    assert r["summary"]["total_chunks"] == 2


def test_report_is_json_serializable() -> None:
    pts = [_point("a"), _point("b", text="Short.")]
    r = audit_points(pts)
    json.dumps(r)  # must not raise
    assert "metrics" in r and "severity" in r and "distribution" in r


def test_severity_classification() -> None:
    assert _classify_severity("empty_chunks") == "CRITICAL"
    assert _classify_severity("duplicate_chunk_ids") == "CRITICAL"
    assert _classify_severity("missing_document_id") == "HIGH"
    assert _classify_severity("missing_section_info") == "HIGH"
    assert _classify_severity("chunks_with_citations") == "LOW"


def test_render_markdown_sections() -> None:
    r = audit_points([_point("a"), _point("b", text="Second.")])
    md = render_markdown(r, "backup:test.json")
    assert "# CHUNK AUDIT" in md
    assert "## Summary" in md
    assert "## Severity-classified findings" in md
    assert "PRESERVE_EXISTING_CHUNKS" in md


def test_qdrant_source_pagination(monkeypatch) -> None:
    """The live-Qdrant source must page through scroll offsets correctly."""

    class _FakeScrollClient:
        """Mimics qdrant-client scroll: (records, next_offset) per page."""

        def __init__(self, pages: list[list[tuple[str, dict]]]) -> None:
            self.pages = pages
            self.calls: list[dict] = []

        def scroll(self, **kwargs):
            self.calls.append(kwargs)
            idx = len(self.calls) - 1
            records = self.pages[idx] if idx < len(self.pages) else []
            nxt = f"off-{idx + 1}" if idx + 1 < len(self.pages) else None
            # Real qdrant records expose .id / .payload attributes.
            return [
                types.SimpleNamespace(id=cid, payload=payload) for cid, payload in records
            ], nxt

    pages = [
        [("p1", {"chunk_id": "p1"}), ("p2", {"chunk_id": "p2"})],
        [("p3", {"chunk_id": "p3"})],
    ]
    fake_client = _FakeScrollClient(pages)

    class _FakeStore:
        collection_name = "fssai_legal_768"

        @staticmethod
        def _require_client():
            return fake_client

    # iter_qdrant_points imports QdrantStore lazily from app.rag.qdrant_client;
    # patch the module attribute the lazy import resolves against.
    import app.rag.qdrant_client as qc

    monkeypatch.setattr(qc, "QdrantStore", _FakeStore)

    import audit_chunks as mod

    pts = list(mod.iter_qdrant_points(batch_size=2))
    assert [p["id"] for p in pts] == ["p1", "p2", "p3"]
    # First page has no offset; second page carries the previous offset.
    assert fake_client.calls[0].get("offset") is None
    assert fake_client.calls[1]["offset"] == "off-1"
    assert fake_client.calls[1]["limit"] == 2


def test_cli_writes_report(tmp_path: Path) -> None:
    # Build a tiny backup file, then run the CLI against it.
    points = [_point("a", text="Alpha."), _point("b", text="Beta.")]
    backup = tmp_path / "tiny_backup.json"
    backup.write_text(
        json.dumps({"collection": "x", "points": points}),
        encoding="utf-8",
    )
    out_json = tmp_path / "chunk_audit.json"
    out_md = tmp_path / "CHUNK_AUDIT.md"
    rc = main(
        [
            "--source",
            f"backup:{backup}",
            "--report-dir",
            str(tmp_path / "r"),
            "--doc-dir",
            str(tmp_path / "d"),
        ]
    )
    assert rc == 0
    report = json.loads((tmp_path / "r" / "chunk_audit.json").read_text(encoding="utf-8"))
    assert report["summary"]["total_chunks"] == 2
    assert (tmp_path / "d" / "CHUNK_AUDIT.md").exists()
    assert not out_json.exists() and not out_md.exists()  # we used report/doc dirs

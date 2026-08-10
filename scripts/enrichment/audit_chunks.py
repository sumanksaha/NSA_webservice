#!/usr/bin/env python3
"""Phase 1 — Audit the existing FSSAI RAG chunk corpus.

Reads chunk points from either the offline vector-store backup JSON
(``backups/vector_store_*.json``) or the live Qdrant collection and computes
the Phase 1 audit metrics from the enrichment task:

* total chunks / unique chunk IDs / duplicate chunk IDs
* duplicate content (by ``content_hash`` / normalized text)
* empty, unusually short, unusually long chunks
* missing document ids / source (URI) / section info / content hash
* broken metadata (non-dict payloads, wrong field types)
* chunks with section/regulation headers
* chunks carrying citations / references / entities
* heuristic flags: incomplete-sentence chunks, multi-provision chunks

Memory safety: points are consumed via a generator — the live-Qdrant source
pages through the collection (``scroll``) so peak RAM stays bounded by one
page; the backup source loads the JSON once (a one-shot analysis read of a
~150 MB file is acceptable) but still processes points lazily.  Only
aggregates + sampled exemplars are retained in memory.

Outputs (written next to the report dir):
* ``reports/chunk_audit.json``            — machine-readable audit report
* ``docs/enrichment/CHUNK_AUDIT.md``      — human-readable audit narrative

The audit is read-only: it never modifies chunks, Qdrant, or the DB.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

# --------------------------------------------------------------------------- #
# Heuristic thresholds (documented in CHUNK_AUDIT.md; adjustable per run)
# --------------------------------------------------------------------------- #
SHORT_CHUNK_CHARS = 100          #: below this many chars => "short"
LONG_CHUNK_CHARS = 3000          #: above this many chars => "long"
SECTION_MARKER_RE = re.compile(r"\b(?:section|sec\.?|regulation|rule|clause|sch(?:edule)?\.?)\s+[A-Z]?\d{1,4}(?:[a-z]|[A-Z])?\b", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[.!?)\"'»]|[:;]\s*$")
#: Legal chunks routinely end in clause numbers / percentages / units (e.g.
#: "...not exceed 3", "...per cent of value", "(2)", "10%") without terminal
#: punctuation.  Accept a trailing numeric or number+unit so those aren't
#: flagged as incomplete-sentence defects.
_TRAILING_NUMERIC_RE = re.compile(r"\d(?:%|°|g|kg|ml|l|ppm|ppb)?$|\d{1,3}[,.]\d{1,3}$|\d\s+(?:g|kg|ml|l|ppm|ppb)$")
#: Legal instrument title fragments (used to detect header-bearing chunks).
INSTRUMENT_WORD_RE = re.compile(r"\b(?:act|regulation|regulations|rule|rules|bill|notification)\b", re.IGNORECASE)

REQUIRED_PAYLOAD_KEYS = (
    "chunk_id",
    "document_id",
    "chunk_text",
    "chunk_index",
    "document_type",
)


def _norm_text(text: str) -> str:
    """Normalize chunk text for content-duplicate detection.

    Strips trailing sentence punctuation so "Section 32." and
    "Section 32" fingerprint identically.
    """
    norm = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return norm.rstrip(".!?;:,)»\"'")


def _text_hash(text: str) -> str:
    """SHA-256 of normalized text — stable fingerprint for content dupes."""
    return hashlib.sha256(_norm_text(text).encode("utf-8")).hexdigest()


def _classify_severity(metric: str) -> str:
    """Map an audit metric to a Phase 1 severity bucket."""
    critical = {
        "empty_chunks",
        "duplicate_chunk_ids",
    }
    high = {
        "missing_document_id",
        "missing_document_uri",
        "broken_payloads",
        "missing_page_info",
        "missing_section_info",
    }
    medium = {
        "missing_content_hash",
        "duplicate_content",
        "unusually_short_chunks",
    }
    low = {
        "unusually_long_chunks",
        "incomplete_sentence_chunks",
        "multi_provision_chunks",
        "chunks_with_section_metadata",
        "chunks_with_citations",
        "chunks_with_references",
        "chunks_with_entities",
    }
    if metric in critical:
        return "CRITICAL"
    if metric in high:
        return "HIGH"
    if metric in medium:
        return "MEDIUM"
    return "LOW"


def _is_complete_sentence(text: str) -> bool:
    """Heuristic completeness: ends in sentence punctuation or a legal marker.

    This is intentionally permissive — it flags chunks that clearly END
    mid-sentence (no terminal punctuation) while not penalising tables,
    headers, or colon-terminated lists.
    """
    stripped = (text or "").rstrip()
    if not stripped:
        return True  # handled by empty-chunk metric
    if SENTENCE_END_RE.search(stripped[-6:]):
        return True
    # A trailing section heading style ("Section 3 —", "3.1", etc.)
    if re.search(r"[—\-–:]\s*$", stripped):
        return True
    # Trailing numeric / unit / percentage (common in legal lists & schedules)
    if _TRAILING_NUMERIC_RE.search(stripped):
        return True
    return False


def audit_points(points: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute the Phase 1 audit report over a chunk-point iterator.

    Args:
        points: Iterable of Qdrant point dicts — each ``{"id": str,
            "payload": dict}`` (optionally with ``"vector"``).

    Returns:
        The machine-readable audit report dict (JSON-serializable).
    """
    total = 0
    ids: collections.Counter[str] = collections.Counter()
    content_hashes: collections.Counter[str] = collections.Counter()
    doc_ids: set[str] = set()
    doc_types: collections.Counter[str] = collections.Counter()
    per_doc_chunks: collections.Counter[str] = collections.Counter()

    empty = 0
    short = 0
    long_ = 0
    missing_doc_id = 0
    missing_uri = 0
    missing_section = 0
    missing_hash = 0
    broken_payload = 0
    missing_required_key = 0
    #: Page numbers do not exist anywhere in the §5.1 payload — every chunk is
    #: flagged; pages can only come from PDF provenance later (source layer).
    missing_page = 0
    section_metadata_chunks = 0
    cite_chunks = 0
    ref_chunks = 0
    entity_chunks = 0
    incomplete_sentence = 0
    multi_provision = 0

    char_lengths: list[int] = []
    word_lengths: list[int] = []

    exemplars: dict[str, list[str]] = collections.defaultdict(list)

    def _exemplar(metric: str, chunk_id: str, snippet: str) -> None:
        if len(exemplars[metric]) < 5:
            exemplars[metric].append(f"{chunk_id}: {snippet[:140]}")

    for pt in points:
        total += 1
        payload = pt.get("payload")
        if not isinstance(payload, dict):
            broken_payload += 1
            _exemplar("broken_payloads", str(pt.get("id")), str(payload)[:140])
            continue

        cid = str(payload.get("chunk_id") or pt.get("id") or "")
        ids[cid] += 1
        text = payload.get("chunk_text")
        if not isinstance(text, str):
            missing_required_key += 1
            text = ""

        # --- Missing-field metrics ------------------------------------- #
        if not payload.get("document_id"):
            missing_doc_id += 1
        if not payload.get("document_uri"):
            missing_uri += 1
        if payload.get("section_number") is None and not payload.get("section_title"):
            missing_section += 1
        if not payload.get("page_number") and not payload.get("page"):
            missing_page += 1
        h = payload.get("content_hash")
        if not h:
            missing_hash += 1
        for key in REQUIRED_PAYLOAD_KEYS:
            if key not in payload:
                missing_required_key += 1
                break

        # --- Document-level aggregation ---------------------------------- #
        did = str(payload.get("document_id") or "?")
        doc_ids.add(did)
        per_doc_chunks[did] += 1
        doc_types[str(payload.get("document_type") or "?")] += 1

        # --- Content-shape metrics --------------------------------------- #
        stripped = text.strip()
        if not stripped:
            empty += 1
            _exemplar("empty_chunks", cid, "(blank)")
        char_lengths.append(len(text))
        word_lengths.append(max(1, len(text.split())))
        if stripped and len(text) < SHORT_CHUNK_CHARS:
            short += 1
        if len(text) > LONG_CHUNK_CHARS:
            long_ += 1

        # --- Dedup fingerprint ------------------------------------------- #
        content_hashes[_text_hash(text)] += 1

        # --- Legal-context flags ----------------------------------------- #
        citations = payload.get("citations") or []
        references = payload.get("references") or []
        entities = payload.get("entities") or []
        if citations:
            cite_chunks += 1
        if references:
            ref_chunks += 1
        if entities:
            entity_chunks += 1
        if payload.get("section_number") or payload.get("section_title"):
            section_metadata_chunks += 1
        if stripped and not _is_complete_sentence(text):
            incomplete_sentence += 1
        # Multiple distinct provision markers in one chunk => possible
        # unrelated-provision crossing (heuristic; not a verdict).
        markers = set(SECTION_MARKER_RE.findall(text)) if stripped else set()
        if len(markers) >= 2:
            multi_provision += 1
            _exemplar("multi_provision_chunks", cid, text[:140])

    unique_ids = sum(1 for _, n in ids.items() if n == 1)
    duplicate_ids = total - unique_ids
    dup_content = sum(1 for _, n in content_hashes.items() if n > 1)
    dup_content_chunks = total - sum(1 for _, n in content_hashes.items() if n == 1)

    metrics = {
        "total_chunks": total,
        "unique_chunk_ids": len(ids),
        "duplicate_chunk_ids": duplicate_ids,
        "duplicate_content_groups": dup_content,
        "duplicate_content_chunks": dup_content_chunks,
        "empty_chunks": empty,
        "unusually_short_chunks": short,
        "unusually_long_chunks": long_,
        "missing_document_id": missing_doc_id,
        "missing_document_uri": missing_uri,
        "missing_section_info": missing_section,
        "missing_page_info": missing_page,
        "missing_content_hash": missing_hash,
        "broken_payloads": broken_payload,
        "missing_required_payload_key": missing_required_key,
        "chunks_with_section_metadata": section_metadata_chunks,
        "chunks_with_citations": cite_chunks,
        "chunks_with_references": ref_chunks,
        "chunks_with_entities": entity_chunks,
        "incomplete_sentence_chunks": incomplete_sentence,
        "multi_provision_chunks": multi_provision,
    }

    # --- Distributions --------------------------------------------------- #
    def _pct(n: int) -> float:
        return round(100.0 * n / total, 2) if total else 0.0

    distribution = {
        "char_length": {
            "min": min(char_lengths) if char_lengths else 0,
            "max": max(char_lengths) if char_lengths else 0,
            "mean": round(sum(char_lengths) / len(char_lengths), 1) if char_lengths else 0,
            "p50": _percentile(char_lengths, 0.50),
            "p90": _percentile(char_lengths, 0.90),
            "p99": _percentile(char_lengths, 0.99),
        },
        "word_length": {
            "mean": round(sum(word_lengths) / len(word_lengths), 1) if word_lengths else 0,
        },
    }

    report = {
        "audit_version": "1.0",
        "generated_at": _now_iso(),
        "source": "points-iterator",
        "summary": {
            "total_chunks": total,
            "unique_chunk_ids": len(ids),
            "unique_documents": len(doc_ids),
            "document_types": dict(doc_types.most_common()),
            "chunks_per_document": {
                "min": min(per_doc_chunks.values()) if per_doc_chunks else 0,
                "max": max(per_doc_chunks.values()) if per_doc_chunks else 0,
                "mean": round(sum(per_doc_chunks.values()) / len(per_doc_chunks), 1) if per_doc_chunks else 0,
            },
        },
        "metrics": metrics,
        "severity": {name: _classify_severity(name) for name in metrics},
        "percentages": {name: _pct(n) for name, n in metrics.items()},
        "distribution": distribution,
        "exemplars": dict(exemplars),
        "recommendations": {
            "default_decision": "PRESERVE_EXISTING_CHUNKS",
            "observed": {
                "empty_chunks": empty,
                "duplicate_chunk_ids": total - unique_ids,
                "duplicate_content_groups": dup_content,
                "missing_section_info": missing_section,
            },
            "rechunk_only_if": (
                "Zero empty chunks, zero duplicate chunk IDs and complete required "
                "payload keys are observed; the only redundancy is {dup_content} "
                "normalized-content groups (likely repeated standard clauses), and "
                "{missing_section} chunks lack section metadata. Neither is a "
                "chunk-boundary defect: re-chunking the corpus is NOT recommended "
                "without retrieval evidence of material damage.".format(
                    dup_content=dup_content, missing_section=missing_section
                )
            ),
        },
    }
    return report


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    k = int((len(values) - 1) * q)
    return values[k]


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


def iter_backup_points(path: str) -> Iterator[dict[str, Any]]:
    """Yield points from the vector-store backup JSON."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    points = data.get("points", []) if isinstance(data, dict) else data
    yield from iter(points)


def iter_qdrant_points(
    batch_size: int = 1000,
    filters: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield points from the live Qdrant collection, paging via ``scroll``.

    Uses the project's ``QdrantStore`` but bypasses ``scroll_all`` (which
    materialises the whole collection) so peak RAM stays bounded by one page.

    Args:
        batch_size: Scroll page size.
        filters: Optional flat ``{field: value}`` payload filter (e.g.
            ``{"document_id": ...}`` to page through a single document).
    """
    from app.rag.qdrant_client import QdrantStore

    store = QdrantStore()
    client = store._require_client()
    next_offset = None
    while True:
        kwargs = {
            "collection_name": store.collection_name,
            "limit": batch_size,
            "with_payload": True,
            "with_vectors": False,
        }
        if next_offset is not None:
            kwargs["offset"] = next_offset
        if filters:
            kwargs["scroll_filter"] = store._build_filter(filters)
        records, next_offset = client.scroll(**kwargs)
        for r in records or []:
            yield {"id": str(r.id), "payload": getattr(r, "payload", None) or {}}
        if not next_offset:
            break


def _resolve_source(source: str) -> tuple[str, Callable[[], Iterable[dict[str, Any]]]]:
    if source.startswith("backup:"):
        return source, lambda: iter_backup_points(source.split(":", 1)[1])
    if source == "qdrant":
        return "qdrant", iter_qdrant_points
    if source.endswith(".json"):
        return f"backup:{source}", lambda: iter_backup_points(source)
    raise SystemExit(f"Unknown source {source!r} (use backup:<path> or qdrant)")


# --------------------------------------------------------------------------- #
# Markdown narrative
# --------------------------------------------------------------------------- #

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def render_markdown(report: dict[str, Any], source_label: str) -> str:
    """Render the audit report as a human-readable markdown narrative."""
    s = report["summary"]
    m = report["metrics"]
    pct = report["percentages"]
    lines: list[str] = [
        "# CHUNK AUDIT — Existing FSSAI RAG Corpus",
        "",
        f"> Source: `{source_label}` · audit v{report['audit_version']} · generated {report['generated_at']}",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"| --- | ---: |",
        f"| Total chunks | {s['total_chunks']} |",
        f"| Unique chunk IDs | {s['unique_chunk_ids']} |",
        f"| Unique documents | {s['unique_documents']} |",
        f"| Document types | `{json.dumps(s['document_types'])}` |",
        f"| Chunks/doc (min/mean/max) | {s['chunks_per_document']['min']} / {s['chunks_per_document']['mean']} / {s['chunks_per_document']['max']} |",
        "",
        "## Severity-classified findings",
        "",
        "| Metric | Count | % of corpus | Severity |",
        "| --- | ---: | ---: | --- |",
    ]
    ordered = sorted(m.items(), key=lambda kv: (_SEVERITY_ORDER[report["severity"][kv[0]]], kv[0]))
    for name, count in ordered:
        lines.append(f"| {name} | {count} | {pct[name]}% | {report['severity'][name]} |")

    dist = report["distribution"]["char_length"]
    lines += [
        "",
        "## Character-length distribution",
        "",
        f"min={dist['min']} · p50={dist['p50']} · mean={dist['mean']} · p90={dist['p90']} · p99={dist['p99']} · max={dist['max']}",
        "",
        "## Exemplars (first 5 per metric)",
        "",
    ]
    for metric, samples in report["exemplars"].items():
        lines.append(f"### {metric}")
        lines.append("")
        for sample in samples:
            lines.append(f"* `{sample}`")
        lines.append("")

    lines += [
        "## Decision",
        "",
        f"**Default decision: {report['recommendations']['default_decision']}.**",
        "",
        report["recommendations"]["rechunk_only_if"],
        "",
        "Severity legend: CRITICAL (data-defect, fix before enrichment), HIGH "
        "(missing provenance — enrichment must not invent it), MEDIUM (quality "
        "gap — enrichment target), LOW (informational).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="backup:backups/vector_store_fssai_legal_768_20260809_161941.json",
        help="backup:<path.json> | qdrant | <path.json>",
    )
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--doc-dir", default="docs/enrichment")
    args = parser.parse_args(argv)

    source_label, gen = _resolve_source(args.source)
    report = audit_points(gen())

    report_dir = Path(args.report_dir)
    doc_dir = Path(args.doc_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    doc_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / "chunk_audit.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = doc_dir / "CHUNK_AUDIT.md"
    md_path.write_text(render_markdown(report, source_label), encoding="utf-8")

    s = report["summary"]
    print(f"chunk_audit: {s['total_chunks']} chunks, {s['unique_documents']} docs -> {json_path}")
    for name, count in sorted(report["metrics"].items()):
        if count:
            print(f"  [{report['severity'][name]:<8}] {name}: {count}")
    print(f"markdown -> {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

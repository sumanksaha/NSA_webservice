"""Corpus evaluation harness for the RAG pipeline (Agent A).

Loads every supported document under a corpus directory and reports the
pipeline stages WITHOUT external services (no Qdrant / sentence-transformers
required): text extraction, cleaning, document classification, chunking via
the real legal engine, metadata extraction, and chunk-quality grading.

Output is a JSON report (per-document stats + aggregate) plus a human-readable
settings recommendation derived from the observed characteristics:

- ``document_type`` / ``authority`` spread  -> whether DocumentClassifier works
- pages/chars/clean-ratio                      -> corpus size + cleaning benefit
- chunk count / sections / hierarchy           -> index size + legal structure
- quality grades (A-F)                         -> chunk health, enrichment need
- extraction failures                          -> OCR / loader concerns

Usage::

    python scripts/evaluate_corpus.py <corpus_dir> [--json]

Exit code 0 on success, 2 on usage/error.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a corpus of legal documents for RAG ingestion.",
    )
    parser.add_argument("corpus_dir", help="Directory of PDF/DOCX/TXT files.")
    parser.add_argument(
        "--json",
        dest="pretty_json",
        action="store_true",
        help="Pretty-print the JSON report.",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=0,
        help="Evaluate at most N documents (0 = all).",
    )
    return parser


def _load_text(path: Path) -> tuple[str, dict[str, Any]]:
    """Load a document; returns (text, doc_meta)."""
    from app.document_loader import DocumentLoaderFactory

    doc = DocumentLoaderFactory.load(str(path))
    meta = {
        "document_id": doc.document_id,
        "file_type": doc.file_type,
        "pages": doc.total_pages,
        "file_size_bytes": doc.metadata.file_size_bytes,
    }
    return doc.text, meta


def _clean(text: str) -> tuple[str, dict[str, Any]]:
    from app.document_cleaner.pipeline import DocumentCleaner

    cleaner = DocumentCleaner()  # aggressive preset
    result = cleaner.clean(text)
    cleaned = result.clean_text
    ratio = (len(cleaned) / len(text)) if text else 0.0
    stats = {
        "raw_chars": len(text),
        "clean_chars": len(cleaned),
        "clean_ratio": round(ratio, 3),
        "chars_removed": result.report.total_chars_removed,
        "items_removed": result.report.total_items_removed,
    }
    return cleaned, stats


def _classify(text: str) -> dict[str, Any]:
    from app.rag.document_classifier import DocumentClassifier

    classifier = DocumentClassifier()
    result = classifier.classify(text)
    return result.to_dict()


def _metadata(text: str) -> dict[str, Any]:
    from app.rag.metadata_adapter import MetadataAdapter

    adapter = MetadataAdapter()
    extraction = adapter.extract(text)
    return extraction.to_dict()


def _chunk(text: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    from app.rag.chunker import Chunker
    from app.services.legal_engine import get_legal_engine

    engine = get_legal_engine()()
    chunker = Chunker(engine=engine)
    chunks = chunker.chunk_text(text, {"document_id": meta.get("document_id", ""), "type": "act"})
    return [c.to_payload() for c in chunks]


def _grade(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    from app.rag.chunk_quality import ChunkQualityValidator

    validator = ChunkQualityValidator()
    verdicts = [validator.validate_chunk(c) for c in chunks]
    grades = [v.grade for v in verdicts]
    return {
        "checked": len(verdicts),
        "grades": {g: grades.count(g) for g in "ABCDEF"},
        "ok": sum(1 for v in verdicts if v.ok),
        "failed": sum(1 for v in verdicts if not v.ok),
        "issues": sorted({i.get("code") for v in verdicts for i in v.issues}),
    }


def evaluate_document(path: Path) -> dict[str, Any]:
    """Evaluate one document through the full offline pipeline."""
    start = time.monotonic()
    name = path.name
    try:
        text, meta = _load_text(path)
    except Exception as exc:
        return {"file": name, "status": "load_failed", "error": str(exc), "elapsed_s": round(time.monotonic() - start, 2)}

    try:
        cleaned, clean_stats = _clean(text)
        classification = _classify(cleaned)
        metadata = _metadata(cleaned)
        chunks = _chunk(cleaned, meta)
        quality = _grade(chunks)
        status = "ok"
    except Exception as exc:
        return {
            "file": name,
            "status": "pipeline_failed",
            "error": str(exc),
            "elapsed_s": round(time.monotonic() - start, 2),
            "extraction": {"pages": meta.get("pages"), "raw_chars": len(text)},
        }

    sections = sorted({c.get("section_number") for c in chunks if c.get("section_number")})
    return {
        "file": name,
        "status": status,
        "elapsed_s": round(time.monotonic() - start, 2),
        "extraction": {**meta, **clean_stats},
        "classification": classification,
        "metadata": {
            k: metadata.get(k)
            for k in ("document_type", "authority", "jurisdiction", "state", "effective_date", "is_current", "overall_confidence")
        },
        "chunks": {
            "count": len(chunks),
            "sections": sections[:12],
            "section_count": len(sections),
            "max_hierarchy": max((c.get("hierarchy_level") or 0) for c in chunks) if chunks else 0,
            "citations": sum(1 for c in chunks if c.get("citations")),
            "references": sum(1 for c in chunks if c.get("references")),
        },
        "quality": quality,
    }


def recommend_settings(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive RAG settings recommendations from the evaluation results."""
    docs = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]

    types: dict[str, int] = {}
    for r in docs:
        t = r["classification"].get("document_type") or "unknown"
        types[t] = types.get(t, 0) + 1
    total_chunks = sum(r["chunks"]["count"] for r in docs)
    total_chars = sum(r["extraction"].get("raw_chars", 0) for r in docs)
    avg_clean = (
        sum(r["extraction"].get("clean_ratio", 0.0) for r in docs) / len(docs) if docs else 0.0
    )
    quality_failed = sum(r["quality"]["failed"] for r in docs)
    quality_checked = sum(r["quality"]["checked"] for r in docs)

    recommendations: list[str] = []
    if failed:
        recommendations.append(
            f"Fix {len(failed)} extraction failures before full ingestion: {[r['file'] for r in failed]}"
        )
    if avg_clean < 0.7:
        recommendations.append(
            f"Cleaning removes only {avg_clean:.0%} of raw text — check OCR preset "
            "(DocumentCleaner 'aggressive' vs 'ocr') for scanned-page PDFs."
        )
    if types.get("unknown", 0) / max(len(docs), 1) > 0.3:
        recommendations.append(
            "DocumentClassifier leaves many documents 'unknown' — supply explicit "
            "'document_type' in the ingestion document dict, or enable RAG_FULL_ENRICHMENT "
            "so MetadataAdapter contributes type detection."
        )
    if quality_failed / max(quality_checked, 1) > 0.2:
        recommendations.append(
            f"Chunk quality: {quality_failed}/{quality_checked} chunks failed validation — "
            "review chunker output / enable RAG_FULL_ENRICHMENT for metadata enrichment."
        )
    if total_chunks:
        recommendations.append(
            f"Corpus will produce ~{total_chunks} Qdrant points — collection fssai_legal_768 "
            "with 768-dim vectors (RAG_VECTOR_SIZE=768, all-mpnet-base-v2) is the correct shape."
        )
    recommendations.append(
        "Install qdrant-client + sentence-transformers (torch) to enable embedding + Qdrant; "
        "set RAG_QDRANT_URL, then run scripts/ingest_corpus.py <corpus_dir>."
    )

    return {
        "documents_evaluated": len(docs),
        "documents_failed": len(failed),
        "document_type_spread": types,
        "total_chunks": total_chunks,
        "total_raw_chars": total_chars,
        "avg_clean_ratio": round(avg_clean, 3),
        "quality_failed": quality_failed,
        "quality_checked": quality_checked,
        "recommended_env": {
            "RAG_QDRANT_COLLECTION": "fssai_legal_768",
            "RAG_VECTOR_SIZE": "768",
            "RAG_EMBEDDING_MODEL": "sentence-transformers/all-mpnet-base-v2",
            "RAG_RERANKER_MODEL": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "RAG_FULL_ENRICHMENT": "true" if types.get("unknown", 0) / max(len(docs), 1) > 0.3 else "false",
        },
        "recommendations": recommendations,
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    args = build_parser().parse_args(argv)
    corpus_dir = Path(args.corpus_dir)
    if not corpus_dir.is_dir():
        return 2

    files = sorted(
        p for p in corpus_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".txt"}
    )
    if args.max_docs:
        files = files[: args.max_docs]
    if not files:
        return 2

    results = [evaluate_document(p) for p in files]
    summary = recommend_settings(results)

    {"corpus_dir": str(corpus_dir), "documents": results, "summary": summary}
    return 0 if summary["documents_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

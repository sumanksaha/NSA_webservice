"""CLI tool: manifest-driven multi-domain corpus ingestion (Phase 2, 2026-08-10).

Reads ``other domain/manifest.json`` (the metadata authority — extractors never
clobber it) and ingests every ``ingest != false`` document into its per-domain
Qdrant collection (map in ``app/rag/collections.py``).

Key behaviours:

- **Per-domain pipelines** — ``make_ingestion_pipeline(full_enrichment=True,
  collection=...)``: spaCy entity extraction (installed 2026-08-10), full §5.1
  enrichment, per-domain collection isolation.
- **Collection creation** — ``pipeline.indexer.ensure_collection()`` is called
  once per domain before the first upsert (the indexer does NOT auto-create;
  see ``docs/INGESTION_READINESS.md`` §4 — without this, upserts 404).
- **Devanagari strip pre-chunk** — locked decision 2026-08-10: Hindi gazette
  boilerplate is removed before chunking so the English-only embedding model
  (``all-mpnet-base-v2``) sees clean text. ``--no-strip`` disables it.
- **OCR passes** — the 2 scanned PDFs carry ``requires_ocr: true`` in the
  manifest. Pass 1: ``--skip-ocr`` (24 text docs, ~1.5–2.5 h). Pass 2: run
  without ``--skip-ocr`` (or with ``--only`` on the two files) — OCR at 300 DPI
  adds ~47 min.
- **Safe re-runs** — chunk ids are fresh per run, so a re-run without cleanup
  accumulates duplicate points. ``--reindex`` deletes each document's prior
  points **before** the fresh ingest (a failure after deletion leaves the
  document absent — re-run it). Without it, runs are append-only.
- **Duplicates** — a document whose content hash was already seen in this run
  counts as OK but contributes 0 chunks (``IngestedDocumentResult.ok``).
- **Per-domain JSON summaries** + a master summary under ``reports/``.

Usage::

    python scripts/ingest_multidomain.py                  # full corpus (all 26)
    python scripts/ingest_multidomain.py --dry-run        # pre-flight: chunks + strip stats, no writes
    python scripts/ingest_multidomain.py --domain env     # one domain only
    python scripts/ingest_multidomain.py --only ep_act_1986.pdf
    python scripts/ingest_multidomain.py --skip-ocr       # pass 1 (24 text docs)
    python scripts/ingest_multidomain.py --reindex        # replace existing points per doc
    python scripts/ingest_multidomain.py --pretty --out-dir reports

Exit codes: 0 all ingested (or all duplicates), 1 any document failed, 2 usage/error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.collections import collection_for_domain  # noqa: E402
from app.rag.ingestion import make_ingestion_pipeline  # noqa: E402

#: Devanagari (Hindi) script block — the strip target (English-only embedder).
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")

#: Lines that are at least this fraction Devanagari are treated as Hindi
#: gazette boilerplate and blanked out entirely.
_HINDI_LINE_RATIO = 0.5


def strip_devanagari(text: str) -> str:
    """Remove Devanagari content from legal text before chunking.

    Mostly-Hindi lines (gazette header boilerplate: reg numbers, ministry
    names) are blanked to ``""`` — keeping the line structure so the legal
    engine's paragraph boundaries are preserved — and Devanagari runs inside
    mixed lines are replaced with a single space. Doubled horizontal space is
    collapsed.

    Returns the cleaned text unchanged when it contains no Devanagari.
    """
    if not _DEVANAGARI.search(text):
        return text
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        # Non-whitespace length so space-padded Hindi lines aren't misjudged.
        dev_count = len(_DEVANAGARI.findall(line))
        if dev_count / max(len(stripped), 1) >= _HINDI_LINE_RATIO:
            out.append("")
        else:
            out.append(_DEVANAGARI.sub(" ", line))
    return re.sub(r"[ \t]{2,}", " ", "\n".join(out))


class DevanagariStrippingCleaner:
    """Pipeline ``cleaner`` wrapper: DocumentCleaner, then Devanagari strip.

    Matches the pipeline's cleaner contract: ``clean(text)`` returns an object
    exposing ``clean_text`` (the default cleaner) or a plain string — both are
    accepted by ``IngestionPipeline._clean_text``.
    """

    def __init__(self, inner: Any | None = None) -> None:
        self._inner = inner

    def clean(self, text: str) -> str:
        if self._inner is None:
            from app.document_cleaner.pipeline import DocumentCleaner

            self._inner = DocumentCleaner()
        cleaned = self._inner.clean(text)
        cleaned_text = cleaned.clean_text if hasattr(cleaned, "clean_text") else str(cleaned)
        return strip_devanagari(cleaned_text)


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and validate the multi-domain manifest."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("documents"), list):
        raise ValueError(f"manifest {path} has no 'documents' list")
    return data


def _app_context():
    """Minimal Flask app context carrying the RAG config for Qdrant/embedding.

    ``QdrantStore`` reads its URL/key exclusively from ``current_app.config``
    (no env fallback), so any out-of-app CLI must provide an app context. The
    minimal app here mirrors ``create_app``'s RAG config keys without wiring
    blueprints/DB/Talisman, keeping the script light.
    """
    from flask import Flask

    app = Flask(__name__)
    app.config["RAG_QDRANT_URL"] = os.environ.get("RAG_QDRANT_URL", "")
    app.config["RAG_QDRANT_API_KEY"] = os.environ.get("RAG_QDRANT_API_KEY", "")
    app.config["RAG_QDRANT_COLLECTION"] = os.environ.get("RAG_QDRANT_COLLECTION", "fssai_legal_768")
    app.config["RAG_VECTOR_SIZE"] = int(os.environ.get("RAG_VECTOR_SIZE", "768"))
    app.config["RAG_EMBEDDING_MODEL"] = os.environ.get(
        "RAG_EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"
    )
    app.config["RAG_FULL_ENRICHMENT"] = True
    app.config["RAG_ENABLE_SPARSE"] = os.environ.get("RAG_ENABLE_SPARSE", "true").lower() == "true"
    for key, val in os.environ.items():
        if key.startswith("RAG_QDRANT_COLLECTION_"):
            app.config[key] = val
    return app.app_context()


def _env_collection_config() -> dict[str, str]:
    """Map ``RAG_QDRANT_COLLECTION_<DOMAIN>`` env vars to the config shape
    ``collection_for_domain`` expects, so env overrides work outside Flask."""
    return {
        key: val
        for key, val in os.environ.items()
        if key.startswith("RAG_QDRANT_COLLECTION_")
    }


def select_documents(
    manifest: dict[str, Any],
    *,
    domain: str | None = None,
    only: str | None = None,
    skip_ocr: bool = False,
) -> list[dict[str, Any]]:
    """Return manifest rows to process (ingest != false, filtered by CLI args)."""
    rows: list[dict[str, Any]] = []
    for row in manifest["documents"]:
        if row.get("ingest") is False:
            continue
        if domain and row.get("domain") != domain:
            continue
        if only and only not in str(row.get("file", "")):
            continue
        if skip_ocr and row.get("requires_ocr"):
            continue
        rows.append(row)
    return rows


def _doc_meta(row: dict[str, Any]) -> dict[str, Any]:
    """Build the pipeline ``document`` dict from a manifest row.

    Maps the manifest's ``document_type`` onto the chunker's expected ``type``
    key (``Chunk.from_paragraph`` reads ``doc.get("type")``); all other §5.1
    keys (``title``, ``act_name``, dates, ``is_current``, …) already align.
    """
    meta = dict(row)
    meta["type"] = row.get("document_type")
    return meta


def _dry_run_doc(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Pre-flight a single doc: load -> clean(strip) -> chunk. No model, no Qdrant."""
    from app.document_loader import DocumentLoaderFactory

    doc = DocumentLoaderFactory.load(str(path))
    raw = getattr(doc, "text", "")
    if isinstance(raw, (list, tuple)):
        raw = "\n\n".join(str(p) for p in raw)
    raw = raw or ""
    result: dict[str, Any] = {
        "file": row.get("file"),
        "document_id": row.get("document_id"),
        "domain": row.get("domain"),
        "raw_chars": len(raw),
        "stripped_chars": 0,
        "chunks": 0,
        "ocr_needed": False,
    }
    if not raw.strip():
        result["ocr_needed"] = bool(row.get("requires_ocr"))
        result["note"] = "no selectable text — OCR pass 2" if row.get("requires_ocr") else "empty document"
        return result
    stripped = strip_devanagari(DevanagariStrippingCleaner().clean(raw))
    result["stripped_chars"] = len(stripped)
    if stripped.strip():
        from app.rag.chunker import Chunker

        result["chunks"] = len(Chunker().chunk_text(stripped, _doc_meta(row)))
    return result


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Manifest-driven multi-domain RAG corpus ingestion (Phase 2).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("other domain/manifest.json"),
        help="Path to the manifest JSON (default: other domain/manifest.json).",
    )
    parser.add_argument(
        "--domain",
        help="Restrict to one domain (env | commercial | animal | wb_state | criminal).",
    )
    parser.add_argument(
        "--only",
        help="Restrict to documents whose filename contains this substring.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pre-flight: load/clean(strip)/chunk only — no embedding, no Qdrant writes.",
    )
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="Skip requires_ocr documents (pass 1: 24 text docs).",
    )
    parser.add_argument(
        "--no-strip",
        action="store_true",
        help="Keep Devanagari text (disable the pre-chunk strip).",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Delete each document's prior points before re-ingesting (fresh replace).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("reports"),
        help="Directory for per-domain + master JSON summaries (default: reports/).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the stdout master summary (default compact).",
    )
    return parser


def ingest_manifest(args: argparse.Namespace) -> dict[str, Any]:
    """Run the manifest-driven ingestion; returns the master summary dict."""
    manifest_path = args.manifest.resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    base_dir = manifest_path.parent
    manifest = load_manifest(manifest_path)

    # QdrantStore reads config exclusively from current_app.config — the real
    # ingestion path therefore runs inside a minimal Flask app context.
    ctx = _app_context()
    ctx.push()
    try:
        return _ingest_manifest_inner(args, manifest_path, base_dir, manifest)
    finally:
        ctx.pop()


def _ingest_manifest_inner(
    args: argparse.Namespace,
    manifest_path: Path,
    base_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Run the manifest-driven ingestion (inside the Flask app context)."""
    rows = select_documents(
        manifest,
        domain=args.domain,
        only=args.only,
        skip_ocr=args.skip_ocr,
    )
    if not rows:
        raise ValueError("no documents selected (check --domain/--only/--skip-ocr)")

    started = datetime.now(timezone.utc).isoformat()
    by_domain: dict[str, dict[str, Any]] = {}
    domain_pipelines: dict[str, Any] = {}
    domain_ensured: set[str] = set()
    any_failed = False

    for idx, row in enumerate(rows, start=1):
        fname = str(row.get("file") or "")
        path = base_dir / fname
        domain = str(row.get("domain") or "")
        collection = collection_for_domain(domain, config=_env_collection_config())
        dsum = by_domain.setdefault(
            domain,
            {
                "domain": domain,
                "collection": collection,
                "docs_total": 0,
                "docs_ok": 0,
                "docs_failed": 0,
                "docs_skipped": 0,
                "chunks_indexed": 0,
                "points_upserted": 0,
                "latency_s": 0.0,
                "results": [],
            },
        )
        dsum["docs_total"] += 1

        if not path.is_file():
            dsum["docs_skipped"] += 1
            dsum["results"].append({"file": fname, "document_id": row.get("document_id"), "ok": False, "errors": ["file missing"], "skipped": True})
            any_failed = True
            print(f"[{idx:>2}/{len(rows)}] SKIP  {fname}  (file missing)", flush=True)
            continue

        t0 = time.monotonic()

        if args.dry_run:
            res = _dry_run_doc(path, row)
            ok = not res.get("note")
            if ok:
                dsum["docs_ok"] += 1
            else:
                dsum["docs_skipped"] += 1
                res["skipped"] = True
            dsum["results"].append(res)
            dsum["chunks_indexed"] += res.get("chunks", 0)
            print(
                f"[{idx:>2}/{len(rows)}] DRY   {fname:<72} domain={domain:<10} "
                f"chars {res.get('raw_chars', 0):>7} -> {res.get('stripped_chars', 0):>7} "
                f"chunks~{res.get('chunks', 0):>5}",
                flush=True,
            )
            continue

        pipeline = domain_pipelines.get(domain)
        if pipeline is None:
            pipeline = make_ingestion_pipeline(
                full_enrichment=True,
                collection=collection,
                cleaner=None if args.no_strip else DevanagariStrippingCleaner(),
            )
            domain_pipelines[domain] = pipeline
        if domain not in domain_ensured:
            pipeline.indexer.ensure_collection()
            domain_ensured.add(domain)
            print(f"  ensured collection {collection!r} (domain {domain})", flush=True)

        if args.reindex and row.get("document_id"):
            try:
                removed = pipeline.indexer.remove_document(str(row["document_id"]))
                print(f"  reindex: removed {removed} prior points for {row['document_id']}", flush=True)
            except Exception as exc:  # noqa: BLE001 - reindex is best-effort
                print(f"  reindex warning for {row['document_id']}: {exc}", file=sys.stderr, flush=True)

        try:
            ingested = pipeline.ingest_file(path, document=_doc_meta(row))
        except Exception as exc:  # noqa: BLE001 - one bad doc must not abort the corpus
            print(f"[{idx:>2}/{len(rows)}] FAIL  {fname}  {exc}", flush=True)
            dsum["docs_failed"] += 1
            dsum["results"].append(
                {"file": fname, "document_id": row.get("document_id"), "ok": False, "errors": [str(exc)]}
            )
            any_failed = True
            continue

        rd = ingested.to_dict()
        rd["file"] = fname
        rd["domain"] = domain
        rd["collection"] = collection
        dsum["results"].append(rd)
        dsum["latency_s"] += rd.get("latency_ms", 0) / 1000
        if rd.get("ok"):
            dsum["docs_ok"] += 1
            dsum["chunks_indexed"] += rd.get("chunk_count", 0)
            dsum["points_upserted"] += rd.get("points_upserted", 0)
        else:
            dsum["docs_failed"] += 1
            any_failed = True
        wall = time.monotonic() - t0
        status = "OK  " if rd.get("ok") else "FAIL"
        print(
            f"[{idx:>2}/{len(rows)}] {status} {fname:<72} domain={domain:<10} "
            f"chunks={rd.get('chunk_count', 0):>5} upserted={rd.get('points_upserted', 0):>5} "
            f"dups={rd.get('duplicate_chunks', 0):>4} {wall:5.1f}s",
            flush=True,
        )

    finished = datetime.now(timezone.utc).isoformat()
    master: dict[str, Any] = {
        "manifest": str(manifest_path),
        "mode": "dry-run" if args.dry_run else "ingest",
        "started": started,
        "finished": finished,
        "domains": by_domain,
        "total_docs": sum(d["docs_total"] for d in by_domain.values()),
        "total_ok": sum(d["docs_ok"] for d in by_domain.values()),
        "total_failed": sum(d["docs_failed"] for d in by_domain.values()),
        "total_skipped": sum(d["docs_skipped"] for d in by_domain.values()),
        "total_chunks": sum(d["chunks_indexed"] for d in by_domain.values()),
        "total_points_upserted": sum(d["points_upserted"] for d in by_domain.values()),
        "ok": not any_failed,
    }
    return master


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code (0/1/2)."""
    from dotenv import load_dotenv

    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        master = ingest_manifest(args)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI should never traceback
        print(f"error: ingestion failed: {exc}", file=sys.stderr)
        return 2

    out_dir = args.out_dir
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for domain, dsum in master["domains"].items():
            (out_dir / f"ingest_multidomain_{domain}.json").write_text(
                json.dumps(dsum, indent=2, default=str), encoding="utf-8"
            )
        (out_dir / "ingest_multidomain_summary.json").write_text(
            json.dumps(master, indent=2, default=str), encoding="utf-8"
        )
        print(f"summaries -> {out_dir}/", flush=True)
    except OSError as exc:
        print(f"warning: could not write summaries to {out_dir}: {exc}", file=sys.stderr)

    indent = 2 if args.pretty else None
    print(json.dumps(master, indent=indent, default=str))
    return 1 if master.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())

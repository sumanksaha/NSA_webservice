"""Backfill provision spans onto existing Qdrant payloads (ADR-0009, plan step 5).

Runs the provision extractor over already-indexed text, then stamps each chunk
with the provisions whose span overlaps it:

* ``provision_spans`` — ``[{provision_id, section, subsection}]`` for the chunk
* ``provision_confidence`` — max boundary confidence over overlapping records
* ``provision_modality`` — primary deontic modality over overlapping records

**Dry-run is the default and is mandatory** (plan §10): it loads the frozen
payload index, computes the planned field updates, validates every record
(gold-grammar round-trip + act-range membership), and writes a diff report to
``reports/`` — without touching Qdrant.  ``--live`` performs the idempotent
``set_payload`` writes, grouped by identical field-set in 500-point batches.
``chunk_id`` values and vectors are never modified.

Usage::

    python scripts/backfill_provision_extraction.py                     # dry-run, frozen cache
    python scripts/backfill_provision_extraction.py --live --collection fssai_legal_768

Exit codes: 0 ok, 2 missing payload index / Qdrant unavailable, 1 failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import _section_from_id
from evaluation.provision_extraction_eval import (
    DEFAULT_PAYLOAD_INDEX,
    DocumentChunks,
    group_payload_documents,
    load_payload_index,
)

logger = logging.getLogger("backfill.provision.extraction")

BATCH_SIZE = 500
MAX_SAMPLES = 25


# --------------------------------------------------------------------------- #
# Planning (pure)
# --------------------------------------------------------------------------- #


def _join_document(chunks: list[dict[str, Any]]) -> tuple[str, list[int], list[int]]:
    parts = [str(chunk.get("chunk_text") or "") for chunk in chunks]
    text = "\n".join(parts)
    starts: list[int] = []
    position = 0
    for part in parts:
        starts.append(position)
        position += len(part) + 1
    return text, starts, [len(part) for part in parts]


def validate_record(record: Any, act_name: str) -> list[str]:
    """Return validation problems for one emitted provision record ([] = ok)."""
    from app.rag.legal_sections import is_known_section_for_act

    problems: list[str] = []
    provision_id = getattr(record, "provision_id", "")
    if _section_from_id(provision_id) is None:
        problems.append(f"gold_grammar_roundtrip_failed:{provision_id}")
    if is_known_section_for_act(getattr(record, "section", None), act_name) is False:
        problems.append(f"section_out_of_act_range:{provision_id}")
    return problems


def plan_for_groups(groups: dict[str, DocumentChunks], disambiguator: Any | None = None) -> dict[str, Any]:
    """Compute planned per-chunk payload updates for every document (no writes)."""
    from app.rag.provision_extractor import isolate_chunks

    updates: dict[str, dict[str, Any]] = {}
    records_total = 0
    validation_errors: list[str] = []
    for document_id, group in groups.items():
        records = isolate_chunks(
            group.chunks,
            act_name=group.act_name,
            document_id=document_id,
            document_title=group.document_title,
            disambiguator=disambiguator,
        )
        records_total += len(records)
        if not records:
            continue
        _, starts, lengths = _join_document(group.chunks)
        per_chunk: dict[str, list[Any]] = {}
        for record in records:
            validation_errors.extend(validate_record(record, group.act_name))
            span = getattr(record, "char_span", None)
            if not span:
                continue
            span_start, span_end = span
            for index, start in enumerate(starts):
                end = start + lengths[index]
                if start < span_end and end > span_start:
                    chunk_id = str(group.chunks[index].get("chunk_id") or "")
                    if chunk_id:
                        per_chunk.setdefault(chunk_id, []).append(record)
        for chunk_id, chunk_records in per_chunk.items():
            modalities = Counter(str(getattr(record, "modality", "")) for record in chunk_records)
            updates[chunk_id] = {
                "provision_spans": [
                    {
                        "provision_id": getattr(record, "provision_id", ""),
                        "section": getattr(record, "section", ""),
                        "subsection": list(getattr(record, "subsection", []) or []),
                    }
                    for record in chunk_records
                ],
                "provision_ids": [getattr(record, "provision_id", "") for record in chunk_records],
                "provision_confidence": round(
                    max(float(getattr(record, "confidence", 0.0)) for record in chunk_records), 6
                ),
                "provision_modality": modalities.most_common(1)[0][0] if modalities else "",
            }
    return {
        "documents": len(groups),
        "records": records_total,
        "points_planned": len(updates),
        "validation_errors": validation_errors,
        "updates": updates,
    }


# --------------------------------------------------------------------------- #
# Live writes
# --------------------------------------------------------------------------- #


def apply_updates(store: Any, updates: dict[str, dict[str, Any]], collection: str) -> int:
    """Idempotent batched ``set_payload``; returns the number of points written."""
    client = store._require_client()
    by_fields: dict[tuple, dict[str, Any]] = {}
    for chunk_id, fields in updates.items():
        key = tuple(sorted((field, json.dumps(value, sort_keys=True)) for field, value in fields.items()))
        group = by_fields.setdefault(key, {"fields": fields, "ids": []})
        group["ids"].append(chunk_id)
    written = 0
    for group in by_fields.values():
        ids = group["ids"]
        for index in range(0, len(ids), BATCH_SIZE):
            client.set_payload(
                collection_name=collection, payload=group["fields"], points=ids[index : index + BATCH_SIZE]
            )
        written += len(ids)
    return written


def _open_collection(collection: str) -> Any:
    """Build a store with an explicit client.

    ``QdrantStore`` reads its URL from ``flask.current_app``, which is
    unavailable in a plain script run (it raises outside an app context and
    the store then reports "Qdrant unavailable").  The client is therefore
    constructed here from the project config, which reads the same
    ``RAG_QDRANT_URL`` / ``RAG_QDRANT_API_KEY`` environment settings.
    """
    from qdrant_client import QdrantClient

    from app.rag.qdrant_client import QdrantStore
    from app.shared.config import cfg

    url = cfg.qdrant_url
    if not url:
        raise RuntimeError("RAG_QDRANT_URL is not set")
    client = QdrantClient(url=url, api_key=cfg.qdrant_api_key or None, prefer_grpc=False, timeout=120)
    return QdrantStore(collection_name=collection, client=client)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill provision spans onto Qdrant payloads.")
    parser.add_argument("--payload-index", type=Path, default=DEFAULT_PAYLOAD_INDEX, help="payload_index.jsonl path.")
    parser.add_argument("--live", action="store_true", help="Perform writes (default: dry-run only).")
    parser.add_argument("--collection", type=str, default=None, help="Comma-separated collections for --live.")
    parser.add_argument("--limit-docs", type=int, default=None, help="Only consider the first N documents.")
    parser.add_argument("--mode", type=str, default="rules", choices=["rules", "hybrid"], help="Disambiguation mode.")
    parser.add_argument("--out-dir", type=Path, default=Path("reports"), help="Where to write the diff report.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the report.")
    return parser


def _summarize(report: dict[str, Any]) -> dict[str, Any]:
    updates = report.pop("updates", {})
    samples = [{"chunk_id": chunk_id, **fields} for chunk_id, fields in list(updates.items())[:MAX_SAMPLES]]
    report["by_modality"] = dict(Counter(fields["provision_modality"] for fields in updates.values()))
    report["samples"] = samples
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from app.rag.provision_extractor import Disambiguator

    disambiguator = Disambiguator(mode=args.mode)
    report: dict[str, Any] = {"mode": "dry-run" if not args.live else "live", "extractor_mode": args.mode}

    try:
        if args.live:
            collections = [c.strip() for c in (args.collection or "").split(",") if c.strip()]
            if not collections:
                logger.error("--live requires --collection")
                return 2
            written_total = 0
            per_collection: dict[str, Any] = {}
            for collection in collections:
                store = _open_collection(collection)
                groups = group_payload_documents(store.scroll_all(batch_size=BATCH_SIZE))
                if args.limit_docs:
                    groups = dict(list(groups.items())[: args.limit_docs])
                plan = plan_for_groups(groups, disambiguator)
                written = apply_updates(store, plan["updates"], collection)
                written_total += written
                per_collection[collection] = {
                    "documents": plan["documents"],
                    "records": plan["records"],
                    "points_written": written,
                    "validation_errors": plan["validation_errors"],
                }
            report["collections"] = per_collection
            report["points_written"] = written_total
        else:
            if not args.payload_index.exists():
                logger.error("payload index not found: %s", args.payload_index)
                return 2
            groups = group_payload_documents(load_payload_index(args.payload_index))
            if args.limit_docs:
                groups = dict(list(groups.items())[: args.limit_docs])
            report.update(plan_for_groups(groups, disambiguator))
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        logger.error("backfill failed: %s", exc)
        return 1

    report = _summarize(report) if not args.live else report
    args.out_dir.mkdir(parents=True, exist_ok=True)
    name = "provision_extraction_dryrun.json" if not args.live else "provision_extraction_live.json"
    args.out_dir.joinpath(name).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    if "points_planned" in report:
        logger.info(
            "dry-run: %d documents, %d records, %d points planned, %d validation errors",
            report["documents"],
            report["records"],
            report["points_planned"],
            len(report["validation_errors"]),
        )
    if args.pretty:
        print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
